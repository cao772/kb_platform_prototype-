from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from typing import Any

from app.compliance import DEMO_RECORDS
from app.store import KnowledgeStore

ALLOWED_RECORD_TYPES = {"regulation", "standard", "certification", "requirement", "test_item"}
ALLOWED_STATUSES = {"unknown", "draft", "active", "transition", "repealed", "withdrawn", "superseded"}
REGION_COORDINATES = {
    "EU": {"lat": 50.8, "lon": 10.5},
    "US": {"lat": 38.0, "lon": -97.0},
    "CN": {"lat": 35.9, "lon": 104.2},
    "UK": {"lat": 55.4, "lon": -3.4},
    "JP": {"lat": 36.2, "lon": 138.3},
    "KR": {"lat": 36.5, "lon": 127.9},
    "CA": {"lat": 56.1, "lon": -106.3},
    "AU": {"lat": -25.3, "lon": 133.8},
    "IN": {"lat": 20.6, "lon": 79.0},
}


def _iso_date(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError(f"invalid ISO date: {text}") from exc


def normalize_candidate_edits(base: dict[str, Any], edits: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(base or {})
    edits = dict(edits or {})
    editable = {
        "record_type", "code", "name", "region_code", "region_name", "product_class",
        "status", "version", "effective_from", "effective_to",
    }
    for key in editable:
        if key in edits:
            payload[key] = str(edits[key] or "").strip()

    record_type = payload.get("record_type", "requirement")
    if record_type not in ALLOWED_RECORD_TYPES:
        raise ValueError(f"unsupported record_type: {record_type}")
    status = payload.get("status") or "unknown"
    if status not in ALLOWED_STATUSES:
        raise ValueError(f"unsupported status: {status}")
    if not payload.get("name"):
        raise ValueError("candidate name is required")

    payload["record_type"] = record_type
    payload["status"] = status
    payload["effective_from"] = _iso_date(payload.get("effective_from", ""))
    payload["effective_to"] = _iso_date(payload.get("effective_to", ""))
    if payload["effective_from"] and payload["effective_to"] and payload["effective_from"] > payload["effective_to"]:
        raise ValueError("effective_from cannot be later than effective_to")

    attributes = dict(payload.get("attributes") or {})
    for key in (
        "authority", "applicability_scope", "exceptions", "transition_note",
        "source_url", "legal_effect", "review_basis",
    ):
        if key in edits:
            attributes[key] = str(edits[key] or "").strip()
    payload["attributes"] = attributes
    return payload


def review_task_with_edits(
    store: KnowledgeStore,
    task_id: int,
    *,
    action: str,
    edits: dict[str, Any] | None = None,
    reviewer_note: str = "",
) -> dict[str, Any]:
    with store.lock:
        row = store.conn.execute("SELECT * FROM extraction_tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise ValueError("extraction task not found")
        if row["status"] != "pending":
            raise ValueError(f"extraction task already {row['status']}")
        import json
        payload = normalize_candidate_edits(json.loads(row["candidate_payload"] or "{}"), edits)
        store.conn.execute(
            """UPDATE extraction_tasks
               SET candidate_type=?,candidate_name=?,candidate_code=?,region_code=?,region_name=?,
                   product_class=?,candidate_payload=?,reviewer_note=?
               WHERE id=?""",
            (
                payload["record_type"], payload["name"], payload.get("code", ""),
                payload.get("region_code", ""), payload.get("region_name", ""),
                payload.get("product_class", ""), json.dumps(payload, ensure_ascii=False),
                reviewer_note, task_id,
            ),
        )
        store.conn.commit()
    result = store.review_extraction_task(task_id, action=action, reviewer_note=reviewer_note)
    result["reviewed_payload"] = payload
    return result


def lifecycle_state(record: dict[str, Any], *, as_of: str | None = None) -> str:
    on_date = date.fromisoformat(as_of) if as_of else date.today()
    status = str(record.get("status") or "unknown").lower()
    if status in {"repealed", "withdrawn", "superseded"}:
        return status
    start = str(record.get("effective_from") or "")
    end = str(record.get("effective_to") or "")
    if start:
        try:
            if date.fromisoformat(start) > on_date:
                return "future"
        except ValueError:
            return "date_invalid"
    if end:
        try:
            if date.fromisoformat(end) < on_date:
                return "expired"
        except ValueError:
            return "date_invalid"
    if status == "transition":
        return "transition"
    if status == "active":
        return "active"
    return "unknown"


def _matches_product_class(record: dict[str, Any], product_class: str) -> bool:
    target = (product_class or "").strip().lower()
    actual = (record.get("product_class") or "").strip().lower()
    if not target or not actual or actual == "*":
        return True
    return target in actual or actual in target


def world_map_v2(store: KnowledgeStore, *, include_demo: bool = False, as_of: str | None = None) -> dict[str, Any]:
    records = store.list_compliance_records(review_status="approved")
    demo_data = False
    if include_demo and not records:
        records = [dict(item) for item in DEMO_RECORDS]
        demo_data = True

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for source in records:
        item = dict(source)
        item["lifecycle_state"] = lifecycle_state(item, as_of=as_of)
        grouped[(item.get("region_code") or "UNSPECIFIED", item.get("region_name") or "未标注地区")].append(item)

    regions = []
    for (code, name), items in sorted(grouped.items()):
        states = Counter(item["lifecycle_state"] for item in items)
        types = Counter(item["record_type"] for item in items)
        evidence = sum(1 for item in items if item.get("source_document_id") and item.get("source_chunk_id"))
        coord = REGION_COORDINATES.get(code, {})
        regions.append({
            "region_code": code,
            "region_name": name,
            "lat": coord.get("lat"),
            "lon": coord.get("lon"),
            "record_count": len(items),
            "active_count": states.get("active", 0) + states.get("transition", 0),
            "lifecycle_counts": dict(states),
            "type_counts": dict(types),
            "product_classes": sorted({item.get("product_class") for item in items if item.get("product_class")}),
            "evidence_coverage": round(evidence / len(items), 4) if items else 0.0,
            "records": items,
        })
    return {
        "demo_data": demo_data,
        "as_of": as_of or date.today().isoformat(),
        "warning": "当前为演示结构数据，不代表真实法规或认证结论。" if demo_data else "",
        "regions": regions,
        "summary": {
            "regions": len(regions),
            "records": len(records),
            "active_or_transition": sum(r["active_count"] for r in regions),
            "pending_review": len(store.list_extraction_tasks(status="pending")),
            "evidence_backed": sum(
                1 for item in records if item.get("source_document_id") and item.get("source_chunk_id")
            ),
        },
    }


def analyze_product_access_v2(
    store: KnowledgeStore,
    *,
    product: str,
    product_class: str,
    region_code: str,
    include_demo: bool = False,
    as_of: str | None = None,
) -> dict[str, Any]:
    records = store.list_compliance_records(review_status="approved", region_code=region_code or None)
    demo_data = False
    if include_demo and not records:
        records = [
            dict(item) for item in DEMO_RECORDS
            if not region_code or item.get("region_code") == region_code
        ]
        demo_data = True

    prepared = []
    for source in records:
        if not _matches_product_class(source, product_class):
            continue
        item = dict(source)
        item["lifecycle_state"] = lifecycle_state(item, as_of=as_of)
        attrs = dict(item.get("attributes") or {})
        item["needs_exception_review"] = bool(attrs.get("exceptions"))
        item["applicability_scope"] = attrs.get("applicability_scope", "")
        item["exceptions"] = attrs.get("exceptions", "")
        prepared.append(item)

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in prepared:
        groups[item["record_type"]].append(item)

    current = [x for x in prepared if x["lifecycle_state"] in {"active", "transition"}]
    unknown = [x for x in prepared if x["lifecycle_state"] in {"unknown", "date_invalid", "future"}]
    evidence_backed = sum(1 for x in prepared if x.get("source_document_id") and x.get("source_chunk_id"))
    exception_review = sum(1 for x in prepared if x["needs_exception_review"])

    if not prepared:
        status = "insufficient_data"
    elif not current or unknown:
        status = "needs_version_review"
    elif exception_review:
        status = "needs_applicability_review"
    else:
        status = "evidence_ready"

    return {
        "product": product,
        "product_class": product_class,
        "region_code": region_code,
        "as_of": as_of or date.today().isoformat(),
        "status": status,
        "demo_data": demo_data,
        "summary": {
            "matched_records": len(prepared),
            "currently_effective": len(current),
            "unknown_or_future": len(unknown),
            "evidence_backed": evidence_backed,
            "exception_review": exception_review,
            "regulations": len(groups.get("regulation", [])),
            "standards": len(groups.get("standard", [])),
            "certifications": len(groups.get("certification", [])),
            "requirements": len(groups.get("requirement", [])),
            "test_items": len(groups.get("test_item", [])),
        },
        "groups": dict(groups),
        "decision_boundary": (
            "结果只表示已审核知识在指定日期下的版本/证据状态；"
            "例外条件、产品实际参数和认证机构判定仍需人工确认，不自动给出法律合规结论。"
        ),
        "next_action": (
            "请先导入并审核该地区/产品分类的法规认证资料。"
            if status == "insufficient_data"
            else "优先处理版本未知、例外条款和证据缺口，再形成正式准入结论。"
            if status != "evidence_ready"
            else "当前知识证据较完整，可进入专家复核与正式准入报告流程。"
        ),
    }
