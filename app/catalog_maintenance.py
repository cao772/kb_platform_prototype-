from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from typing import Any

from app.governance import ALLOWED_RECORD_TYPES, ALLOWED_STATUSES
from app.store import KnowledgeStore

MAINTENANCE_SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog_change_log (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 record_id INTEGER NOT NULL,
 related_record_id INTEGER,
 action TEXT NOT NULL,
 operator TEXT DEFAULT '',
 note TEXT DEFAULT '',
 before_json TEXT NOT NULL,
 after_json TEXT NOT NULL,
 created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_catalog_change_record ON catalog_change_log(record_id,id DESC);
CREATE INDEX IF NOT EXISTS idx_catalog_change_related ON catalog_change_log(related_record_id,id DESC);
"""

EDITABLE_FIELDS = {
    "record_type", "code", "name", "region_code", "region_name", "product_class",
    "status", "version", "effective_from", "effective_to",
}
ATTRIBUTE_FIELDS = {
    "authority", "applicability_scope", "exceptions", "transition_note",
    "source_url", "legal_effect", "review_basis",
}

ACTION_LABELS = {
    "update": "编辑知识",
    "status": "状态维护",
    "create_version": "新增替代版本",
    "attach_evidence": "补充依据",
}


def _iso_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return date.fromisoformat(text).isoformat()


def _snapshot(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    attrs = item.get("attributes")
    if isinstance(attrs, str):
        try:
            attrs = json.loads(attrs or "{}")
        except json.JSONDecodeError:
            attrs = {}
    item["attributes"] = dict(attrs or {})
    return item


class CatalogMaintenanceService:
    """Human-governed maintenance for approved formal knowledge.

    All changes are explicit business operations and are written to an audit log.
    Records are never hard-deleted by this service; retirement is represented by
    lifecycle status so history, evidence and graph references remain traceable.
    """

    def __init__(self, store: KnowledgeStore):
        self.store = store
        with self.store.lock:
            self.store.conn.executescript(MAINTENANCE_SCHEMA)
            self.store.conn.commit()

    def _record_row(self, record_id: int) -> sqlite3.Row:
        with self.store.lock:
            row = self.store.conn.execute(
                "SELECT * FROM compliance_records WHERE id=? AND review_status='approved'",
                (int(record_id),),
            ).fetchone()
        if not row:
            raise ValueError("formal knowledge record not found")
        return row

    def _record(self, record_id: int) -> dict[str, Any]:
        return _snapshot(self._record_row(record_id))

    def _validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        record_type = str(payload.get("record_type") or "").strip()
        if record_type not in ALLOWED_RECORD_TYPES:
            raise ValueError(f"unsupported record_type: {record_type}")
        status = str(payload.get("status") or "unknown").strip()
        if status not in ALLOWED_STATUSES:
            raise ValueError(f"unsupported status: {status}")
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("name is required")
        start = _iso_date(payload.get("effective_from"))
        end = _iso_date(payload.get("effective_to"))
        if start and end and start > end:
            raise ValueError("effective_from cannot be later than effective_to")
        payload = dict(payload)
        payload["record_type"] = record_type
        payload["status"] = status
        payload["name"] = name
        payload["effective_from"] = start
        payload["effective_to"] = end
        payload["region_code"] = str(payload.get("region_code") or "").strip().upper()
        for key in ("code", "region_name", "product_class", "version"):
            payload[key] = str(payload.get(key) or "").strip()
        return payload

    def _audit_locked(
        self,
        *,
        record_id: int,
        action: str,
        before: dict[str, Any],
        after: dict[str, Any],
        operator: str = "",
        note: str = "",
        related_record_id: int | None = None,
    ) -> int:
        cur = self.store.conn.execute(
            """INSERT INTO catalog_change_log(
                   record_id,related_record_id,action,operator,note,before_json,after_json)
               VALUES(?,?,?,?,?,?,?)""",
            (
                int(record_id), related_record_id, action, str(operator or "").strip(), str(note or "").strip(),
                json.dumps(before, ensure_ascii=False, sort_keys=True),
                json.dumps(after, ensure_ascii=False, sort_keys=True),
            ),
        )
        return int(cur.lastrowid)

    def update_record(
        self,
        record_id: int,
        *,
        changes: dict[str, Any],
        operator: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        before = self._record(record_id)
        payload = dict(before)
        attrs = dict(before.get("attributes") or {})
        for key in EDITABLE_FIELDS:
            if key in changes:
                payload[key] = changes[key]
        for key in ATTRIBUTE_FIELDS:
            if key in changes:
                attrs[key] = str(changes.get(key) or "").strip()
        payload["attributes"] = attrs
        payload = self._validate_payload(payload)

        with self.store.lock:
            try:
                self.store.conn.execute(
                    """UPDATE compliance_records SET
                           record_type=?,code=?,name=?,region_code=?,region_name=?,product_class=?,
                           status=?,version=?,effective_from=?,effective_to=?,attributes=?,updated_at=CURRENT_TIMESTAMP
                       WHERE id=? AND review_status='approved'""",
                    (
                        payload["record_type"], payload["code"], payload["name"], payload["region_code"],
                        payload["region_name"], payload["product_class"], payload["status"], payload["version"],
                        payload["effective_from"], payload["effective_to"], json.dumps(attrs, ensure_ascii=False),
                        int(record_id),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("same formal knowledge version already exists") from exc
            after = self._record(record_id)
            change_id = self._audit_locked(
                record_id=record_id, action="update", before=before, after=after, operator=operator, note=note,
            )
            self.store.conn.commit()
        return {"record_id": int(record_id), "change_id": change_id, "item": after}

    def set_status(
        self,
        record_id: int,
        *,
        status: str,
        operator: str = "",
        note: str = "",
        effective_to: str = "",
    ) -> dict[str, Any]:
        status = str(status or "").strip()
        if status not in ALLOWED_STATUSES:
            raise ValueError(f"unsupported status: {status}")
        before = self._record(record_id)
        end = _iso_date(effective_to) if effective_to else str(before.get("effective_to") or "")
        start = str(before.get("effective_from") or "")
        if start and end and start > end:
            raise ValueError("effective_to cannot be earlier than effective_from")
        with self.store.lock:
            self.store.conn.execute(
                "UPDATE compliance_records SET status=?,effective_to=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, end, int(record_id)),
            )
            after = self._record(record_id)
            change_id = self._audit_locked(
                record_id=record_id, action="status", before=before, after=after, operator=operator, note=note,
            )
            self.store.conn.commit()
        return {"record_id": int(record_id), "change_id": change_id, "item": after}

    def create_replacement_version(
        self,
        record_id: int,
        *,
        version: str,
        effective_from: str,
        effective_to: str = "",
        status: str = "active",
        operator: str = "",
        note: str = "",
        copy_evidence: bool = True,
        mark_source_superseded: bool = True,
        close_source_day_before: bool = False,
    ) -> dict[str, Any]:
        before = self._record(record_id)
        version = str(version or "").strip()
        if not version:
            raise ValueError("new version is required")
        start = _iso_date(effective_from)
        if not start:
            raise ValueError("effective_from is required")
        end = _iso_date(effective_to)
        if end and start > end:
            raise ValueError("effective_from cannot be later than effective_to")
        if status not in ALLOWED_STATUSES:
            raise ValueError(f"unsupported status: {status}")

        payload = dict(before)
        payload["id"] = None
        payload["version"] = version
        payload["status"] = status
        payload["effective_from"] = start
        payload["effective_to"] = end
        if not copy_evidence:
            payload["source_document_id"] = None
            payload["source_chunk_id"] = None
        payload = self._validate_payload(payload)

        with self.store.lock:
            duplicate = self.store.conn.execute(
                """SELECT id FROM compliance_records
                   WHERE record_type=? AND code=? AND name=? AND region_code=? AND product_class=? AND version=?""",
                (
                    payload["record_type"], payload["code"], payload["name"], payload["region_code"],
                    payload["product_class"], payload["version"],
                ),
            ).fetchone()
            if duplicate:
                raise ValueError("the replacement version already exists")

            if mark_source_superseded:
                source_end = str(before.get("effective_to") or "")
                if close_source_day_before:
                    previous_day = (date.fromisoformat(start) - timedelta(days=1)).isoformat()
                    if not before.get("effective_from") or str(before.get("effective_from")) <= previous_day:
                        source_end = previous_day
                self.store.conn.execute(
                    "UPDATE compliance_records SET status='superseded',effective_to=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (source_end, int(record_id)),
                )

            cur = self.store.conn.execute(
                """INSERT INTO compliance_records(
                       record_type,code,name,region_code,region_name,product_class,status,version,
                       effective_from,effective_to,source_document_id,source_chunk_id,review_status,attributes)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'approved', ?)""",
                (
                    payload["record_type"], payload["code"], payload["name"], payload["region_code"],
                    payload["region_name"], payload["product_class"], payload["status"], payload["version"],
                    payload["effective_from"], payload["effective_to"], payload.get("source_document_id"),
                    payload.get("source_chunk_id"), json.dumps(payload.get("attributes") or {}, ensure_ascii=False),
                ),
            )
            new_id = int(cur.lastrowid)

            # Creating a replacement version is itself an explicit human-governed action,
            # so the version relation can be recorded as an approved formal relation.
            try:
                self.store.conn.execute(
                    """INSERT INTO graph_relations(
                           source_record_id,relation_type,target_record_id,confidence,
                           evidence_document_id,evidence_chunk_id,status,reviewer_note,reviewed_at)
                       VALUES(?, 'REPLACED_BY', ?, 1.0, ?, ?, 'approved', ?, CURRENT_TIMESTAMP)
                       ON CONFLICT(source_record_id,relation_type,target_record_id)
                       DO UPDATE SET status='approved',confidence=1.0,reviewer_note=excluded.reviewer_note,reviewed_at=CURRENT_TIMESTAMP""",
                    (
                        int(record_id), new_id, before.get("source_document_id"), before.get("source_chunk_id"),
                        str(note or "").strip() or "正式知识维护：版本替代",
                    ),
                )
            except sqlite3.OperationalError:
                pass

            source_after = self._record(record_id)
            new_after = self._record(new_id)
            change_id = self._audit_locked(
                record_id=record_id, related_record_id=new_id, action="create_version",
                before=before,
                after={"source": source_after, "replacement": new_after},
                operator=operator, note=note,
            )
            self.store.conn.commit()
        return {
            "record_id": int(record_id), "replacement_record_id": new_id,
            "change_id": change_id, "source": source_after, "replacement": new_after,
        }

    def attach_evidence(
        self,
        record_id: int,
        *,
        document_id: int | None = None,
        chunk_id: int | None = None,
        chunk_index: int | None = None,
        source_url: str = "",
        review_basis: str = "",
        operator: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        before = self._record(record_id)
        resolved_document_id = int(document_id) if document_id else before.get("source_document_id")
        resolved_chunk_id = int(chunk_id) if chunk_id else None

        with self.store.lock:
            if resolved_document_id:
                doc = self.store.conn.execute("SELECT id FROM documents WHERE id=?", (resolved_document_id,)).fetchone()
                if not doc:
                    raise ValueError("source document not found")
                if resolved_chunk_id:
                    chunk = self.store.conn.execute(
                        "SELECT id FROM chunks WHERE id=? AND document_id=?",
                        (resolved_chunk_id, resolved_document_id),
                    ).fetchone()
                    if not chunk:
                        raise ValueError("source chunk does not belong to selected document")
                elif chunk_index is not None:
                    chunk = self.store.conn.execute(
                        "SELECT id FROM chunks WHERE document_id=? AND chunk_index=? ORDER BY id LIMIT 1",
                        (resolved_document_id, int(chunk_index)),
                    ).fetchone()
                    if not chunk:
                        raise ValueError("source chunk index not found")
                    resolved_chunk_id = int(chunk["id"])
                elif not before.get("source_chunk_id") or int(before.get("source_document_id") or 0) != resolved_document_id:
                    first = self.store.conn.execute(
                        "SELECT id FROM chunks WHERE document_id=? ORDER BY chunk_index,id LIMIT 1",
                        (resolved_document_id,),
                    ).fetchone()
                    resolved_chunk_id = int(first["id"]) if first else None
                else:
                    resolved_chunk_id = int(before.get("source_chunk_id"))

            attrs = dict(before.get("attributes") or {})
            if source_url != "":
                attrs["source_url"] = str(source_url or "").strip()
            if review_basis != "":
                attrs["review_basis"] = str(review_basis or "").strip()
            self.store.conn.execute(
                """UPDATE compliance_records SET source_document_id=?,source_chunk_id=?,attributes=?,updated_at=CURRENT_TIMESTAMP
                   WHERE id=?""",
                (
                    resolved_document_id, resolved_chunk_id,
                    json.dumps(attrs, ensure_ascii=False), int(record_id),
                ),
            )
            after = self._record(record_id)
            change_id = self._audit_locked(
                record_id=record_id, action="attach_evidence", before=before, after=after,
                operator=operator, note=note,
            )
            self.store.conn.commit()
        return {"record_id": int(record_id), "change_id": change_id, "item": after}

    def history(self, record_id: int, *, limit: int = 100) -> list[dict[str, Any]]:
        self._record(record_id)
        with self.store.lock:
            rows = self.store.conn.execute(
                """SELECT * FROM catalog_change_log
                   WHERE record_id=? OR related_record_id=?
                   ORDER BY id DESC LIMIT ?""",
                (int(record_id), int(record_id), max(1, min(int(limit), 300))),
            ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["action_label"] = ACTION_LABELS.get(item.get("action"), item.get("action"))
            try:
                item["before"] = json.loads(item.pop("before_json") or "{}")
            except json.JSONDecodeError:
                item["before"] = {}
            try:
                item["after"] = json.loads(item.pop("after_json") or "{}")
            except json.JSONDecodeError:
                item["after"] = {}
            output.append(item)
        return output

    def evidence_options(self, *, document_id: int | None = None, limit: int = 200) -> dict[str, Any]:
        documents = self.store.list_documents()
        chunks: list[dict[str, Any]] = []
        if document_id:
            chunks = self.store.document_chunks(int(document_id))[: max(1, min(int(limit), 500))]
            chunks = [
                {
                    "id": item["id"], "document_id": item["document_id"], "chunk_index": item["chunk_index"],
                    "preview": str(item.get("text") or "")[:240],
                }
                for item in chunks
            ]
        return {"documents": documents, "chunks": chunks}
