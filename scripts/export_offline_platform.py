"""Export the platform's public reading views to one portable HTML file.

Run from the repository with PYTHONPATH=. python3 scripts/export_offline_platform.py OUTPUT.html
"""
import argparse
import base64
import hashlib
import json
import re
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime
from pathlib import Path
from threading import RLock
from urllib.parse import parse_qsl, unquote, urlsplit

from app.map_service import RegulationMapService
from app.ontology import ontology_schema
from app.regions import REGION_META
from app.store import KnowledgeStore

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
STATIC = ROOT / "static"
# Raw files and full text are shareable only for this reviewed public collection.
PUBLIC_HOSTS = {
    "DE-LAW": {"www.gesetze-im-internet.de"},
    "AT-LAW": {"www.ris.bka.gv.at"},
    "US-DOE-EFFICIENCY": {"www.energy.gov", "www1.eere.energy.gov"},
}
SUPPLIED_LEGISLATION = {
    "欧盟法规电气安全Directive 2014-35-EU.pdf",
    "Permen ESDM No. 14 Tahun 2021-能效.pdf",
    "Permenperin No. 7 Tahun 2025-安全.pdf",
}


class SnapshotStore(KnowledgeStore):
    """Use the existing business projections without running database initialization."""

    def __init__(self, conn):
        self.conn = conn
        self.lock = RLock()


def rows(conn, sql, params=()):
    return [dict(row) for row in conn.execute(sql, params)]


def official_url(value):
    parts = urlsplit(value or "")
    private_param = re.compile(r"token|password|secret|signature|credential|api[-_]?key", re.I)
    if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username
            or parts.password or any(private_param.search(k) for k, _ in parse_qsl(parts.query))):
        return ""
    return value


def public_original(source, url):
    return urlsplit(url).hostname in PUBLIC_HOSTS.get(source, set())


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_collection(conn):
    sources = rows(conn, """SELECT source_key,region_code,source_name,source_type,authority,base_url,
        access_method,priority,harvestability,verification_status,last_verified_at,last_http_status
        FROM knowledge_sources ORDER BY region_code,source_key""")
    source_by_key = {r["source_key"]: r for r in sources}
    for r in sources:
        r["base_url"] = official_url(r["base_url"])
    items = rows(conn, """SELECT id,source_key,source_url,item_type,title,document_id,text_excerpt,
        fields_json,last_seen_at,sha256,raw_path FROM source_extracted_items ORDER BY last_seen_at DESC,id DESC""")
    public_docs = {}
    files = {}
    download_root = (DATA / "source_downloads").resolve()
    for item in items:
        key = item["source_key"]
        url = item["source_url"] = official_url(item["source_url"])
        raw_path = item.pop("raw_path")
        digest = item.pop("sha256")
        fields = json.loads(item.pop("fields_json") or "{}")
        item["fields"] = {k: fields[k] for k in ("date", "status", "code") if fields.get(k)}
        policy = source_by_key.get(key, {}).get("harvestability")
        if policy in {"metadata_only", "manual_authorized"} and not public_original(key, url):
            item["text_excerpt"] = ""
        if not public_original(key, url):
            item["document_id"] = None
            continue
        if item["document_id"]:
            public_docs[item["document_id"]] = (key, url, item["title"])
        if item["item_type"] != "attachment" or not raw_path:
            continue
        path = Path(raw_path).resolve()
        if not path.is_relative_to(download_root) or path.suffix.lower() not in {".pdf", ".zip"}:
            continue
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f"Attachment hash mismatch, item {item['id']}")
        files[item["id"]] = {
            "name": Path(unquote(urlsplit(url).path)).name, "size": len(data),
            "mime": "application/pdf" if path.suffix.lower() == ".pdf" else "application/zip",
            "data": base64.b64encode(data).decode("ascii"),
        }
    snapshots = rows(conn, """SELECT s.source_key,s.document_id,p.entry_url,s.last_seen_at
        FROM source_snapshots s JOIN collection_profiles p ON s.profile_id=p.id""")
    for snap in snapshots:
        url = official_url(snap["entry_url"])
        if snap["document_id"] and public_original(snap["source_key"], url):
            public_docs.setdefault(snap["document_id"], (snap["source_key"], url, "入口页 · " + urlsplit(url).path))
    documents = []
    candidates = []
    for doc_id, (source, url, label) in sorted(public_docs.items()):
        record = conn.execute("""SELECT id,title,parser,full_text,created_at FROM documents WHERE id=?""",
                              (doc_id,)).fetchone()
        if not record:
            raise ValueError(f"Missing parsed document {doc_id}")
        doc = dict(record, source_key=source, url=url)
        if re.match(r"^\d{8}T\d{6}Z_", doc["title"]):
            doc["title"] = label
        documents.append(doc)
        candidates.extend(rows(conn, """SELECT id,document_id,candidate_name,candidate_code,candidate_type,status
            FROM extraction_tasks WHERE document_id=? ORDER BY id""", (doc_id,)))
    return dict(sources=sources, items=items, documents=documents, files=files, candidates=candidates)


def access_archive():
    path = DATA / "live_validation_stage37/access_queries.json"
    output = []
    for run in load_json(path) if path.exists() else []:
        r = run["response"]
        cls = r["product_classification"]
        mapping = cls.get("market_mapping") or {}
        output.append(dict(family=cls["family_id"], region=r["region_code"], date=r["as_of"],
                           status=r["status"], path=cls["classification_path"], summary=r["summary"],
                           category=mapping.get("market_category"), sources=mapping.get("source_keys", []),
                           dimensions=mapping.get("regulatory_dimensions", []),
                           leads=mapping.get("research_leads", []), next_action=r["next_action"]))
    return output


