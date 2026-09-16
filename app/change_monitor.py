from __future__ import annotations

import json
import sqlite3
from collections import Counter
from typing import Any

from app.store import KnowledgeStore

WATCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_change_tasks (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 fingerprint TEXT NOT NULL UNIQUE,
 event_type TEXT NOT NULL,
 severity TEXT NOT NULL DEFAULT 'medium',
 status TEXT NOT NULL DEFAULT 'open',
 title TEXT NOT NULL,
 summary TEXT NOT NULL,
 existing_record_id INTEGER,
 candidate_task_id INTEGER,
 related_record_id INTEGER,
 source_document_id INTEGER,
 source_chunk_id INTEGER,
 region_code TEXT DEFAULT '',
 product_class TEXT DEFAULT '',
 payload_json TEXT NOT NULL DEFAULT '{}',
 operator TEXT DEFAULT '',
 resolution_note TEXT DEFAULT '',
 created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 resolved_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_change_task_status ON knowledge_change_tasks(status,id DESC);
CREATE INDEX IF NOT EXISTS idx_change_task_type ON knowledge_change_tasks(event_type,id DESC);
CREATE INDEX IF NOT EXISTS idx_change_task_existing ON knowledge_change_tasks(existing_record_id,id DESC);
"""

EVENT_LABELS = {
    "version_difference": "版本差异",
    "status_difference": "效力状态变化",
    "effective_date_difference": "生效时间变化",
    "content_change": "内容变化",
    "requirement_change": "认证/要求变化",
    "possible_new_requirement": "可能新增认证要求",
    "possible_new_rule": "可能新增法规标准",
    "impact_review": "关联影响复核",
}

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _compatible_class(a: Any, b: Any) -> bool:
    left, right = _norm(a), _norm(b)
    if not left or not right or left == "*" or right == "*":
        return True
    return left in right or right in left


def _candidate_payload(task: dict[str, Any]) -> dict[str, Any]:
    payload = task.get("candidate_payload") or {}
    return dict(payload) if isinstance(payload, dict) else {}


class ChangeMonitorService:
    """Detects possible changes and creates human-review work items.

    Monitoring tasks are advisory only. They never modify formal knowledge, approve
    extraction candidates, or create legal/compliance conclusions automatically.
    """

    def __init__(self, store: KnowledgeStore):
        self.store = store
        with self.store.lock:
            self.store.conn.executescript(WATCH_SCHEMA)
            self.store.conn.commit()

    def reset(self) -> None:
        with self.store.lock:
            self.store.conn.execute("DELETE FROM knowledge_change_tasks")
            self.store.conn.commit()

    def _create(
        self,
        *,
        fingerprint: str,
        event_type: str,
        severity: str,
        title: str,
        summary: str,
        existing_record_id: int | None = None,
        candidate_task_id: int | None = None,
        related_record_id: int | None = None,
        source_document_id: int | None = None,
        source_chunk_id: int | None = None,
        region_code: str = "",
        product_class: str = "",
        payload: dict[str, Any] | None = None,
    ) -> bool:
        if event_type not in EVENT_LABELS:
            raise ValueError(f"unsupported event_type: {event_type}")
        if severity not in SEVERITY_ORDER:
            severity = "medium"
        with self.store.lock:
            existing = self.store.conn.execute(
                "SELECT id FROM knowledge_change_tasks WHERE fingerprint=?", (fingerprint,)
            ).fetchone()
            if existing:
                return False
            self.store.conn.execute(
                """INSERT INTO knowledge_change_tasks(
                       fingerprint,event_type,severity,status,title,summary,
                       existing_record_id,candidate_task_id,related_record_id,
                       source_document_id,source_chunk_id,region_code,product_class,payload_json)
                   VALUES(?,?,?,'open',?,?,?,?,?,?,?,?,?,?)""",
                (
                    fingerprint, event_type, severity, title, summary,
                    existing_record_id, candidate_task_id, related_record_id,
                    source_document_id, source_chunk_id, region_code, product_class,
                    json.dumps(payload or {}, ensure_ascii=False),
                ),
            )
            self.store.conn.commit()
        return True

    def _formal_records(self) -> list[dict[str, Any]]:
        return self.store.list_compliance_records(review_status="approved", limit=5000)

    def _match_identity(self, candidate: dict[str, Any], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        record_type = candidate.get("record_type") or candidate.get("candidate_type")
        code, name = _norm(candidate.get("code")), _norm(candidate.get("name"))
        region = _norm(candidate.get("region_code"))
        product_class = candidate.get("product_class")
        matched = []
        for item in records:
            if item.get("record_type") != record_type:
                continue
            if region and _norm(item.get("region_code")) and _norm(item.get("region_code")) != region:
                continue
            if not _compatible_class(product_class, item.get("product_class")):
                continue
            same_code = bool(code and _norm(item.get("code")) == code)
            same_name = bool(name and _norm(item.get("name")) == name)
            if same_code or same_name:
                matched.append(item)
        matched.sort(
            key=lambda x: (
                0 if str(x.get("status") or "") in {"active", "transition"} else 1,
                str(x.get("effective_from") or ""), str(x.get("version") or ""), int(x.get("id") or 0),
            ),
            reverse=True,
        )
        return matched

    def _has_business_context(self, candidate: dict[str, Any], records: list[dict[str, Any]]) -> bool:
        region = _norm(candidate.get("region_code"))
        product_class = candidate.get("product_class")
        for item in records:
            if region and _norm(item.get("region_code")) != region:
                continue
            if not _compatible_class(product_class, item.get("product_class")):
                continue
            return True
        return False

    def scan(self, *, document_id: int | None = None) -> dict[str, Any]:
        records = self._formal_records()
        tasks = self.store.list_extraction_tasks(status="pending", limit=2000)
        if document_id:
            tasks = [task for task in tasks if int(task.get("document_id") or 0) == int(document_id)]
        created = 0
        by_type: Counter[str] = Counter()

        for task in tasks:
            candidate = _candidate_payload(task)
            if not candidate:
                continue
            candidate.setdefault("record_type", task.get("candidate_type"))
            candidate.setdefault("name", task.get("candidate_name"))
            candidate.setdefault("code", task.get("candidate_code"))
            candidate.setdefault("region_code", task.get("region_code"))
            candidate.setdefault("region_name", task.get("region_name"))
            candidate.setdefault("product_class", task.get("product_class"))
            candidate_id = int(task.get("id") or 0)
            matches = self._match_identity(candidate, records)
            source_doc = int(task.get("document_id") or 0) or None
            source_chunk = int(task.get("chunk_id") or 0) or None
            region_code = str(candidate.get("region_code") or "")
            product_class = str(candidate.get("product_class") or "")

            events: list[tuple[str, str, str, dict[str, Any]]] = []
            if matches:
                current = matches[0]
                current_id = int(current.get("id") or 0)
                cv, ev = str(candidate.get("version") or "").strip(), str(current.get("version") or "").strip()
                if cv and cv != ev:
                    events.append((
                        "version_difference", "high", "发现可能的新版本或版本差异",
                        {"existing_version": ev, "candidate_version": cv},
                    ))
                cs, es = str(candidate.get("status") or "unknown"), str(current.get("status") or "unknown")
                if cs not in {"", "unknown"} and cs != es:
                    events.append((
                        "status_difference", "high", "发现可能的效力状态变化",
                        {"existing_status": es, "candidate_status": cs},
                    ))
                date_changes = {}
                for key in ("effective_from", "effective_to"):
                    cval, eval_ = str(candidate.get(key) or ""), str(current.get(key) or "")
                    if cval and cval != eval_:
                        date_changes[key] = {"existing": eval_, "candidate": cval}
                if date_changes:
                    events.append(("effective_date_difference", "medium", "发现可能的生效时间变化", date_changes))

                attrs = dict(candidate.get("attributes") or {})
                existing_attrs = dict(current.get("attributes") or {})
                changed_attrs = {}
                for key in ("authority", "applicability_scope", "exceptions", "transition_note", "legal_effect"):
                    cval, eval_ = _norm(attrs.get(key)), _norm(existing_attrs.get(key))
                    if cval and cval != eval_:
                        changed_attrs[key] = {"existing": existing_attrs.get(key, ""), "candidate": attrs.get(key, "")}
                if _norm(candidate.get("name")) and _norm(candidate.get("name")) != _norm(current.get("name")):
                    changed_attrs["name"] = {"existing": current.get("name", ""), "candidate": candidate.get("name", "")}
                if changed_attrs:
                    event = "requirement_change" if candidate.get("record_type") in {"certification", "requirement", "test_item"} else "content_change"
                    severity = "high" if event == "requirement_change" else "medium"
                    title = "发现可能的认证或技术要求变化" if event == "requirement_change" else "发现可能的法规标准内容变化"
                    events.append((event, severity, title, changed_attrs))

                for event_type, severity, title, diff in events:
                    if self._create(
                        fingerprint=f"candidate:{candidate_id}:record:{current_id}:{event_type}",
                        event_type=event_type,
                        severity=severity,
                        title=title,
                        summary=f"{candidate.get('code') or candidate.get('name') or '知识项'} 与现有正式知识存在差异，需人工核对后再决定是否更新正式知识。",
                        existing_record_id=current_id,
                        candidate_task_id=candidate_id,
                        source_document_id=source_doc,
                        source_chunk_id=source_chunk,
                        region_code=region_code,
                        product_class=product_class,
                        payload={"difference": diff, "candidate": candidate},
                    ):
                        created += 1
                        by_type[event_type] += 1
            elif self._has_business_context(candidate, records):
                record_type = str(candidate.get("record_type") or "")
                if record_type in {"certification", "requirement", "test_item"}:
                    event_type, severity, title = "possible_new_requirement", "medium", "发现可能新增的认证或技术要求"
                elif record_type in {"regulation", "standard"}:
                    event_type, severity, title = "possible_new_rule", "medium", "发现可能新增的法规或标准"
                else:
                    continue
                if self._create(
                    fingerprint=f"candidate:{candidate_id}:new:{event_type}",
                    event_type=event_type,
                    severity=severity,
                    title=title,
                    summary=f"新资料中识别到 {candidate.get('code') or candidate.get('name') or '新知识项'}，当前正式知识中未找到同一知识项，需人工确认是否纳入。",
                    candidate_task_id=candidate_id,
                    source_document_id=source_doc,
                    source_chunk_id=source_chunk,
                    region_code=region_code,
                    product_class=product_class,
                    payload={"candidate": candidate},
                ):
                    created += 1
                    by_type[event_type] += 1

        return {"created": created, "scanned_candidates": len(tasks), "created_by_type": dict(by_type), "summary": self.summary()}

    def create_impact_review(
        self,
        *,
        record_id: int,
        change_id: int,
        action: str,
        related_record_id: int | None = None,
    ) -> dict[str, Any]:
        record = next((item for item in self._formal_records() if int(item.get("id") or 0) == int(record_id)), None)
        if not record:
            return {"created": False, "reason": "record_not_found"}
        with self.store.lock:
            try:
                rows = self.store.conn.execute(
                    """SELECT id FROM graph_relations
                       WHERE status='approved' AND (source_record_id=? OR target_record_id=?)""",
                    (int(record_id), int(record_id)),
                ).fetchall()
                relation_count = len(rows)
            except sqlite3.OperationalError:
                relation_count = 0
        severity = "high" if action in {"status", "create_version"} else "medium"
        created = self._create(
            fingerprint=f"catalog-change:{int(change_id)}:impact",
            event_type="impact_review",
            severity=severity,
            title="正式知识发生变化，需复核关联影响",
            summary=f"{record.get('code') or record.get('name') or '正式知识'} 已完成维护，当前关联 {relation_count} 条已确认关系，建议复核相关认证、技术要求和准入结果。",
            existing_record_id=int(record_id),
            related_record_id=related_record_id,
            source_document_id=record.get("source_document_id"),
            source_chunk_id=record.get("source_chunk_id"),
            region_code=str(record.get("region_code") or ""),
            product_class=str(record.get("product_class") or ""),
            payload={"change_id": int(change_id), "action": action, "relation_count": relation_count},
        )
        return {"created": created, "relation_count": relation_count}

    def summary(self) -> dict[str, Any]:
        with self.store.lock:
            rows = self.store.conn.execute(
                "SELECT status,severity,event_type,COUNT(*) count FROM knowledge_change_tasks GROUP BY status,severity,event_type"
            ).fetchall()
        status_counts: Counter[str] = Counter()
        severity_counts: Counter[str] = Counter()
        type_counts: Counter[str] = Counter()
        for row in rows:
            count = int(row["count"])
            status_counts[row["status"]] += count
            if row["status"] == "open":
                severity_counts[row["severity"]] += count
                type_counts[row["event_type"]] += count
        return {
            "total": sum(status_counts.values()),
            "open": status_counts.get("open", 0),
            "resolved": status_counts.get("resolved", 0),
            "dismissed": status_counts.get("dismissed", 0),
            "open_high": severity_counts.get("high", 0),
            "open_medium": severity_counts.get("medium", 0),
            "event_counts": dict(type_counts),
        }

    def list_tasks(self, *, status: str = "open", event_type: str = "", severity: str = "", limit: int = 200) -> list[dict[str, Any]]:
        clauses, params = [], []
        if status:
            clauses.append("w.status=?")
            params.append(status)
        if event_type:
            clauses.append("w.event_type=?")
            params.append(event_type)
        if severity:
            clauses.append("w.severity=?")
            params.append(severity)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(int(limit), 500)))
        with self.store.lock:
            rows = self.store.conn.execute(
                f"""SELECT w.*,d.filename source_filename,t.candidate_name,t.candidate_code,t.status candidate_status,
                           r.code existing_code,r.name existing_name,r.version existing_version,r.status existing_status
                    FROM knowledge_change_tasks w
                    LEFT JOIN documents d ON d.id=w.source_document_id
                    LEFT JOIN extraction_tasks t ON t.id=w.candidate_task_id
                    LEFT JOIN compliance_records r ON r.id=w.existing_record_id
                    {where} ORDER BY CASE w.severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,w.id DESC LIMIT ?""",
                params,
            ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json") or "{}")
            item["event_label"] = EVENT_LABELS.get(item["event_type"], item["event_type"])
            output.append(item)
        return output

    def detail(self, task_id: int) -> dict[str, Any]:
        items = self.list_tasks(status="", limit=500)
        item = next((row for row in items if int(row.get("id") or 0) == int(task_id)), None)
        if not item:
            raise ValueError("change task not found")
        candidate = None
        if item.get("candidate_task_id"):
            candidate = next((t for t in self.store.list_extraction_tasks(status=None, limit=2000) if int(t.get("id") or 0) == int(item["candidate_task_id"])), None)
        existing = None
        if item.get("existing_record_id"):
            existing = next((r for r in self._formal_records() if int(r.get("id") or 0) == int(item["existing_record_id"])), None)
        excerpt = ""
        chunk_id = item.get("source_chunk_id")
        if chunk_id:
            with self.store.lock:
                row = self.store.conn.execute("SELECT text FROM chunks WHERE id=?", (int(chunk_id),)).fetchone()
            excerpt = str(row["text"] if row else "")[:1800]
        return {"task": item, "candidate": candidate, "existing": existing, "evidence_excerpt": excerpt}

    def review(self, task_id: int, *, action: str, operator: str = "", note: str = "") -> dict[str, Any]:
        if action not in {"resolve", "dismiss"}:
            raise ValueError("action must be resolve or dismiss")
        new_status = "resolved" if action == "resolve" else "dismissed"
        with self.store.lock:
            row = self.store.conn.execute("SELECT * FROM knowledge_change_tasks WHERE id=?", (int(task_id),)).fetchone()
            if not row:
                raise ValueError("change task not found")
            if row["status"] != "open":
                raise ValueError(f"change task already {row['status']}")
            self.store.conn.execute(
                """UPDATE knowledge_change_tasks
                   SET status=?,operator=?,resolution_note=?,resolved_at=CURRENT_TIMESTAMP WHERE id=?""",
                (new_status, str(operator or "").strip(), str(note or "").strip(), int(task_id)),
            )
            self.store.conn.commit()
        return {"task_id": int(task_id), "status": new_status, "summary": self.summary()}
