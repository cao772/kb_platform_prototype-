"""Export the platform's public reading views to one portable HTML file.

Run from the repository with PYTHONPATH=. python3 scripts/export_offline_platform.py OUTPUT.html
"""
import argparse
import base64
import hashlib
import json
import re
import sqlite3
import struct
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
from app.workspace_accounts import ROLES
from app.workspace_catalog import EXTRA, FIELDS, TYPES, STATUSES
from app.workspace_data import workspace_snapshot

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
        row = conn.execute("SELECT text,chunk_index FROM chunks WHERE document_id=? AND id=?", match.groups()).fetchone()
        if not row:
            return None
        citations.append(dict(doc=int(match[1]), chunk=row["chunk_index"], text=row["text"]))
    return dict(question=r["question"], answer=r["answer"], citations=citations)


def shareable_vectors(conn, document_ids):
    """Copy existing vectors and source passages. Only vocabulary of shareable text leaves the host."""
    vocabulary = {}
    passages = []
    marks = ",".join("?" for _ in document_ids) or "NULL"
    data = conn.execute(f"""SELECT c.document_id,c.chunk_index,c.text,c.metadata,v.vector_json
        FROM chunks c JOIN chunk_vectors v ON v.chunk_id=c.id WHERE v.model_name='local-hashing-embedding-v1'
        AND v.dimensions=128 AND c.document_id IN ({marks}) ORDER BY c.document_id,c.chunk_index""", tuple(document_ids))
    for row in data:
        passage = row["text"]
        packed = struct.pack("<128f", *json.loads(row["vector_json"]))
        passages.append([row["document_id"],row["chunk_index"],json.loads(row["metadata"]).get("page_no"),
                         passage,base64.b64encode(packed).decode("ascii")])
        for token in re.findall(r"[a-z0-9_.+-]+|[\u4e00-\u9fff]+", passage.lower()):
            words = [token]
            if "\u4e00" <= token[0] <= "\u9fff":
                words += list(token) + [token[i:i+2] for i in range(len(token)-1)]
            for w in words:
                if not w or len(w)>100 or w in vocabulary: continue
                digest = hashlib.blake2b(w.encode(),digest_size=8).digest()
                bucket = 1 + int.from_bytes(digest[:4],"big") % 128
                vocabulary[w] = bucket if digest[4] % 2 == 0 else -bucket
    return {"passages":passages,"vocabulary":vocabulary}


def snapshot(conn):
    result = read_collection(conn)
    public_ids = {r["id"] for r in result["documents"]}
    supplied = supplied_material(conn)
    shared_ids = public_ids | {r["id"] for r in supplied if r.get("full_text")}
    records = rows(conn, """SELECT r.id,r.record_type,r.code,r.name,r.region_code,r.region_name,r.product_class,
        r.status,r.version,r.effective_from,r.effective_to,r.source_document_id,r.attributes,c.chunk_index source_chunk_index
        FROM compliance_records r LEFT JOIN chunks c ON c.id=r.source_chunk_id AND c.document_id=r.source_document_id
        WHERE r.review_status='approved' ORDER BY r.id""")
    for r in records:
        attributes = json.loads(r.pop("attributes") or "{}")
        for key in EXTRA:
            if key in {"body_zh","body_en","source_excerpt"} and r["source_document_id"] not in public_ids:
                continue
            value = attributes.get(key) or ""
            if key == "source_url":
                value = official_url(value)
            r[key] = value
        if r["source_document_id"] not in shared_ids:
            r["source_document_id"] = None
            r["source_chunk_index"] = None
    changes = rows(conn, """SELECT id,title,event_type,severity,status,region_code,product_class,
        source_document_id,created_at FROM knowledge_change_tasks ORDER BY id DESC""")
    for r in changes:
        if r["source_document_id"] not in public_ids:
            r["source_document_id"] = None
    workspace = workspace_snapshot(conn)
    workspace_index = shareable_vectors(conn, shared_ids)
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='ws_facts'").fetchone():
        workspace["facts"] = [dict(r) for r in conn.execute("""SELECT id,document_id,chunk_id,subject,predicate,object,excerpt,status
            FROM ws_facts WHERE status='approved'""") if r["document_id"] in shared_ids]
    hashes = {row["sha256"]:row["document_id"] for row in conn.execute(
        "SELECT sha256,document_id FROM source_extracted_items WHERE document_id IS NOT NULL AND item_type='attachment'")
        if row["document_id"] in shared_ids}
    # Only fingerprints of the supplied legislation may leave the workstation.
    for doc in conn.execute("SELECT id,filename,source_path FROM documents WHERE id IN (%s)" %
            (",".join("?" for _ in shared_ids) or "NULL"),tuple(shared_ids)):
        if doc["filename"] in SUPPLIED_LEGISLATION and Path(doc["source_path"]).is_file():
            with Path(doc["source_path"]).open("rb") as f:
                sig = hashlib.sha256()
                for part in iter(lambda: f.read(1024*256), b""):
                    sig.update(part)
                hashes[sig.hexdigest()] = doc["id"]
    workspace.update(fields=FIELDS,types=TYPES,statuses=STATUSES,document_hashes=hashes,
        roles={k:v[0] for k,v in ROLES.items()},role_permissions={k:sorted(v[1]) for k,v in ROLES.items()})
    result.update(workspace=workspace,
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
        supplied=supplied, search_index=workspace_index, saved_question=saved_question(conn, public_ids),
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
             "CLIENT": js, "DATA": data, "WORKSPACE_CSS": (STATIC/"workspace.css").read_text(),
             "EXCEL_JS": (STATIC/"workspace_excel.js").read_text(),
             "LOCAL_JS": (STATIC/"workspace_local.js").read_text(),
             "WORKSPACE_JS": (STATIC/"workspace.js").read_text()}
    html = re.sub(r"<!--("+"|".join(parts)+r")-->", lambda m: parts[m[1]], template)
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
