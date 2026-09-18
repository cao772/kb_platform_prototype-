from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from typing import Any

from app.change_monitor import ChangeMonitorService
from app.map_service import RegulationMapService
from app.store import KnowledgeStore


class BusinessDashboardService:
    """Read-only management dashboard over governed business data."""

    def __init__(self, store: KnowledgeStore, change_monitor: ChangeMonitorService):
        self.store = store
        self.change_monitor = change_monitor
        self.map_service = RegulationMapService(store)

    def _change_trend(self, *, days: int = 30) -> list[dict[str, Any]]:
        end = date.today()
        start = end - timedelta(days=max(1, days) - 1)
        with self.store.lock:
            try:
                rows = self.store.conn.execute(
                    """SELECT substr(created_at,1,10) day,
                              COUNT(*) total,
                              SUM(CASE WHEN severity='high' THEN 1 ELSE 0 END) high
                       FROM knowledge_change_tasks
                       WHERE substr(created_at,1,10) >= ?
                       GROUP BY substr(created_at,1,10)
                       ORDER BY day""",
                    (start.isoformat(),),
                ).fetchall()
            except Exception:
                rows = []
        by_day = {
            str(row["day"]): {
                "total": int(row["total"] or 0),
                "high": int(row["high"] or 0),
            }
            for row in rows
            if row["day"]
        }
        output = []
        cursor = start
        while cursor <= end:
            item = by_day.get(cursor.isoformat(), {"total": 0, "high": 0})
            output.append({"date": cursor.isoformat(), **item})
            cursor += timedelta(days=1)
        return output

    def snapshot(self, *, as_of: str | None = None) -> dict[str, Any]:
        effective_date = as_of or date.today().isoformat()
        overview = self.map_service.overview(as_of=effective_date)
        summary = dict(overview.get("summary") or {})
        records = self.store.list_compliance_records(review_status="approved", limit=5000)

        type_counts = Counter(str(item.get("record_type") or "unknown") for item in records)
        product_counts = Counter(
            str(item.get("product_class") or "").strip()
            for item in records
            if str(item.get("product_class") or "").strip() not in {"", "*"}
        )
        markets = list(overview.get("target_markets") or [])
        priorities = sorted(
            markets,
            key=lambda item: (
                -int(item.get("high_changes") or 0),
                -int(item.get("pending_changes") or 0),
                -int(item.get("upcoming_effective") or 0),
                -int(item.get("evidence_missing") or 0),
                float(item.get("chain_completeness") or 0),
                str(item.get("region_code") or ""),
            ),
        )[:8]

        coverage_buckets = Counter()
        for market in markets:
            completeness = float(market.get("chain_completeness") or 0)
            if completeness >= 1:
                coverage_buckets["complete"] += 1
            elif completeness > 0:
                coverage_buckets["building"] += 1
            else:
                coverage_buckets["not_started"] += 1

        changes = self.change_monitor.summary()
        return {
            "as_of": effective_date,
            "headline": {
                "target_markets": int(summary.get("target_markets") or len(markets)),
                "markets_with_data": int(summary.get("target_markets_with_data") or 0),
                "complete_markets": int(summary.get("complete_markets") or 0),
                "formal_knowledge": int(summary.get("knowledge") or len(records)),
                "open_changes": int(changes.get("open") or 0),
                "open_high": int(changes.get("open_high") or 0),
                "upcoming_effective": int(summary.get("upcoming_effective") or 0),
                "evidence_missing": int(summary.get("evidence_missing") or 0),
            },
            "knowledge_libraries": [
                {"key": "regulation", "label": "法规知识库", "count": int(type_counts.get("regulation", 0)), "unit": "条"},
                {"key": "standard", "label": "标准知识库", "count": int(type_counts.get("standard", 0)), "unit": "条"},
                {"key": "certification", "label": "认证知识库", "count": int(type_counts.get("certification", 0)), "unit": "条"},
                {
                    "key": "gma",
                    "label": "GMA知识库",
                    "count": int(summary.get("target_markets_with_data") or 0),
                    "unit": "个市场",
                    "note": "按市场组织正式知识，不复制法规事实",
                },
            ],
            "supporting_knowledge": {
                "requirements": int(type_counts.get("requirement", 0)),
                "test_items": int(type_counts.get("test_item", 0)),
            },
            "market_progress": {
                "complete": int(coverage_buckets.get("complete", 0)),
                "building": int(coverage_buckets.get("building", 0)),
                "not_started": int(coverage_buckets.get("not_started", 0)),
                "average_chain_completeness": float(summary.get("average_chain_completeness") or 0),
            },
            "product_coverage": {
                "product_classes": len(product_counts),
                "top": [
                    {"name": name, "count": int(count)}
                    for name, count in product_counts.most_common(8)
                ],
            },
            "priority_markets": priorities,
            "recent_open_changes": self.change_monitor.list_tasks(status="open", limit=6),
            "change_summary": changes,
            "change_trend_30d": self._change_trend(days=30),
            "principle": "驾驶舱仅汇总人工确认后的正式知识、市场准入组织结果和变化待办，不把候选内容计入正式统计。",
        }
