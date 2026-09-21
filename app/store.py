from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from app.text_processing import cosine_score, embed_text, tokenize, vector_cosine

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS documents (
 id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
 knowledge_type TEXT NOT NULL, mime_type TEXT DEFAULT '', parser TEXT DEFAULT '',
 ingestion_status TEXT DEFAULT 'indexed', tags TEXT NOT NULL, source_path TEXT NOT NULL,
 full_text TEXT NOT NULL, metadata TEXT DEFAULT '{}', created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS chunks (
 id INTEGER PRIMARY KEY AUTOINCREMENT, document_id INTEGER NOT NULL, chunk_index INTEGER NOT NULL,
 text TEXT NOT NULL, metadata TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
 text, title UNINDEXED, filename UNINDEXED, tags UNINDEXED, chunk_id UNINDEXED, tokenize='unicode61'
);
CREATE TABLE IF NOT EXISTS chunk_vectors (
 chunk_id INTEGER PRIMARY KEY, model_name TEXT NOT NULL, dimensions INTEGER NOT NULL, vector_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ingestion_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, filename TEXT NOT NULL, source_path TEXT NOT NULL,
 status TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS extraction_tasks (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 document_id INTEGER NOT NULL,
 chunk_id INTEGER NOT NULL,
 candidate_type TEXT NOT NULL,
 candidate_name TEXT NOT NULL,
 candidate_code TEXT DEFAULT '',
 region_code TEXT DEFAULT '',
 region_name TEXT DEFAULT '',
 product_class TEXT DEFAULT '',
 confidence REAL DEFAULT 0,
 candidate_payload TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending',
 reviewer_note TEXT DEFAULT '',
 created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 reviewed_at TEXT DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_extraction_candidate_unique
 ON extraction_tasks(document_id,chunk_id,candidate_type,candidate_name);
CREATE TABLE IF NOT EXISTS compliance_records (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 record_type TEXT NOT NULL,
 code TEXT DEFAULT '',
 name TEXT NOT NULL,
 region_code TEXT DEFAULT '',
 region_name TEXT DEFAULT '',
 product_class TEXT DEFAULT '',
 status TEXT DEFAULT 'unknown',
 version TEXT DEFAULT '',
 effective_from TEXT DEFAULT '',
 effective_to TEXT DEFAULT '',
 source_document_id INTEGER,
 source_chunk_id INTEGER,
 review_status TEXT NOT NULL DEFAULT 'approved',
 attributes TEXT DEFAULT '{}',
 created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_compliance_record_unique
 ON compliance_records(record_type,code,name,region_code,product_class,version);
"""


class KnowledgeStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def reset(self) -> None:
        with self.lock:
            self.conn.executescript(
                "DELETE FROM compliance_records; DELETE FROM extraction_tasks; DELETE FROM chunk_fts; "
                "DELETE FROM chunk_vectors; DELETE FROM chunks; DELETE FROM documents; DELETE FROM ingestion_events;"
            )
            self.conn.commit()

    def upsert_document(self, *, filename: str, title: str, knowledge_type: str, tags: list[str], source_path: str,
                        full_text: str, mime_type: str = "", parser: str = "", metadata: dict[str, Any] | None = None,
                        chunks: list[dict[str, Any]]) -> int:
        with self.lock:
            old = self.conn.execute("SELECT id FROM documents WHERE filename=?", (filename,)).fetchone()
            if old:
                ids = [row["id"] for row in self.conn.execute("SELECT id FROM chunks WHERE document_id=?", (old["id"],))]
                for chunk_id in ids:
                    self.conn.execute("DELETE FROM chunk_fts WHERE chunk_id=?", (chunk_id,))
                    self.conn.execute("DELETE FROM chunk_vectors WHERE chunk_id=?", (chunk_id,))
                self.conn.execute("DELETE FROM extraction_tasks WHERE document_id=?", (old["id"],))
                self.conn.execute("DELETE FROM compliance_records WHERE source_document_id=?", (old["id"],))
                self.conn.execute("DELETE FROM chunks WHERE document_id=?", (old["id"],))
                self.conn.execute("DELETE FROM documents WHERE id=?", (old["id"],))
            cur = self.conn.execute(
                "INSERT INTO documents(filename,title,knowledge_type,mime_type,parser,tags,source_path,full_text,metadata) VALUES(?,?,?,?,?,?,?,?,?)",
                (filename, title, knowledge_type, mime_type, parser, json.dumps(tags, ensure_ascii=False), source_path,
                 full_text, json.dumps(metadata or {}, ensure_ascii=False)),
            )
            document_id = int(cur.lastrowid)
            for chunk in chunks:
                ccur = self.conn.execute("INSERT INTO chunks(document_id,chunk_index,text,metadata) VALUES(?,?,?,?)",
                    (document_id, chunk["chunk_index"], chunk["text"], json.dumps(chunk.get("metadata", {}), ensure_ascii=False)))
                chunk_id = int(ccur.lastrowid)
                self.conn.execute("INSERT INTO chunk_fts(text,title,filename,tags,chunk_id) VALUES(?,?,?,?,?)",
                    (chunk["text"], title, filename, " ".join(tags), chunk_id))
                vector = embed_text(chunk["text"])
                self.conn.execute("INSERT INTO chunk_vectors(chunk_id,model_name,dimensions,vector_json) VALUES(?,?,?,?)",
                    (chunk_id, "local-hashing-embedding-v1", len(vector), json.dumps(vector)))
            self.conn.execute("INSERT INTO ingestion_events(filename,source_path,status,message) VALUES(?,?,?,?)",
                (filename, source_path, "success", f"Indexed {len(chunks)} chunks"))
            self.conn.commit()
            return document_id

    def record_ingestion_failure(self, *, filename: str, source_path: str, message: str) -> None:
        with self.lock:
            self.conn.execute("INSERT INTO ingestion_events(filename,source_path,status,message) VALUES(?,?,?,?)",
                (filename, source_path, "failed", message))
            self.conn.commit()

    def stats(self) -> dict[str, Any]:
        with self.lock:
            documents = self.conn.execute("SELECT COUNT(*) count FROM documents").fetchone()["count"]
            chunks = self.conn.execute("SELECT COUNT(*) count FROM chunks").fetchone()["count"]
            compliance_records = self.conn.execute("SELECT COUNT(*) count FROM compliance_records WHERE review_status='approved'").fetchone()["count"]
            pending_review = self.conn.execute("SELECT COUNT(*) count FROM extraction_tasks WHERE status='pending'").fetchone()["count"]
            types = [dict(row) for row in self.conn.execute("SELECT knowledge_type,COUNT(*) count FROM documents GROUP BY knowledge_type")]
        return {"documents": documents, "chunks": chunks, "types": types, "compliance_records": compliance_records, "pending_review": pending_review}

    def list_documents(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute("SELECT id,filename,title,knowledge_type,mime_type,parser,ingestion_status,tags,created_at FROM documents ORDER BY id").fetchall()
        return [{**dict(row), "tags": json.loads(row["tags"])} for row in rows]

    def document_detail(self, document_id: int) -> dict[str, Any]:
        with self.lock:
            row = self.conn.execute(
                "SELECT id,filename,title,knowledge_type,mime_type,parser,ingestion_status,tags,source_path,full_text,metadata,created_at FROM documents WHERE id=?",
                (int(document_id),),
            ).fetchone()
        if not row:
            raise ValueError("document not found")
        item = dict(row)
        item["tags"] = json.loads(item.get("tags") or "[]")
        item["metadata"] = json.loads(item.get("metadata") or "{}")
        return item

    def document_chunks(self, document_id: int) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute("SELECT id,document_id,chunk_index,text,metadata FROM chunks WHERE document_id=? ORDER BY chunk_index,id", (document_id,)).fetchall()
        return [{**dict(row), "metadata": json.loads(row["metadata"] or "{}")} for row in rows]

    def create_extraction_task(self, *, document_id: int, chunk_id: int, candidate_type: str, candidate_name: str,
                               candidate_code: str, region_code: str, region_name: str, product_class: str,
                               confidence: float, candidate_payload: dict[str, Any]) -> int | None:
        with self.lock:
            existing = self.conn.execute(
                "SELECT id FROM extraction_tasks WHERE document_id=? AND chunk_id=? AND candidate_type=? AND candidate_name=?",
                (document_id, chunk_id, candidate_type, candidate_name),
            ).fetchone()
            if existing:
                return None
            cur = self.conn.execute(
                "INSERT INTO extraction_tasks(document_id,chunk_id,candidate_type,candidate_name,candidate_code,region_code,region_name,product_class,confidence,candidate_payload) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (document_id, chunk_id, candidate_type, candidate_name, candidate_code, region_code, region_name, product_class, confidence, json.dumps(candidate_payload, ensure_ascii=False)),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def list_extraction_tasks(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        where = "WHERE t.status=?" if status else ""
        params: list[Any] = [status] if status else []
        params.append(limit)
        with self.lock:
            rows = self.conn.execute(
                f"SELECT t.*,d.filename,d.title document_title,c.chunk_index,c.text chunk_text FROM extraction_tasks t "
                f"JOIN documents d ON d.id=t.document_id JOIN chunks c ON c.id=t.chunk_id {where} "
                f"ORDER BY CASE t.status WHEN 'pending' THEN 0 ELSE 1 END,t.id DESC LIMIT ?",
                params,
            ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["candidate_payload"] = json.loads(item["candidate_payload"] or "{}")
            output.append(item)
        return output

    def review_extraction_task(self, task_id: int, *, action: str, reviewer_note: str = "") -> dict[str, Any]:
        if action not in {"approve", "reject"}:
            raise ValueError("action must be approve or reject")
        with self.lock:
            row = self.conn.execute("SELECT * FROM extraction_tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                raise ValueError("extraction task not found")
            if row["status"] != "pending":
                raise ValueError(f"extraction task already {row['status']}")
            new_status = "approved" if action == "approve" else "rejected"
            self.conn.execute("UPDATE extraction_tasks SET status=?,reviewer_note=?,reviewed_at=CURRENT_TIMESTAMP WHERE id=?", (new_status, reviewer_note, task_id))
            record_id = None
            if action == "approve":
                record_id = self._upsert_compliance_record_locked(json.loads(row["candidate_payload"] or "{}"))
            self.conn.commit()
        return {"task_id": task_id, "status": new_status, "compliance_record_id": record_id}

    def _upsert_compliance_record_locked(self, payload: dict[str, Any]) -> int:
        key = (payload.get("record_type", "requirement"), payload.get("code", ""), payload.get("name", ""), payload.get("region_code", ""), payload.get("product_class", ""), payload.get("version", ""))
        existing = self.conn.execute("SELECT id FROM compliance_records WHERE record_type=? AND code=? AND name=? AND region_code=? AND product_class=? AND version=?", key).fetchone()
        values = (payload.get("region_name", ""), payload.get("status", "unknown"), payload.get("effective_from", ""), payload.get("effective_to", ""), payload.get("source_document_id"), payload.get("source_chunk_id"), json.dumps(payload.get("attributes", {}), ensure_ascii=False))
        if existing:
            self.conn.execute("UPDATE compliance_records SET region_name=?,status=?,effective_from=?,effective_to=?,source_document_id=?,source_chunk_id=?,attributes=?,review_status='approved',updated_at=CURRENT_TIMESTAMP WHERE id=?", (*values, existing["id"]))
            return int(existing["id"])
        cur = self.conn.execute(
            "INSERT INTO compliance_records(record_type,code,name,region_code,region_name,product_class,status,version,effective_from,effective_to,source_document_id,source_chunk_id,review_status,attributes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (payload.get("record_type", "requirement"), payload.get("code", ""), payload.get("name", ""), payload.get("region_code", ""), payload.get("region_name", ""), payload.get("product_class", ""), payload.get("status", "unknown"), payload.get("version", ""), payload.get("effective_from", ""), payload.get("effective_to", ""), payload.get("source_document_id"), payload.get("source_chunk_id"), "approved", json.dumps(payload.get("attributes", {}), ensure_ascii=False)),
        )
        return int(cur.lastrowid)

    def list_compliance_records(self, *, review_status: str | None = "approved", region_code: str | None = None,
                                product_class: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if review_status:
            clauses.append("r.review_status=?")
            params.append(review_status)
        if region_code:
            clauses.append("r.region_code=?")
            params.append(region_code)
        if product_class:
            clauses.append("(r.product_class='' OR r.product_class='*' OR lower(r.product_class)=lower(?))")
            params.append(product_class)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        with self.lock:
            rows = self.conn.execute(
                f"SELECT r.*,d.filename source_filename,c.chunk_index source_chunk_index FROM compliance_records r "
                f"LEFT JOIN documents d ON d.id=r.source_document_id LEFT JOIN chunks c ON c.id=r.source_chunk_id "
                f"{where} ORDER BY r.region_code,r.record_type,r.name LIMIT ?", params).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["attributes"] = json.loads(item["attributes"] or "{}")
            output.append(item)
        return output

    def _rows(self, knowledge_type: str | None = None) -> list[dict[str, Any]]:
        where = "WHERE d.knowledge_type=?" if knowledge_type else ""
        params = (knowledge_type,) if knowledge_type else ()
        with self.lock:
            rows = self.conn.execute(
                f"SELECT c.id chunk_id,c.chunk_index,c.text,c.metadata,d.id document_id,d.filename,d.title,d.knowledge_type,d.tags,v.vector_json "
                f"FROM chunks c JOIN documents d ON d.id=c.document_id LEFT JOIN chunk_vectors v ON v.chunk_id=c.id {where}", params).fetchall()
        return [{**dict(row), "metadata": json.loads(row["metadata"] or "{}"), "tags": json.loads(row["tags"] or "[]")} for row in rows]

    def keyword_search(self, query: str, *, limit: int = 20, knowledge_type: str | None = None) -> list[dict[str, Any]]:
        tokens = [token for token in tokenize(query) if token.strip()][:12]
        if not tokens:
            return []
        fts = " OR ".join(f'"{token.replace(chr(34), "")}"' for token in tokens)
        params: list[Any] = [fts]
        type_clause = ""
        if knowledge_type:
            type_clause = "AND d.knowledge_type=?"
            params.append(knowledge_type)
        params.append(max(limit * 4, 40))
        try:
            with self.lock:
                rows = self.conn.execute(
                    f"SELECT c.id chunk_id,c.chunk_index,c.text,c.metadata,d.id document_id,d.filename,d.title,d.knowledge_type,d.tags,bm25(chunk_fts) bm25_score "
                    f"FROM chunk_fts JOIN chunks c ON c.id=chunk_fts.chunk_id JOIN documents d ON d.id=c.document_id "
                    f"WHERE chunk_fts MATCH ? {type_clause} ORDER BY bm25_score ASC LIMIT ?", params).fetchall()
        except sqlite3.OperationalError:
            rows = []
        output = []
        for rank, row in enumerate(rows, 1):
            output.append({**dict(row), "metadata": json.loads(row["metadata"] or "{}"), "tags": json.loads(row["tags"] or "[]"), "keyword_rank": rank, "keyword_score": round(1.0 / rank, 4)})
        return output[:limit]

    def semantic_search(self, query: str, *, limit: int = 20, knowledge_type: str | None = None) -> list[dict[str, Any]]:
        query_vector, query_tokens = embed_text(query), tokenize(query)
        scored = []
        for row in self._rows(knowledge_type):
            vector_score = vector_cosine(query_vector, json.loads(row["vector_json"])) if row.get("vector_json") else 0.0
            token_score = cosine_score(query_tokens, row["text"])
            item = dict(row)
            item["semantic_score"] = round(max(0.0, 0.78 * vector_score + 0.22 * token_score), 4)
            scored.append(item)
        scored.sort(key=lambda item: item["semantic_score"], reverse=True)
        return scored[:limit]

    def recent_ingestion_events(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.conn.execute("SELECT filename,source_path,status,message,created_at FROM ingestion_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]
