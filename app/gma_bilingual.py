from __future__ import annotations

from typing import Any

from app.formal_translation import FormalKnowledgeTranslationService
from app.graph_governance import GraphGovernanceService
from app.regions import canonical_region_code, knowledge_scope_codes, target_market
from app.store import KnowledgeStore


class GmaBilingualService:
    """Derived GMA/market-access path view over approved knowledge and relations.

    GMA does not duplicate facts. It reuses approved graph paths and attaches the
    independent bilingual display layer of each formal knowledge record.
    """

    def __init__(
        self,
        store: KnowledgeStore,
        graph: GraphGovernanceService,
        formal_translation: FormalKnowledgeTranslationService,
    ):
        self.store = store
        self.graph = graph
        self.formal_translation = formal_translation

    def _node_bilingual(self, node: dict[str, Any]) -> dict[str, Any]:
        record_id = int(node["id"])
        try:
            bilingual = self.formal_translation.detail(record_id, target_language="zh-CN")
            name_field = bilingual.get("fields", {}).get("name", {})
            chinese_name = str(name_field.get("translated") or "")
            translation_status = bilingual.get("review", {}).get("review_status", "draft")
            source_changed = bool(name_field.get("source_changed"))
        except Exception:
            chinese_name = ""
            translation_status = "missing"
            source_changed = False
        return {
            **node,
            "name_original": str(node.get("label") or ""),
            "name_zh": chinese_name,
            "translation_status": translation_status,
            "translation_source_changed": source_changed,
        }

    def path(
        self,
        *,
        region_code: str,
        product_class: str,
        as_of: str | None = None,
    ) -> dict[str, Any]:
        region = canonical_region_code(region_code)
        if not region:
            raise ValueError("region_code is required")
        if not str(product_class or "").strip():
            raise ValueError("product_class is required")

        raw = self.graph.certification_paths(
            region_code=region,
            product_class=product_class,
            as_of=as_of,
        )
        decorated_paths: list[dict[str, Any]] = []
        seen_records: dict[int, dict[str, Any]] = {}
        for path in raw.get("paths", []):
            nodes = [self._node_bilingual(node) for node in path.get("nodes", [])]
            for node in nodes:
                seen_records[int(node["id"])] = node
            decorated_paths.append({**path, "nodes": nodes})

        gaps = [self._node_bilingual(node) for node in raw.get("unconnected_targets", [])]
        for node in gaps:
            seen_records[int(node["id"])] = node

        market = target_market(region)
        translated = sum(1 for node in seen_records.values() if node.get("name_zh"))
        confirmed = sum(
            1 for node in seen_records.values()
            if node.get("translation_status") == "confirmed"
        )
        pending_translation = sum(1 for node in seen_records.values() if not node.get("name_zh"))

        return {
            **raw,
            "region_code": region,
            "region_name": market.name if market else region,
            "knowledge_scope_codes": list(knowledge_scope_codes(region)),
            "paths": decorated_paths,
            "unconnected_targets": gaps,
            "bilingual_summary": {
                "records": len(seen_records),
                "with_chinese": translated,
                "confirmed_chinese": confirmed,
                "pending_chinese": pending_translation,
            },
            "principle": (
                "GMA路径复用已审核法规、标准、认证、要求及已审核关系；"
                "原名称和原始依据保留，中文翻译为独立派生层。"
            ),
        }

    def translate_path(
        self,
        *,
        region_code: str,
        product_class: str,
        as_of: str | None = None,
        force: bool = False,
        max_records: int = 100,
    ) -> dict[str, Any]:
        data = self.path(region_code=region_code, product_class=product_class, as_of=as_of)
        record_ids: list[int] = []
        seen: set[int] = set()
        for path in data.get("paths", []):
            for node in path.get("nodes", []):
                rid = int(node["id"])
                if rid not in seen:
                    seen.add(rid)
                    record_ids.append(rid)
        for node in data.get("unconnected_targets", []):
            rid = int(node["id"])
            if rid not in seen:
                seen.add(rid)
                record_ids.append(rid)
        record_ids = record_ids[: min(max(int(max_records), 1), 500)]

        translated = 0
        failures: list[dict[str, Any]] = []
        for record_id in record_ids:
            try:
                result = self.formal_translation.translate(
                    record_id,
                    target_language="zh-CN",
                    force=force,
                )
                if result.get("fields", {}).get("name", {}).get("translated"):
                    translated += 1
                trace = result.get("translation") or {}
                if trace.get("mode") in {"translation_failed", "translation_not_configured"}:
                    failures.append({"record_id": record_id, "reason": trace.get("reason", trace.get("mode"))})
            except Exception as exc:
                failures.append({"record_id": record_id, "reason": str(exc)})

        result = self.path(region_code=region_code, product_class=product_class, as_of=as_of)
        result["translation_run"] = {
            "requested_records": len(record_ids),
            "with_chinese_after_run": translated,
            "failures": failures,
            "force": bool(force),
        }
        return result
