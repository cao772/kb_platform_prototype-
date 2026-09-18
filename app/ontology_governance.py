from __future__ import annotations

from collections import Counter
from typing import Any

from app.ontology import KNOWLEDGE_DOMAINS, domains_for_record_type, ontology_schema, shape_for
from app.store import KnowledgeStore


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


class OntologyGovernanceService:
    """Business-facing ontology governance over candidate and approved knowledge.

    This service keeps ontology rules explicit and auditable without requiring a
    specific graph database. It can later be exported to RDF/OWL/SHACL or a
    graph backend, while the current platform continues to use governed SQL
    records as the source of truth.
    """

    def __init__(self, store: KnowledgeStore):
        self.store = store

    def validate_payload(self, payload: dict[str, Any], *, formal: bool = False) -> dict[str, Any]:
        record_type = str(payload.get("record_type") or payload.get("candidate_type") or "").strip()
        shape = shape_for(record_type)
        if not shape:
            return {
                "valid": False,
                "record_type": record_type,
                "domains": [],
                "errors": [f"不支持的知识类型：{record_type or '未指定'}"],
                "warnings": [],
                "missing_required": [],
                "missing_recommended": [],
                "completeness": 0.0,
            }

        values = dict(payload)
        attrs = dict(values.get("attributes") or {})
        merged = {**attrs, **values}

        missing_required = [key for key in shape.required if _blank(merged.get(key))]
        missing_recommended = [key for key in shape.recommended if _blank(merged.get(key))]
        errors = [f"缺少必填字段：{key}" for key in missing_required]
        warnings = [f"建议补充：{key}" for key in missing_recommended]

        start = str(merged.get("effective_from") or "")
        end = str(merged.get("effective_to") or "")
        if start and end and start > end:
            errors.append("生效日期不能晚于失效日期")

        if formal and _blank(merged.get("source_document_id")):
            warnings.append("正式知识尚未关联来源文件")
        if formal and _blank(merged.get("source_chunk_id")):
            warnings.append("正式知识尚未关联原文片段")

        total = len(shape.required) + len(shape.recommended)
        filled = total - len(missing_required) - len(missing_recommended)
        completeness = round(filled / total, 4) if total else 1.0

        return {
            "valid": not errors,
            "record_type": record_type,
            "label": shape.label,
            "domains": domains_for_record_type(record_type),
            "errors": errors,
            "warnings": warnings,
            "missing_required": missing_required,
            "missing_recommended": missing_recommended,
            "completeness": completeness,
            "business_key": list(shape.business_key),
        }

    def candidate_queue(self, *, limit: int = 200) -> dict[str, Any]:
        tasks = self.store.list_extraction_tasks(status="pending", limit=limit)
        items = []
        for task in tasks:
            payload = dict(task.get("candidate_payload") or {})
            payload.setdefault("record_type", task.get("candidate_type"))
            payload.setdefault("name", task.get("candidate_name"))
            payload.setdefault("code", task.get("candidate_code"))
            payload.setdefault("region_code", task.get("region_code"))
            payload.setdefault("region_name", task.get("region_name"))
            payload.setdefault("product_class", task.get("product_class"))
            validation = self.validate_payload(payload)
            items.append({
                "task_id": task.get("id"),
                "document_id": task.get("document_id"),
                "document_title": task.get("document_title"),
                "chunk_id": task.get("chunk_id"),
                "record_type": payload.get("record_type"),
                "name": payload.get("name"),
                "code": payload.get("code", ""),
                "region_code": payload.get("region_code", ""),
                "product_class": payload.get("product_class", ""),
                "confidence": task.get("confidence", 0),
                "validation": validation,
            })
        return {
            "items": items,
            "summary": {
                "pending": len(items),
                "valid": sum(1 for item in items if item["validation"]["valid"]),
                "needs_completion": sum(1 for item in items if item["validation"]["missing_recommended"]),
                "invalid": sum(1 for item in items if not item["validation"]["valid"]),
            },
        }

    def coverage(self, *, region_code: str = "", product_class: str = "") -> dict[str, Any]:
        records = self.store.list_compliance_records(
            review_status="approved",
            region_code=region_code or None,
            product_class=product_class or None,
            limit=5000,
        )
        by_type: Counter[str] = Counter()
        by_domain: Counter[str] = Counter()
        missing: Counter[str] = Counter()
        evidence_backed = 0
        valid = 0
        completeness = 0.0

        for record in records:
            record_type = str(record.get("record_type") or "")
            by_type[record_type] += 1
            for domain in domains_for_record_type(record_type):
                by_domain[domain] += 1
            result = self.validate_payload(record, formal=True)
            valid += int(result["valid"])
            completeness += float(result["completeness"])
            for field in result["missing_recommended"]:
                missing[field] += 1
            for field in result["missing_required"]:
                missing[field] += 1
            if record.get("source_document_id") and record.get("source_chunk_id"):
                evidence_backed += 1

        total = len(records)
        domain_rows = []
        for domain in KNOWLEDGE_DOMAINS:
            domain_rows.append({
                **domain.to_dict(),
                "record_count": by_domain.get(domain.key, 0),
            })

        return {
            "region_code": region_code,
            "product_class": product_class,
            "summary": {
                "records": total,
                "valid_records": valid,
                "evidence_backed": evidence_backed,
                "evidence_coverage": round(evidence_backed / total, 4) if total else 0.0,
                "average_completeness": round(completeness / total, 4) if total else 0.0,
                "regions": len({r.get("region_code") for r in records if r.get("region_code")}),
                "product_classes": len({r.get("product_class") for r in records if r.get("product_class")}),
            },
            "by_type": dict(by_type),
            "domains": domain_rows,
            "top_missing_fields": [
                {"field": field, "count": count}
                for field, count in missing.most_common(12)
            ],
        }

    def domain_view(
        self,
        domain_key: str,
        *,
        region_code: str = "",
        product_class: str = "",
        limit: int = 500,
    ) -> dict[str, Any]:
        domain = next((item for item in KNOWLEDGE_DOMAINS if item.key == domain_key), None)
        if not domain:
            raise ValueError(f"unsupported ontology domain: {domain_key}")

        records = self.store.list_compliance_records(
            review_status="approved",
            region_code=region_code or None,
            product_class=product_class or None,
            limit=min(max(int(limit), 1), 2000),
        )
        items = []
        for record in records:
            if record.get("record_type") not in domain.record_types:
                continue
            item = dict(record)
            item["ontology"] = self.validate_payload(item, formal=True)
            items.append(item)
        return {
            "domain": domain.to_dict(),
            "region_code": region_code,
            "product_class": product_class,
            "records": items,
            "summary": {
                "records": len(items),
                "evidence_backed": sum(1 for item in items if item.get("source_document_id") and item.get("source_chunk_id")),
            },
        }

    def blueprint(self) -> dict[str, Any]:
        schema = ontology_schema()
        return {
            "version": schema["version"],
            "principle": schema["principle"],
            "domains": schema["domains"],
            "market_access_path": schema["market_access_path"],
            "business_flow": [
                "来源网站/上传文件",
                "文件解析与证据切分",
                "候选知识识别",
                "本体对象与属性映射",
                "人工业务校核",
                "正式知识目录",
                "关联关系确认",
                "知识图谱",
                "产品准入/GMA/法规问答/法规认证地图/变化待办",
            ],
            "implementation": {
                "source_of_truth": "正式知识目录及已确认关系",
                "gma_strategy": "派生视图，不重复维护法规、标准和认证事实",
                "validation_strategy": "按对象类型校验必填字段、推荐字段、版本、地区、产品和证据完整性",
                "graph_strategy": "只有人工确认关系进入正式知识图谱和路径分析",
            },
        }
