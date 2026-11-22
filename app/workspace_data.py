from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.product_taxonomy import ProductTaxonomyService
from app.regions import REGION_META
from app.store import evidence_kind
from app.workspace_catalog import clean_text, dump

ROOT = Path(__file__).resolve().parents[1]
REQUIREMENTS = json.loads((ROOT / "data/platform_requirements.json").read_text())
SCHEMA = """
CREATE TABLE IF NOT EXISTS ws_nodes (
 key TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, parent TEXT NOT NULL DEFAULT '', members TEXT NOT NULL DEFAULT '[]');
CREATE TABLE IF NOT EXISTS ws_mapping (
 id INTEGER PRIMARY KEY, category TEXT NOT NULL, record_id INTEGER NOT NULL, region TEXT NOT NULL DEFAULT '',
 mode TEXT NOT NULL DEFAULT '', mark TEXT NOT NULL DEFAULT '', scope TEXT NOT NULL DEFAULT '',
 UNIQUE(category,record_id,region));
CREATE TABLE IF NOT EXISTS ws_scores (
 id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL, reference TEXT NOT NULL, model TEXT NOT NULL, output TEXT NOT NULL,
 correct INTEGER NOT NULL, total INTEGER NOT NULL, actor INTEGER NOT NULL, UNIQUE(document_id,reference,model));
CREATE TABLE IF NOT EXISTS ws_feedback (
 id INTEGER PRIMARY KEY, actor INTEGER NOT NULL, turn_id INTEGER, message TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'open', resolution TEXT NOT NULL DEFAULT '', record_id INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS ws_leads (
 id INTEGER PRIMARY KEY, actor INTEGER NOT NULL, contact TEXT NOT NULL, intent TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS ws_facts (
 id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL, chunk_id INTEGER NOT NULL, subject TEXT NOT NULL,
 predicate TEXT NOT NULL, object TEXT NOT NULL, excerpt TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 UNIQUE(document_id,chunk_id,subject,predicate,object));
CREATE TABLE IF NOT EXISTS ws_sync (
 id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL DEFAULT 0, minutes INTEGER NOT NULL DEFAULT 60,
 next_at INTEGER NOT NULL DEFAULT 0, actor INTEGER, result TEXT DEFAULT '');
INSERT OR IGNORE INTO ws_sync(id) VALUES(1);
CREATE TABLE IF NOT EXISTS ws_watches (
 source_key TEXT PRIMARY KEY, channel TEXT NOT NULL, keywords TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
 minutes INTEGER NOT NULL, next_at INTEGER NOT NULL DEFAULT 0, actor INTEGER, result TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS ws_settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
"""


def default_nodes():
    nodes = {}

    def add(key, kind, name, parent="", members=()):
        nodes[key] = dict(key=key, kind=kind, name=name, parent=parent, members=list(members))

    for code, meta in REGION_META.items():
        if not meta.get("target_market"):
            continue
        area = meta["area"]
        add("area:" + area, "region", area)
        add(code, "region", meta["name"], "area:" + area)
    add(
        "EU",
        "alliance",
        "欧盟",
        members=[k for k, v in REGION_META.items() if "EU" in v.get("shared_scopes", [])],
    )
    add("ASEAN", "alliance", "东盟", members=["SG"])
    for family in ProductTaxonomyService().catalog()["product_families"]:
        parent = ""
        for name in family["parent_path"]:
            key = parent + "/" + name
            add(key, "category", name, parent)
            parent = key
        add(family["id"], "category", family["name_zh"], parent)
        for child in family["children"]:
            add(child["id"], "category", child["name_zh"], family["id"])
    return list(nodes.values())


def initialize(store):
    with store.lock, store.conn:
        store.conn.executescript(SCHEMA)
        if not store.conn.execute("SELECT 1 FROM ws_settings WHERE key='initial_taxonomy'").fetchone():
            for n in default_nodes():
                store.conn.execute(
                    "INSERT OR IGNORE INTO ws_nodes VALUES(?,?,?,?,?)",
                    (n["key"], n["kind"], n["name"], n["parent"], dump(n["members"])),
                )
            store.conn.execute("INSERT INTO ws_settings VALUES('initial_taxonomy','1')")