def supplied_material(conn):
    inventory = []
    for r in conn.execute("""SELECT id,filename,parser,created_at,metadata FROM documents
        WHERE json_extract(metadata,'$.dataset_role')='regulatory_evidence' ORDER BY id"""):
        meta = json.loads(r["metadata"])
        doc = dict(id=r["id"], title=r["filename"], parser=r["parser"], pages=meta.get("page_count"),
                   sheets=meta.get("sheet_count"), created_at=r["created_at"])
        if r["filename"] in SUPPLIED_LEGISLATION:
            doc["full_text"] = conn.execute("SELECT full_text FROM documents WHERE id=?", (r["id"],)).fetchone()[0]
        inventory.append(doc)
    return inventory


def saved_question(conn, documents):
    path = DATA / "live_validation_stage37/grounded_qa.json"
    if not path.exists():
        return None
    r = load_json(path)["response"]
    citations = []
    for ref in r["citations"]:
        match = re.fullmatch(r"doc-(\d+)-chunk-(\d+)", ref["evidence_id"])
        if not match or int(match[1]) not in documents:
            return None
        row = conn.execute("SELECT text FROM chunks WHERE document_id=? AND id=?", match.groups()).fetchone()
        if not row:
            return None
        citations.append(dict(doc=int(match[1]), chunk=int(match[2]), text=row[0]))
    return dict(question=r["question"], answer=r["answer"], citations=citations)


def snapshot(conn):
    result = read_collection(conn)
    public_ids = {r["id"] for r in result["documents"]}
    records = rows(conn, """SELECT id,record_type,code,name,region_code,region_name,product_class,status,version,
        effective_from,effective_to,source_document_id FROM compliance_records WHERE review_status='approved' ORDER BY id""")
    for r in records:
        if r["source_document_id"] not in public_ids:
            r["source_document_id"] = None
    changes = rows(conn, """SELECT id,title,event_type,severity,status,region_code,product_class,
        source_document_id,created_at FROM knowledge_change_tasks ORDER BY id DESC""")
    for r in changes:
        if r["source_document_id"] not in public_ids:
            r["source_document_id"] = None
    result.update(
        created=datetime.now().astimezone().isoformat(timespec="seconds"),
        regions=REGION_META, map=RegulationMapService(SnapshotStore(conn)).overview(),
        catalog=records, changes=changes, ontology=ontology_schema(),
        terms=rows(conn, "SELECT term_type,canonical_name,code,status FROM ontology_terms ORDER BY id"),
        relations=rows(conn, """SELECT source_record_id,relation_type,target_record_id FROM graph_relations
            WHERE status='approved' AND source_record_id IN (SELECT id FROM compliance_records WHERE review_status='approved')
            AND target_record_id IN (SELECT id FROM compliance_records WHERE review_status='approved')"""),
        runs=rows(conn, """SELECT source_key,status,started_at,pages_fetched,items_discovered,attachments_discovered,
            documents_ingested FROM source_deep_runs ORDER BY id DESC"""),
        counts=dict(documents=conn.execute("SELECT count(*) FROM documents").fetchone()[0],
                    pending=conn.execute("SELECT count(*) FROM extraction_tasks WHERE status='pending'").fetchone()[0]),
        products=load_json(DATA / "product_taxonomy.json")["product_families"], access=access_archive(),
        supplied=supplied_material(conn), saved_question=saved_question(conn, public_ids),
    )
    rounds = [(int(m.group(1)), p) for p in DATA.glob("first50_round*_status.json")
              if (m := re.fullmatch(r"first50_round(\d+)_status.json", p.name))]
    number, latest = max(rounds)
    result["first50"] = dict(round=number, **load_json(latest))
    return result


def export(output):
    if output.suffix.lower() != ".html":
        raise ValueError("Output must end in .html")
    with closing(sqlite3.connect((DATA / "knowledge.db").as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        payload = snapshot(conn)
    native = (STATIC / "index.html").read_text()
    map_page = (STATIC / "map.html").read_text()
    css = native.split("<style>", 1)[1].split("</style>", 1)[0]
    world = map_page.split('<svg id="worldSvg"', 1)[1].split('<g id="markers">', 1)[0]
    js = (STATIC / "offline_platform.js").read_text()
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    for char in "<>&\u2028\u2029":
        data = data.replace(char, "\\u%04x" % ord(char))
    template = (STATIC / "offline_platform.html").read_text()
    parts = {"NATIVE_CSS": css, "WORLD": '<svg id="worldSvg"' + world + '<g id="markers"></g></svg>',
             "CLIENT": js, "DATA": data}
    html = re.sub(r"<!--(NATIVE_CSS|WORLD|CLIENT|DATA)-->", lambda m: parts[m[1]], template)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, mode="w", encoding="utf-8", delete=False) as tmp:
        tmp.write(html)
    Path(tmp.name).replace(output)
    print(f"{output} · {output.stat().st_size:,} bytes · {len(payload['items'])} collected items · "
          f"{len(payload['documents'])} public documents · {len(payload['files'])} attachments")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    export(parser.parse_args().output.expanduser().resolve())
