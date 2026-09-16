from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import date, timedelta
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

# Common market coordinates. EU is represented by Brussels because the platform
# treats EU-wide requirements as a first-class market scope rather than a country.
REGION_META: dict[str, dict[str, Any]] = {
    "EU": {"name": "欧盟", "lat": 50.85, "lon": 4.35},
    "US": {"name": "美国", "lat": 38.0, "lon": -97.0},
    "CN": {"name": "中国", "lat": 35.0, "lon": 103.0},
    "UK": {"name": "英国", "lat": 54.5, "lon": -3.0},
    "JP": {"name": "日本", "lat": 36.2, "lon": 138.2},
    "KR": {"name": "韩国", "lat": 36.3, "lon": 127.8},
    "CA": {"name": "加拿大", "lat": 56.1, "lon": -106.3},
    "AU": {"name": "澳大利亚", "lat": -25.3, "lon": 133.8},
    "IN": {"name": "印度", "lat": 22.6, "lon": 79.0},
    "SG": {"name": "新加坡", "lat": 1.35, "lon": 103.82},
    "MY": {"name": "马来西亚", "lat": 4.2, "lon": 102.0},
    "TH": {"name": "泰国", "lat": 15.8, "lon": 101.0},
    "VN": {"name": "越南", "lat": 16.0, "lon": 108.0},
    "ID": {"name": "印度尼西亚", "lat": -2.5, "lon": 118.0},
    "PH": {"name": "菲律宾", "lat": 12.9, "lon": 121.8},
    "MX": {"name": "墨西哥", "lat": 23.6, "lon": -102.5},
    "BR": {"name": "巴西", "lat": -10.0, "lon": -55.0},
    "AR": {"name": "阿根廷", "lat": -34.0, "lon": -64.0},
    "CL": {"name": "智利", "lat": -30.0, "lon": -71.0},
    "ZA": {"name": "南非", "lat": -30.6, "lon": 22.9},
    "AE": {"name": "阿联酋", "lat": 24.3, "lon": 54.4},
    "SA": {"name": "沙特阿拉伯", "lat": 24.0, "lon": 45.0},
    "TR": {"name": "土耳其", "lat": 39.0, "lon": 35.2},
    "RU": {"name": "俄罗斯", "lat": 61.5, "lon": 105.3},
    "DE": {"name": "德国", "lat": 51.1, "lon": 10.4},
    "FR": {"name": "法国", "lat": 46.2, "lon": 2.2},
    "IT": {"name": "意大利", "lat": 42.8, "lon": 12.5},
    "ES": {"name": "西班牙", "lat": 40.4, "lon": -3.7},
    "NL": {"name": "荷兰", "lat": 52.1, "lon": 5.3},
    "BE": {"name": "比利时", "lat": 50.8, "lon": 4.7},
    "CH": {"name": "瑞士", "lat": 46.8, "lon": 8.2},
    "NO": {"name": "挪威", "lat": 61.0, "lon": 8.0},
    "SE": {"name": "瑞典", "lat": 62.0, "lon": 15.0},
    "PL": {"name": "波兰", "lat": 52.1, "lon": 19.1},
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
            code = str(record.get("region_code") or "").strip().upper()
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
            code = str(change.get("region_code") or "").strip().upper()
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
        }

    def detail(self, region_code: str, *, product_class: str = "", as_of: str | None = None) -> dict[str, Any]:
        code = str(region_code or "").strip().upper()
        if not code:
            raise ValueError("region is required")
        effective_date = as_of or date.today().isoformat()
        all_records = self._records(product_class=product_class, as_of=effective_date)
        records = [item for item in all_records if str(item.get("region_code") or "").strip().upper() == code]
        changes = [item for item in self._open_changes(product_class=product_class) if str(item.get("region_code") or "").strip().upper() == code]
        type_counts = Counter(item.get("record_type") or "unknown" for item in records)
        lifecycle_counts = Counter(item.get("lifecycle_state") or "unknown" for item in records)
        product_classes = Counter(str(item.get("product_class") or "").strip() for item in records if str(item.get("product_class") or "").strip() not in {"", "*"})
        records.sort(key=lambda item: (
            0 if item.get("lifecycle_state") in {"active", "transition"} else 1,
            str(item.get("record_type") or ""), str(item.get("code") or item.get("name") or ""),
        ))
        meta = REGION_META.get(code, {})
        region_name = next((str(item.get("region_name") or "") for item in records if item.get("region_name")), "") or str(meta.get("name") or code)
        return {
            "as_of": effective_date,
            "region_code": code,
            "region_name": region_name,
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
