from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.graph_governance import GraphGovernanceService
from app.source_diff import SourceDifferenceService
from app.store import KnowledgeStore


IMPACT_STATUSES = {"draft", "confirmed", "dismissed"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _record_label(record: dict[str, Any]) -> str:
    code = str(record.get("code") or "").strip()
    name = str(record.get("name") or "").strip()
    return f"{code} {name}".strip() or f"知识项 {record.get('id')}"


class SourceImpactService:
    """Maps a confirmed source change review to formal knowledge and impact candidates.

    Matching and graph traversal only generate candidates. A human must select the
    source formal record and may edit/cancel/add impact items before confirming.
    No formal knowledge or approved graph relation is modified here.
    """

    def __init__(
        self,
        store: KnowledgeStore,
        source_diff: SourceDifferenceService,
        graph: GraphGovernanceService,
    ):
        self.store = store
        self.source_diff = source_diff
        self.graph = graph
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.store.lock:
            self.store.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS source_impact_cases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    update_event_id INTEGER NOT NULL UNIQUE,
                    source_record_id INTEGER,
                    match_payload_json TEXT NOT NULL DEFAULT '{}',
                    impact_items_json TEXT NOT NULL DEFAULT '[]',
                    review_status TEXT NOT NULL DEFAULT 'draft',
                    operator TEXT DEFAULT '',
                    note TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    confirmed_at TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_source_impact_status
                    ON source_impact_cases(review_status,id DESC);
                """
            )
            self.store.conn.commit()

    def _document_metadata(self, document_id: int | None) -> dict[str, Any]:
        if not document_id:
            return {}
        with self.store.lock:
            row = self.store.conn.execute(
                "SELECT metadata FROM documents WHERE id=?", (int(document_id),)
            ).fetchone()
        if not row:
            return {}
        return json.loads(row["metadata"] or "{}")

    def _review_context(self, event_id: int) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        review = self.source_diff.review_state(event_id)
        if review.get("review_status") != "confirmed":
            raise ValueError("source difference must be human-confirmed before impact analysis")
        report = self.source_diff.analyze(event_id)
        current_meta = self._document_metadata(report.get("current", {}).get("document_id"))
        return review, report, current_meta

    def match_formal_records(self, event_id: int, *, limit: int = 50) -> dict[str, Any]:
        review, report, current_meta = self._review_context(event_id)
        selected_items = [item for item in review.get("items", []) if item.get("selected", True)]
        review_text = "\n".join(
            " ".join([
                str(item.get("before") or ""),
                str(item.get("after") or ""),
                str(item.get("label") or ""),
                str(item.get("impact_note") or ""),
            ])
            for item in selected_items
        )
        review_text_norm = _norm(review_text)
        source_key = str(report.get("source_key") or current_meta.get("source_key") or "")
        region_code = str(current_meta.get("region_code") or "").upper()
        previous_document_id = report.get("previous", {}).get("document_id")
        current_document_id = report.get("current", {}).get("document_id")

        records = self.store.list_compliance_records(review_status="approved", limit=5000)
        candidates: list[dict[str, Any]] = []
        for record in records:
            score = 0.0
            reasons: list[str] = []
            source_doc_id = record.get("source_document_id")
            if source_doc_id and int(source_doc_id) == int(previous_document_id or -1):
                score += 0.58
                reasons.append("正式知识来源为上一版本文档")
            if source_doc_id and int(source_doc_id) == int(current_document_id or -1):
                score += 0.58
                reasons.append("正式知识来源为当前版本文档")

            code = _norm(record.get("code"))
            name = _norm(record.get("name"))
            if code and len(code) >= 3 and code in review_text_norm:
                score += 0.35
                reasons.append("变化内容命中知识编号")
            if name and len(name) >= 4 and name in review_text_norm:
                score += 0.24
                reasons.append("变化内容命中知识名称")

            record_region = str(record.get("region_code") or "").upper()
            if region_code and record_region == region_code:
                score += 0.08
                reasons.append("国家/地区一致")

            source_meta = self._document_metadata(source_doc_id)
            if source_key and str(source_meta.get("source_key") or "") == source_key:
                score += 0.20
                reasons.append("来源网站一致")

            if not reasons:
                continue
            candidates.append({
                "record_id": int(record["id"]),
                "record_type": record.get("record_type", ""),
                "code": record.get("code", ""),
                "name": record.get("name", ""),
                "version": record.get("version", ""),
                "status": record.get("status", ""),
                "region_code": record.get("region_code", ""),
                "region_name": record.get("region_name", ""),
                "product_class": record.get("product_class", ""),
                "score": round(min(score, 1.0), 4),
                "reasons": reasons,
                "source_document_id": source_doc_id,
            })
        candidates.sort(key=lambda item: (-item["score"], item["record_type"], item["name"]))
        return {
            "update_event_id": int(event_id),
            "source_key": source_key,
            "region_code": region_code,
            "items": candidates[: min(max(int(limit), 1), 200)],
            "summary": {
                "candidates": len(candidates),
                "high_confidence": sum(1 for item in candidates if item["score"] >= 0.75),
            },
            "note": "这里只提供正式知识匹配候选，由复核人员选择实际受变更影响的正式知识对象。",
        }

    def _formal_record(self, record_id: int) -> dict[str, Any]:
        records = self.store.list_compliance_records(review_status="approved", limit=5000)
        for record in records:
            if int(record.get("id") or 0) == int(record_id):
                return record
        raise ValueError("approved formal knowledge record not found")

    def _default_impact_items(self, record_id: int) -> list[dict[str, Any]]:
        source = self._formal_record(record_id)
        graph = self.graph.impact_analysis(record_id)
        items: list[dict[str, Any]] = [{
            "impact_id": "impact-1",
            "selected": True,
            "impact_kind": "formal_record_review",
            "impact_level": "direct",
            "record_id": int(source["id"]),
            "record_type": source.get("record_type", ""),
            "code": source.get("code", ""),
            "name": source.get("name", ""),
            "label": _record_label(source),
            "reason": "法规来源版本变化已人工确认，需要复核该正式知识的版本、效力、日期或要求内容。",
            "action": "核对正式知识内容，必要时通过正式知识维护流程新增版本或更新状态。",
            "note": "",
        }]
        seq = 2
        for affected in graph.get("downstream", []):
            record = affected.get("record") or {}
            depth = int(affected.get("depth") or 1)
            items.append({
                "impact_id": f"impact-{seq}",
                "selected": True,
                "impact_kind": "related_knowledge_review",
                "impact_level": "direct" if depth == 1 else "indirect",
                "record_id": int(record.get("id") or 0),
                "record_type": record.get("record_type", ""),
                "code": record.get("code", ""),
                "name": record.get("name", ""),
                "label": _record_label(record),
                "reason": f"通过已确认知识关系链关联，关系深度 {depth}。",
                "action": "复核该知识项是否需要同步调整、重新确认适用性或重新验证准入路径。",
                "note": "",
                "graph_depth": depth,
                "relation_path": affected.get("via") or [],
            })
            seq += 1
        return items

    def build_case(self, event_id: int, *, record_id: int, refresh: bool = False) -> dict[str, Any]:
        review, report, _ = self._review_context(event_id)
        source_record = self._formal_record(record_id)
        matches = self.match_formal_records(event_id, limit=200)
        selected_match = next(
            (item for item in matches["items"] if int(item["record_id"]) == int(record_id)),
            {
                "record_id": int(record_id),
                "record_type": source_record.get("record_type", ""),
                "code": source_record.get("code", ""),
                "name": source_record.get("name", ""),
                "score": 0.0,
                "reasons": ["人工指定正式知识"],
            },
        )

        with self.store.lock:
            existing = self.store.conn.execute(
                "SELECT * FROM source_impact_cases WHERE update_event_id=?",
                (int(event_id),),
            ).fetchone()
        if existing and int(existing["source_record_id"] or 0) == int(record_id) and not refresh:
            return self.case(event_id)

        items = self._default_impact_items(record_id)
        now = _now()
        match_payload = {
            "selected": selected_match,
            "source_key": report.get("source_key", ""),
            "difference_review": {
                "operator": review.get("operator", ""),
                "confirmed_at": review.get("confirmed_at", ""),
                "selected_change_items": sum(
                    1 for item in review.get("items", []) if item.get("selected", True)
                ),
            },
        }
        with self.store.lock:
            self.store.conn.execute(
                """
                INSERT INTO source_impact_cases(
                    update_event_id,source_record_id,match_payload_json,impact_items_json,
                    review_status,operator,note,created_at,updated_at,confirmed_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(update_event_id) DO UPDATE SET
                    source_record_id=excluded.source_record_id,
                    match_payload_json=excluded.match_payload_json,
                    impact_items_json=excluded.impact_items_json,
                    review_status='draft',
                    operator='',
                    note='',
                    updated_at=excluded.updated_at,
                    confirmed_at=''
                """,
                (
                    int(event_id), int(record_id),
                    json.dumps(match_payload, ensure_ascii=False),
                    json.dumps(items, ensure_ascii=False),
                    "draft", "", "", now, now, "",
                ),
            )
            self.store.conn.commit()
        return self.case(event_id)

    def case(self, event_id: int) -> dict[str, Any]:
        with self.store.lock:
            row = self.store.conn.execute(
                "SELECT * FROM source_impact_cases WHERE update_event_id=?",
                (int(event_id),),
            ).fetchone()
        if not row:
            return {
                "update_event_id": int(event_id),
                "exists": False,
                "review_status": "not_started",
                "impact_items": [],
            }
        item = dict(row)
        item["exists"] = True
        item["match"] = json.loads(item.pop("match_payload_json") or "{}")
        item["impact_items"] = json.loads(item.pop("impact_items_json") or "[]")
        return item

    def save_case(
        self,
        event_id: int,
        *,
        items: list[dict[str, Any]],
        action: str = "save",
        operator: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        if action not in {"save", "confirm", "dismiss"}:
            raise ValueError("action must be save, confirm or dismiss")
        current = self.case(event_id)
        if not current.get("exists"):
            raise ValueError("impact case has not been built")
        normalized: list[dict[str, Any]] = []
        for index, raw in enumerate(items or [], start=1):
            normalized.append({
                "impact_id": str(raw.get("impact_id") or f"manual-impact-{index}"),
                "selected": bool(raw.get("selected", True)),
                "impact_kind": str(raw.get("impact_kind") or "manual_review").strip(),
                "impact_level": str(raw.get("impact_level") or "direct").strip(),
                "record_id": int(raw.get("record_id") or 0),
                "record_type": str(raw.get("record_type") or "").strip(),
                "code": str(raw.get("code") or "").strip(),
                "name": str(raw.get("name") or "").strip(),
                "label": str(raw.get("label") or raw.get("name") or "人工补充影响项").strip(),
                "reason": str(raw.get("reason") or "").strip(),
                "action": str(raw.get("action") or "").strip(),
                "note": str(raw.get("note") or "").strip(),
                "graph_depth": int(raw.get("graph_depth") or 0),
                "relation_path": raw.get("relation_path") or [],
            })
        status = {"save": "draft", "confirm": "confirmed", "dismiss": "dismissed"}[action]
        now = _now()
        confirmed_at = now if action in {"confirm", "dismiss"} else ""
        with self.store.lock:
            self.store.conn.execute(
                """
                UPDATE source_impact_cases SET
                    impact_items_json=?,review_status=?,operator=?,note=?,
                    updated_at=?,confirmed_at=?
                WHERE update_event_id=?
                """,
                (
                    json.dumps(normalized, ensure_ascii=False),
                    status,
                    str(operator or "").strip(),
                    str(note or "").strip(),
                    now,
                    confirmed_at,
                    int(event_id),
                ),
            )
            self.store.conn.commit()
        return self.case(event_id)

    def list_cases(self, *, status: str = "", limit: int = 100) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            if status not in IMPACT_STATUSES:
                raise ValueError(f"unsupported impact status: {status}")
            clauses.append("review_status=?")
            params.append(status)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(min(max(int(limit), 1), 500))
        with self.store.lock:
            rows = self.store.conn.execute(
                f"""
                SELECT id,update_event_id,source_record_id,review_status,operator,note,
                       created_at,updated_at,confirmed_at
                FROM source_impact_cases {where}
                ORDER BY id DESC LIMIT ?
                """,
                params,
            ).fetchall()
        items = [dict(row) for row in rows]
        return {
            "items": items,
            "summary": {
                "cases": len(items),
                "draft": sum(1 for item in items if item["review_status"] == "draft"),
                "confirmed": sum(1 for item in items if item["review_status"] == "confirmed"),
                "dismissed": sum(1 for item in items if item["review_status"] == "dismissed"),
            },
        }
