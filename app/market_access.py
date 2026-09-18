from __future__ import annotations

from datetime import date
from typing import Any

from app.governance import analyze_product_access_v2
from app.graph_governance import GraphGovernanceService
from app.regions import REGION_META, canonical_region_code
from app.store import KnowledgeStore


STAGE_ORDER = (
    ("regulation", "法规"),
    ("standard", "标准"),
    ("certification", "认证"),
    ("requirement", "技术要求"),
    ("test_item", "检测项目"),
)


class MarketAccessService:
    """Organize approved knowledge into a GMA market-access business view.

    GMA is intentionally an orchestration layer. It does not create a second
    copy of regulation/standard/certification facts and never promotes
    unreviewed candidates into the result.
    """

    def __init__(self, store: KnowledgeStore, graph_service: GraphGovernanceService):
        self.store = store
        self.graph_service = graph_service

    @staticmethod
    def _business_item(record: dict[str, Any]) -> dict[str, Any]:
        attrs = dict(record.get("attributes") or {})
        return {
            "id": int(record.get("id") or 0),
            "record_type": record.get("record_type", ""),
            "code": record.get("code", ""),
            "name": record.get("name", ""),
            "region_code": canonical_region_code(str(record.get("region_code") or "")),
            "region_name": record.get("region_name", ""),
            "product_class": record.get("product_class", ""),
            "version": record.get("version", ""),
            "status": record.get("status", ""),
            "lifecycle_state": record.get("lifecycle_state", ""),
            "effective_from": record.get("effective_from", ""),
            "effective_to": record.get("effective_to", ""),
            "authority": attrs.get("authority", ""),
            "applicability_scope": record.get("applicability_scope") or attrs.get("applicability_scope", ""),
            "exceptions": record.get("exceptions") or attrs.get("exceptions", ""),
            "source_filename": record.get("source_filename", ""),
            "source_url": attrs.get("source_url", ""),
            "source_document_id": record.get("source_document_id"),
            "source_chunk_id": record.get("source_chunk_id"),
            "evidence_backed": bool(record.get("source_document_id") and record.get("source_chunk_id")),
        }

    def evaluate(
        self,
        *,
        product: str,
        product_class: str,
        region_code: str,
        as_of: str | None = None,
    ) -> dict[str, Any]:
        effective_date = as_of or date.today().isoformat()
        access = analyze_product_access_v2(
            self.store,
            product=product,
            product_class=product_class,
            region_code=region_code,
            include_demo=False,
            as_of=effective_date,
        )
        paths = self.graph_service.certification_paths(
            region_code=access["region_code"],
            product_class=product_class,
            as_of=effective_date,
        )

        stages = []
        all_items: list[dict[str, Any]] = []
        for key, label in STAGE_ORDER:
            items = [self._business_item(item) for item in access.get("groups", {}).get(key, [])]
            all_items.extend(items)
            stages.append({
                "key": key,
                "label": label,
                "count": len(items),
                "items": items,
            })

        evidence_gaps = [item for item in all_items if not item["evidence_backed"]]
        version_review = [
            item for item in all_items
            if item.get("lifecycle_state") in {"unknown", "date_invalid", "future"}
        ]
        exception_review = [item for item in all_items if item.get("exceptions")]
        empty_stages = [stage["key"] for stage in stages if not stage["items"]]

        path_status = str(paths.get("status") or "")
        if not all_items:
            status = "insufficient_data"
        elif (
            access.get("status") != "evidence_ready"
            or path_status not in {"ready"}
            or evidence_gaps
            or version_review
            or exception_review
        ):
            status = "needs_review"
        else:
            status = "ready"

        requested = canonical_region_code(region_code)
        requested_name = str(REGION_META.get(requested, {}).get("name") or requested or region_code)
        scope_codes = access.get("knowledge_scope_codes", [])
        scope_names = [
            str(REGION_META.get(code, {}).get("name") or code)
            for code in scope_codes
        ]
        source_record_ids = sorted({item["id"] for item in all_items if item["id"]})

        return {
            "product": product,
            "product_class": product_class,
            "region_code": requested or region_code,
            "region_name": requested_name,
            "as_of": effective_date,
            "status": status,
            "knowledge_scope_codes": scope_codes,
            "knowledge_scope_names": scope_names,
            "summary": {
                **dict(access.get("summary") or {}),
                "approved_paths": int((paths.get("summary") or {}).get("paths") or 0),
                "unconnected_targets": int((paths.get("summary") or {}).get("unconnected_targets") or 0),
                "evidence_gaps": len(evidence_gaps),
                "version_review": len(version_review),
                "exception_review": len(exception_review),
            },
            "stages": stages,
            "paths": paths.get("paths", []),
            "path_status": path_status,
            "unconnected_targets": paths.get("unconnected_targets", []),
            "gaps": {
                "empty_stages": empty_stages,
                "evidence": evidence_gaps,
                "version": version_review,
                "exceptions": exception_review,
            },
            "source_record_ids": source_record_ids,
            "organization_principle": (
                "GMA仅按市场准入业务链路组织法规、标准、认证、技术要求和检测项目；"
                "事实数据仍来自前三类正式知识及已确认关系，不复制建立第二套法规事实。"
            ),
            "decision_boundary": (
                "本结果用于组织已审核知识和已确认关系，不自动替代产品参数核验、"
                "例外条件判断、认证机构判定或最终法律合规意见。"
            ),
            "next_action": (
                "当前市场/产品分类尚无足够正式知识，请先补充并审核依据。"
                if status == "insufficient_data"
                else "仍有版本、依据、例外条件或关系连通性需要业务人员复核。"
                if status == "needs_review"
                else "正式知识、依据和已确认准入路径较完整，可进入业务专家复核与准入报告。"
            ),
        }
