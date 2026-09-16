from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import date
from typing import Any

from app.governance import lifecycle_state
from app.store import KnowledgeStore

TYPE_LABELS = {
    "regulation": "法规",
    "standard": "标准",
    "certification": "认证",
    "requirement": "技术要求",
    "test_item": "检测项目",
}


def _normalize_record(record: dict[str, Any], *, as_of: str | None = None) -> dict[str, Any]:
    item = dict(record)
    attrs = dict(item.get("attributes") or {})
    item["lifecycle_state"] = lifecycle_state(item, as_of=as_of)
    item["type_label"] = TYPE_LABELS.get(str(item.get("record_type") or ""), str(item.get("record_type") or ""))
    item["authority"] = str(attrs.get("authority") or "")
    item["applicability_scope"] = str(attrs.get("applicability_scope") or "")
    item["exceptions"] = str(attrs.get("exceptions") or "")
    item["transition_note"] = str(attrs.get("transition_note") or "")
    item["source_url"] = str(attrs.get("source_url") or "")
    item["legal_effect"] = str(attrs.get("legal_effect") or "")
    item["review_basis"] = str(attrs.get("review_basis") or "")
    item["evidence_backed"] = bool(item.get("source_document_id") and item.get("source_chunk_id"))
    return item


def _matches_query(item: dict[str, Any], query: str) -> bool:
    q = (query or "").strip().lower()
    if not q:
        return True
    values = (
        item.get("code"), item.get("name"), item.get("version"), item.get("region_code"),
        item.get("region_name"), item.get("product_class"), item.get("authority"),
        item.get("applicability_scope"), item.get("exceptions"), item.get("source_filename"),
    )
    return q in " ".join(str(value or "") for value in values).lower()


def list_catalog(
    store: KnowledgeStore,
    *,
    record_type: str = "",
    region_code: str = "",
    product_class: str = "",
    lifecycle: str = "",
    query: str = "",
    evidence: str = "",
    as_of: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    effective_date = as_of or date.today().isoformat()
    # Validate the date early so the API returns a clear error.
    date.fromisoformat(effective_date)

    raw = store.list_compliance_records(review_status="approved", limit=5000)
    all_items = [_normalize_record(item, as_of=effective_date) for item in raw]

    type_counts = Counter(item.get("record_type") or "unknown" for item in all_items)
    lifecycle_counts = Counter(item.get("lifecycle_state") or "unknown" for item in all_items)
    region_counts = Counter(item.get("region_code") or "UNSPECIFIED" for item in all_items)
    evidence_count = sum(1 for item in all_items if item.get("evidence_backed"))

    items = all_items
    if record_type:
        items = [item for item in items if item.get("record_type") == record_type]
    if region_code:
        rc = region_code.strip().upper()
        items = [item for item in items if str(item.get("region_code") or "").upper() == rc]
    if product_class:
        pc = product_class.strip().lower()
        items = [item for item in items if pc in str(item.get("product_class") or "").lower()]
    if lifecycle:
        items = [item for item in items if item.get("lifecycle_state") == lifecycle]
    if evidence == "with":
        items = [item for item in items if item.get("evidence_backed")]
    elif evidence == "without":
        items = [item for item in items if not item.get("evidence_backed")]
    if query:
        items = [item for item in items if _matches_query(item, query)]

    items.sort(key=lambda item: (
        str(item.get("region_code") or ""),
        str(item.get("record_type") or ""),
        str(item.get("code") or item.get("name") or ""),
        str(item.get("effective_from") or ""),
    ))
    total_filtered = len(items)
    items = items[: max(1, min(int(limit), 1000))]

    return {
        "as_of": effective_date,
        "summary": {
            "total": len(all_items),
            "filtered": total_filtered,
            "evidence_backed": evidence_count,
            "evidence_coverage": round(evidence_count / len(all_items), 4) if all_items else 0.0,
            "type_counts": dict(type_counts),
            "lifecycle_counts": dict(lifecycle_counts),
            "region_counts": dict(region_counts),
        },
        "filters": {
            "record_type": record_type,
            "region_code": region_code,
            "product_class": product_class,
            "lifecycle": lifecycle,
            "query": query,
            "evidence": evidence,
        },
        "items": items,
    }


def catalog_detail(store: KnowledgeStore, record_id: int, *, as_of: str | None = None) -> dict[str, Any]:
    effective_date = as_of or date.today().isoformat()
    records = store.list_compliance_records(review_status="approved", limit=5000)
    selected = next((item for item in records if int(item.get("id") or 0) == int(record_id)), None)
    if not selected:
        raise ValueError("knowledge record not found")
    item = _normalize_record(selected, as_of=effective_date)

    code = str(item.get("code") or "").strip()
    name = str(item.get("name") or "").strip()
    versions = []
    for candidate in records:
        if candidate.get("record_type") != item.get("record_type"):
            continue
        same_identity = bool(code and str(candidate.get("code") or "").strip().lower() == code.lower())
        if not code:
            same_identity = str(candidate.get("name") or "").strip().lower() == name.lower()
        if not same_identity:
            continue
        if str(candidate.get("region_code") or "") != str(item.get("region_code") or ""):
            continue
        versions.append(_normalize_record(candidate, as_of=effective_date))
    versions.sort(key=lambda value: (str(value.get("effective_from") or ""), str(value.get("version") or ""), int(value.get("id") or 0)))

    excerpt = ""
    if item.get("source_chunk_id"):
        with store.lock:
            row = store.conn.execute("SELECT text FROM chunks WHERE id=?", (item["source_chunk_id"],)).fetchone()
        excerpt = str(row["text"] if row else "")[:1600]

    relations: list[dict[str, Any]] = []
    try:
        with store.lock:
            rows = store.conn.execute(
                """SELECT g.id,g.relation_type,g.source_record_id,g.target_record_id,
                          s.code source_code,s.name source_name,s.record_type source_type,
                          t.code target_code,t.name target_name,t.record_type target_type
                   FROM graph_relations g
                   JOIN compliance_records s ON s.id=g.source_record_id
                   JOIN compliance_records t ON t.id=g.target_record_id
                   WHERE g.status='approved' AND (g.source_record_id=? OR g.target_record_id=?)
                   ORDER BY g.id""",
                (record_id, record_id),
            ).fetchall()
        relation_labels = {"REFERENCES": "引用", "REQUIRES": "要求", "REPLACED_BY": "被替代"}
        relations = [
            {**dict(row), "relation_label": relation_labels.get(row["relation_type"], row["relation_type"])}
            for row in rows
        ]
    except sqlite3.OperationalError:
        relations = []

    return {
        "as_of": effective_date,
        "item": item,
        "version_history": versions,
        "relations": relations,
        "evidence_excerpt": excerpt,
    }
