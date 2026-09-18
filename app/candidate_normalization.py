from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.ontology_registry import OntologyRegistryService
from app.regions import canonical_region_code, target_market
from app.store import KnowledgeStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CandidateNormalizationService:
    """Suggest and apply terminology normalization before human approval.

    Applying normalization only edits the pending candidate. It never approves the
    candidate or writes formal knowledge; the existing human review step remains
    mandatory and editable.
    """

    def __init__(self, store: KnowledgeStore, registry: OntologyRegistryService):
        self.store = store
        self.registry = registry
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.store.lock:
            self.store.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS candidate_normalization_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    before_json TEXT NOT NULL,
                    after_json TEXT NOT NULL,
                    operator TEXT DEFAULT '',
                    note TEXT DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_candidate_normalization_task
                    ON candidate_normalization_log(task_id,id DESC);
                """
            )
            self.store.conn.commit()

    def _task(self, task_id: int) -> dict[str, Any]:
        with self.store.lock:
            row = self.store.conn.execute(
                """SELECT t.*,d.filename,d.title document_title,c.text chunk_text
                   FROM extraction_tasks t
                   JOIN documents d ON d.id=t.document_id
                   JOIN chunks c ON c.id=t.chunk_id
                   WHERE t.id=?""",
                (int(task_id),),
            ).fetchone()
        if not row:
            raise ValueError("extraction task not found")
        item = dict(row)
        item["candidate_payload"] = json.loads(item.get("candidate_payload") or "{}")
        return item

    def suggestions(self, task_id: int) -> dict[str, Any]:
        task = self._task(task_id)
        payload = dict(task["candidate_payload"])
        attrs = dict(payload.get("attributes") or {})
        fields: dict[str, Any] = {}

        def term_suggestion(field: str, value: str, term_type: str) -> None:
            result = self.registry.normalize(value, term_type=term_type) if value else {"matches": []}
            fields[field] = {
                "original": value,
                "matches": result.get("matches", []),
            }

        term_suggestion("record_type", str(payload.get("record_type") or ""), "record_type")
        term_suggestion("product_class", str(payload.get("product_class") or ""), "product_class")
        term_suggestion("authority", str(attrs.get("authority") or ""), "authority")

        region_code = str(payload.get("region_code") or "").strip()
        canonical = canonical_region_code(region_code)
        market = target_market(canonical) if canonical else None
        fields["region"] = {
            "original_code": region_code,
            "original_name": str(payload.get("region_name") or ""),
            "canonical_code": canonical,
            "canonical_name": market.name_zh if market else str(payload.get("region_name") or ""),
            "recognized_target_market": bool(market),
        }

        return {
            "task_id": int(task_id),
            "status": task["status"],
            "filename": task["filename"],
            "candidate": payload,
            "fields": fields,
            "note": "术语归一只修改待校核候选；业务人员仍可继续编辑，并需人工确认后才进入正式知识库。",
        }

    def list_pending(self, *, limit: int = 100) -> dict[str, Any]:
        tasks = self.store.list_extraction_tasks(status="pending", limit=min(max(int(limit), 1), 500))
        items = []
        for task in tasks:
            try:
                suggestion = self.suggestions(int(task["id"]))
            except ValueError:
                continue
            suggested_fields = 0
            for field, detail in suggestion["fields"].items():
                if field == "region":
                    suggested_fields += int(bool(detail.get("canonical_code")))
                else:
                    suggested_fields += int(bool(detail.get("matches")))
            items.append({
                "task_id": int(task["id"]),
                "candidate_type": task.get("candidate_type", ""),
                "candidate_name": task.get("candidate_name", ""),
                "filename": task.get("filename", ""),
                "confidence": task.get("confidence", 0),
                "suggested_fields": suggested_fields,
            })
        return {
            "items": items,
            "summary": {
                "pending": len(items),
                "with_suggestions": sum(1 for item in items if item["suggested_fields"] > 0),
            },
        }

    def apply(
        self,
        task_id: int,
        *,
        changes: dict[str, Any],
        operator: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        task = self._task(task_id)
        if task["status"] != "pending":
            raise ValueError("only pending extraction tasks can be normalized")
        before = dict(task["candidate_payload"])
        after = json.loads(json.dumps(before, ensure_ascii=False))
        attrs = dict(after.get("attributes") or {})

        if "record_type" in changes:
            value = str(changes.get("record_type") or "").strip()
            if value:
                normalized = self.registry.normalize(value, term_type="record_type")
                match = normalized.get("matches", [])
                after["record_type"] = str(match[0].get("code") or value) if match else value
        if "product_class" in changes:
            after["product_class"] = str(changes.get("product_class") or "").strip()
        if "authority" in changes:
            attrs["authority"] = str(changes.get("authority") or "").strip()
        if "region_code" in changes or "region_name" in changes:
            code = canonical_region_code(str(changes.get("region_code") or after.get("region_code") or ""))
            market = target_market(code) if code else None
            after["region_code"] = code
            after["region_name"] = str(
                changes.get("region_name")
                or (market.name_zh if market else after.get("region_name") or "")
            ).strip()

        attrs["normalization_review"] = {
            "applied": True,
            "operator": str(operator or "").strip(),
            "note": str(note or "").strip(),
            "at": _now(),
            "original": {
                "record_type": before.get("record_type", ""),
                "product_class": before.get("product_class", ""),
                "authority": (before.get("attributes") or {}).get("authority", ""),
                "region_code": before.get("region_code", ""),
                "region_name": before.get("region_name", ""),
            },
        }
        after["attributes"] = attrs

        with self.store.lock:
            self.store.conn.execute(
                """UPDATE extraction_tasks
                   SET candidate_type=?,candidate_name=?,candidate_code=?,
                       region_code=?,region_name=?,product_class=?,candidate_payload=?
                   WHERE id=? AND status='pending'""",
                (
                    after.get("record_type", task.get("candidate_type", "")),
                    after.get("name", task.get("candidate_name", "")),
                    after.get("code", task.get("candidate_code", "")),
                    after.get("region_code", ""),
                    after.get("region_name", ""),
                    after.get("product_class", ""),
                    json.dumps(after, ensure_ascii=False),
                    int(task_id),
                ),
            )
            self.store.conn.execute(
                """INSERT INTO candidate_normalization_log(
                       task_id,before_json,after_json,operator,note,created_at
                   ) VALUES(?,?,?,?,?,?)""",
                (
                    int(task_id),
                    json.dumps(before, ensure_ascii=False),
                    json.dumps(after, ensure_ascii=False),
                    str(operator or "").strip(),
                    str(note or "").strip(),
                    _now(),
                ),
            )
            self.store.conn.commit()
        return {
            "task_id": int(task_id),
            "status": "pending",
            "candidate": after,
            "note": "已更新待校核候选，尚未批准为正式知识；请继续人工复核和修改。",
        }