def table(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args)]


def taxonomy(conn):
    nodes = table(conn, "SELECT * FROM ws_nodes ORDER BY rowid")
    for n in nodes:
        n["members"] = json.loads(n["members"])
    return nodes


def save_node(conn, p):
    key, name, kind, parent = (clean_text(p.get(k), 180) for k in ("key", "name", "kind", "parent"))
    if not key or not name or kind not in {"category", "region", "alliance", "mode", "mark"}:
        raise ValueError("请输入名称、唯一编码和有效类型。")
    nodes = {n["key"]: n for n in taxonomy(conn)}
    if key in nodes and nodes[key]["kind"] != kind:
        raise ValueError("已存在的编码属于另一种类型。")
    if p.get("delete"):
        if (
            any(n["parent"] == key or key in n["members"] for n in nodes.values())
            or conn.execute(
                "SELECT 1 FROM ws_mapping WHERE ? IN (category,region,mode,mark)", (key,)
            ).fetchone()
        ):
            raise ValueError("该项仍有子项、成员或适用映射。")
        conn.execute("DELETE FROM ws_nodes WHERE key=?", (key,))
        return
    cursor = parent
    seen = {key}
    while cursor:
        if cursor in seen or cursor not in nodes or nodes[cursor]["kind"] != kind:
            raise ValueError("父级无效或存在循环。")
        seen.add(cursor)
        cursor = nodes[cursor]["parent"]
    members = list(dict.fromkeys(p.get("members") or []))
    if members and (kind != "alliance" or any(nodes.get(k, {}).get("kind") != "region" for k in members)):
        raise ValueError("联盟成员须选自地区库。")
    conn.execute(
        """INSERT INTO ws_nodes VALUES(?,?,?,?,?) ON CONFLICT(key) DO UPDATE
        SET name=excluded.name,parent=excluded.parent,members=excluded.members""",
        (key, kind, name, parent, dump(members)),
    )


def save_mapping(conn, p):
    if p.get("delete"):
        conn.execute("DELETE FROM ws_mapping WHERE id=?", (int(p["id"]),))
        return
    record_id = int(p.get("record_id") or 0)
    if not conn.execute(
        "SELECT 1 FROM compliance_records WHERE id=? AND review_status='approved'", (record_id,)
    ).fetchone():
        raise ValueError("请选择已审核并启用的法规或知识。")
    for field, kinds in [
        ("category", {"category"}),
        ("region", {"region", "alliance"}),
        ("mode", {"mode"}),
        ("mark", {"mark"}),
    ]:
        value = p.get(field) or ""
        if not value and field != "category":
            continue
        found = conn.execute("SELECT kind FROM ws_nodes WHERE key=?", (value,)).fetchone()
        if not found or found[0] not in kinds:
            raise ValueError("分类、市场、模式或标志选择无效。")
    conn.execute(
        """INSERT INTO ws_mapping(category,record_id,region,mode,mark,scope) VALUES(?,?,?,?,?,?)
        ON CONFLICT(category,record_id,region) DO UPDATE SET mode=excluded.mode,mark=excluded.mark,scope=excluded.scope""",
        (
            p["category"],
            record_id,
            p.get("region") or "",
            p.get("mode") or "",
            p.get("mark") or "",
            clean_text(p.get("scope")),
        ),
    )


def mappings(conn):
    return table(
        conn,
        """SELECT m.*,c.name record_name,n.name category_name,rg.name region_name,md.name mode_name,mk.name mark_name
        FROM ws_mapping m JOIN compliance_records c ON c.id=m.record_id AND c.review_status='approved'
        JOIN ws_nodes n ON n.key=m.category LEFT JOIN ws_nodes rg ON rg.key=m.region
        LEFT JOIN ws_nodes md ON md.key=m.mode LEFT JOIN ws_nodes mk ON mk.key=m.mark ORDER BY m.id""",
    )


