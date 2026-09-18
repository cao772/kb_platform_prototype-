from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import date, timedelta
from typing import Any

from app.governance import lifecycle_state
from app.regions import REGION_META, TARGET_MARKETS, canonical_region_code, knowledge_scope_codes
from app.store import KnowledgeStore

TYPE_LABELS = {
    "regulation": "法规",
    "standard": "标准",
    "certification": "认证",
    "requirement": "技术要求",
    "test_item": "检测项目",
}


def _point(lat: float, lon: float) -> tuple[float, float]:
    # Equirectangular coordinates used by the browser SVG canvas.
    x = (lon + 180.0) / 360.0 * 1000.0
    y = (90.0 - lat) / 180.0 * 500.0
    return round(x, 2), round(y, 2)


def _date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _class_match(value: Any, query: str) -> bool:
    q = str(query or "").strip().lower()
    if not q:
        return True
    text = str(value or "").strip().lower()
    return not text or text == "*" or q in text or text in q


class RegulationMapService:
    """Business map projection over approved formal knowledge and open change tasks."""

    def __init__(self, store: KnowledgeStore):
        self.store = store

    def _records(self, *, product_class: str = "", record_type: str = "", as_of: str | None = None) -> list[dict[str, Any]]:
        effective_date = as_of or date.today().isoformat()
        date.fromisoformat(effective_date)
        records = self.store.list_compliance_records(review_status="approved", limit=5000)
        result = []
        for record in records:
            if product_class and not _class_match(record.get("product_class"), product_class):
                continue
            if record_type and record.get("record_type") != record_type:
                continue
            item = dict(record)
            item["lifecycle_state"] = lifecycle_state(item, as_of=effective_date)
            item["type_label"] = TYPE_LABELS.get(str(item.get("record_type") or ""), str(item.get("record_type") or ""))
            result.append(item)
        return result

    def _open_changes(self, *, product_class: str = "") -> list[dict[str, Any]]:
        with self.store.lock:
            try:
                rows = self.store.conn.execute(
                    """SELECT id,event_type,severity,title,summary,region_code,product_class,
                              existing_record_id,candidate_task_id,created_at
                       FROM knowledge_change_tasks
                       WHERE status='open'
                       ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,id DESC"""
                ).fetchall()
            except sqlite3.OperationalError:
                return []
        items = [dict(row) for row in rows]
        if product_class:
            items = [item for item in items if _class_match(item.get("product_class"), product_class)]
        return items

    def overview(
        self,
        *,
        product_class: str = "",
        record_type: str = "",
        as_of: str | None = None,
        only_changed: bool = False,
    ) -> dict[str, Any]:
        effective_date = as_of or date.today().isoformat()
        as_of_date = date.fromisoformat(effective_date)
        window_end = as_of_date + timedelta(days=90)
        records = self._records(product_class=product_class, record_type=record_type, as_of=effective_date)
        changes = self._open_changes(product_class=product_class)

        by_region: dict[str, dict[str, Any]] = {}
        for record in records:
            code = canonical_region_code(str(record.get("region_code") or ""))
            if not code:
                continue
            meta = REGION_META.get(code, {})
            region = by_region.setdefault(code, {
                "region_code": code,
                "region_name": str(record.get("region_name") or meta.get("name") or code),
                "knowledge_count": 0,
                "type_counts": Counter(),
                "lifecycle_counts": Counter(),
                "evidence_missing": 0,
                "upcoming_effective": 0,
                "expiring_soon": 0,
                "pending_changes": 0,
                "high_changes": 0,
                "product_classes": Counter(),
            })
            region["knowledge_count"] += 1
            region["type_counts"][record.get("record_type") or "unknown"] += 1
            region["lifecycle_counts"][record.get("lifecycle_state") or "unknown"] += 1
            if not (record.get("source_document_id") and record.get("source_chunk_id")):
                region["evidence_missing"] += 1
            start, end = _date(record.get("effective_from")), _date(record.get("effective_to"))
            if start and as_of_date < start <= window_end:
                region["upcoming_effective"] += 1
            if end and as_of_date <= end <= window_end:
                region["expiring_soon"] += 1
            pc = str(record.get("product_class") or "").strip()
            if pc and pc != "*":
                region["product_classes"][pc] += 1

        for change in changes:
            code = canonical_region_code(str(change.get("region_code") or ""))
            if not code:
                continue
            meta = REGION_META.get(code, {})
            region = by_region.setdefault(code, {
                "region_code": code,
                "region_name": str(meta.get("name") or code),
                "knowledge_count": 0,
                "type_counts": Counter(),
                "lifecycle_counts": Counter(),
                "evidence_missing": 0,
                "upcoming_effective": 0,
                "expiring_soon": 0,
                "pending_changes": 0,
                "high_changes": 0,
                "product_classes": Counter(),
            })
            region["pending_changes"] += 1
            if change.get("severity") == "high":
                region["high_changes"] += 1

        regions = []
        for code, region in by_region.items():
            if only_changed and not region["pending_changes"]:
                continue
            meta = REGION_META.get(code)
            if meta:
                x, y = _point(float(meta["lat"]), float(meta["lon"]))
            else:
                x = y = None
            if region["high_changes"]:
                attention = "high"
            elif region["pending_changes"] or region["upcoming_effective"] or region["expiring_soon"] or region["evidence_missing"]:
                attention = "attention"
            else:
                attention = "stable"
            regions.append({
                **region,
                "type_counts": dict(region["type_counts"]),
                "lifecycle_counts": dict(region["lifecycle_counts"]),
                "product_classes": [name for name, _ in region["product_classes"].most_common(8)],
                "x": x,
                "y": y,
                "mappable": x is not None and y is not None,
                "attention": attention,
            })
        regions.sort(key=lambda item: (-int(item["pending_changes"]), -int(item["knowledge_count"]), item["region_code"]))

        target_markets = []
        for market in TARGET_MARKETS:
            scope_codes = set(knowledge_scope_codes(market.code))
            scoped_records = [
                item for item in records
                if canonical_region_code(str(item.get("region_code") or "")) in scope_codes
            ]
            scoped_changes = [
                item for item in changes
                if canonical_region_code(str(item.get("region_code") or "")) in scope_codes
            ]
            if only_changed and not scoped_changes:
                continue
            scoped_types = Counter(item.get("record_type") or "unknown" for item in scoped_records)
            scoped_lifecycle = Counter(item.get("lifecycle_state") or "unknown" for item in scoped_records)
            x, y = _point(market.lat, market.lon)
            high_changes = sum(1 for item in scoped_changes if item.get("severity") == "high")
            upcoming_effective = 0
            expiring_soon = 0
            evidence_missing_market = 0
            for item in scoped_records:
                if not (item.get("source_document_id") and item.get("source_chunk_id")):
                    evidence_missing_market += 1
                start_date, end_date = _date(item.get("effective_from")), _date(item.get("effective_to"))
                if start_date and as_of_date < start_date <= window_end:
                    upcoming_effective += 1
                if end_date and as_of_date <= end_date <= window_end:
                    expiring_soon += 1
            if high_changes:
                attention = "high"
            elif scoped_changes or upcoming_effective or expiring_soon or evidence_missing_market:
                attention = "attention"
            elif scoped_records:
                attention = "stable"
            else:
                attention = "not_started"
            target_markets.append({
                "region_code": market.code,
                "region_name": market.name,
                "area": market.area,
                "shared_scopes": list(market.shared_scopes),
                "knowledge_scope_codes": list(knowledge_scope_codes(market.code)),
                "knowledge_count": len(scoped_records),
                "type_counts": dict(scoped_types),
                "lifecycle_counts": dict(scoped_lifecycle),
                "pending_changes": len(scoped_changes),
                "high_changes": high_changes,
                "evidence_missing": evidence_missing_market,
                "upcoming_effective": upcoming_effective,
                "expiring_soon": expiring_soon,
                "x": x,
                "y": y,
                "mappable": True,
                "attention": attention,
                "has_data": bool(scoped_records or scoped_changes),
            })

        type_counts = Counter(record.get("record_type") or "unknown" for record in records)
        lifecycle_counts = Counter(record.get("lifecycle_state") or "unknown" for record in records)
        evidence_missing = sum(1 for record in records if not (record.get("source_document_id") and record.get("source_chunk_id")))
        upcoming = sum(region["upcoming_effective"] for region in regions)
        expiring = sum(region["expiring_soon"] for region in regions)
        return {
            "as_of": effective_date,
            "filters": {"product_class": product_class, "record_type": record_type, "only_changed": bool(only_changed)},
            "summary": {
                "regions": len(regions),
                "target_markets": len(TARGET_MARKETS),
                "target_markets_with_data": sum(1 for item in target_markets if item["has_data"]),
                "knowledge": len(records),
                "active": lifecycle_counts.get("active", 0),
                "transition": lifecycle_counts.get("transition", 0),
                "pending_changes": len(changes),
                "high_changes": sum(1 for item in changes if item.get("severity") == "high"),
                "upcoming_effective": upcoming,
                "expiring_soon": expiring,
                "evidence_missing": evidence_missing,
                "type_counts": dict(type_counts),
            },
            "regions": regions,
            "target_markets": target_markets,
        }

    def detail(self, region_code: str, *, product_class: str = "", as_of: str | None = None) -> dict[str, Any]:
        code = canonical_region_code(region_code)
        if not code:
            raise ValueError("region is required")
        effective_date = as_of or date.today().isoformat()
        all_records = self._records(product_class=product_class, as_of=effective_date)
        scope_codes = set(knowledge_scope_codes(code))
        records = [
            item for item in all_records
            if canonical_region_code(str(item.get("region_code") or "")) in scope_codes
        ]
        changes = [
            item for item in self._open_changes(product_class=product_class)
            if canonical_region_code(str(item.get("region_code") or "")) in scope_codes
        ]
        type_counts = Counter(item.get("record_type") or "unknown" for item in records)
        lifecycle_counts = Counter(item.get("lifecycle_state") or "unknown" for item in records)
        product_classes = Counter(str(item.get("product_class") or "").strip() for item in records if str(item.get("product_class") or "").strip() not in {"", "*"})
        records.sort(key=lambda item: (
            0 if item.get("lifecycle_state") in {"active", "transition"} else 1,
            str(item.get("record_type") or ""), str(item.get("code") or item.get("name") or ""),
        ))
        meta = REGION_META.get(code, {})
        region_name = str(meta.get("name") or "") or next(
            (str(item.get("region_name") or "") for item in records if item.get("region_name")), ""
        ) or code
        return {
            "as_of": effective_date,
            "region_code": code,
            "region_name": region_name,
            "knowledge_scope_codes": sorted(scope_codes),
            "summary": {
                "knowledge": len(records),
                "pending_changes": len(changes),
                "high_changes": sum(1 for item in changes if item.get("severity") == "high"),
                "type_counts": dict(type_counts),
                "lifecycle_counts": dict(lifecycle_counts),
                "product_classes": [{"name": name, "count": count} for name, count in product_classes.most_common(12)],
                "evidence_missing": sum(1 for item in records if not (item.get("source_document_id") and item.get("source_chunk_id"))),
            },
            "records": records[:120],
            "changes": changes[:80],
        }