def applicable_mappings(conn, family, child, region, product=""):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='ws_mapping'").fetchone():
        return []
    nodes = {n["key"]: n for n in taxonomy(conn)}
    category = {k for k in (family, child, product) if k in nodes}
    category.update(k for k, n in nodes.items() if n["kind"] == "category" and n["name"] == product)
    for k in tuple(category):
        parent = nodes[k]["parent"]
        while parent and parent not in category:
            category.add(parent)
            parent = nodes[parent]["parent"]
    scopes = {
        "",
        region,
        *(n["key"] for n in nodes.values() if n["kind"] == "alliance" and region in n["members"]),
    }
    return [m for m in mappings(conn) if m["category"] in category and m["region"] in scopes]


def score(conn, p, actor):
    doc_id = int(p.get("document_id") or 0)
    if not conn.execute("SELECT id FROM documents WHERE id=?", (doc_id,)).fetchone():
        raise ValueError("请指定原始文档。")
    expected = p.get("reference")
    actual = p.get("output")
    model = clean_text(p.get("model"), 160)
    if (
        not model
        or not isinstance(expected, dict)
        or not expected
        or not isinstance(actual, dict)
        or len(expected) > 300
    ):
        raise ValueError("请输入模型名、标准答案和模型解析字段对象。")

    def norm(x):
        return " ".join(str(x).split()) if isinstance(x, str) else dump(x)

    correct = sum(k in actual and norm(actual[k]) == norm(v) for k, v in expected.items())
    conn.execute(
        """INSERT INTO ws_scores(document_id,reference,model,output,correct,total,actor) VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(document_id,reference,model) DO UPDATE SET output=excluded.output,correct=excluded.correct,
        total=excluded.total,actor=excluded.actor""",
        (doc_id, dump(expected), model, dump(actual), correct, len(expected), actor["id"]),
    )


def score_rows(conn):
    result = table(
        conn,
        """SELECT s.id,s.document_id,d.title,s.model,s.correct,s.total,s.reference
        FROM ws_scores s JOIN documents d ON d.id=s.document_id ORDER BY s.document_id,s.reference,s.correct DESC,s.model""",
    )
    for row in result:
        row["benchmark"] = hashlib.sha256(row.pop("reference").encode()).hexdigest()[:12]
    return result


def workspace_snapshot(conn):
    initialized = conn.execute("SELECT 1 FROM sqlite_master WHERE name='ws_settings'").fetchone()
    quality = {"located": 0, "excerpt": 0, "background": 0, "missing": 0, "without_region": 0, "items": []}
    for row in table(
        conn,
        """SELECT r.id,r.name,r.attributes,r.region_code,r.source_document_id,r.source_chunk_id,
        c.chunk_index source_chunk_index,json_extract(d.metadata,'$.dataset_role') source_role
        FROM compliance_records r LEFT JOIN documents d ON d.id=r.source_document_id
        LEFT JOIN chunks c ON c.id=r.source_chunk_id AND c.document_id=r.source_document_id
        WHERE r.review_status='approved' ORDER BY r.id""",
    ):
        kind = evidence_kind(row)
        quality[kind] += 1
        quality["without_region"] += int(not row["region_code"])
        quality["items"].append(
            {"id": row["id"], "name": row["name"], "kind": kind, "region": row["region_code"]}
        )
    return {
        "quality": quality,
        "requirements": REQUIREMENTS,
        "nodes": taxonomy(conn) if initialized else default_nodes(),
        "mapping": mappings(conn) if initialized else [],
        "vectors": table(
            conn,
            """SELECT model_name,dimensions,count(*) chunks,count(distinct c.document_id) documents
                FROM chunk_vectors v JOIN chunks c ON c.id=v.chunk_id GROUP BY model_name,dimensions""",
        ),
    }
