from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any

from app.store import KnowledgeStore

REGION_ALIASES = {
    "欧盟": ("EU", "欧盟"),
    "欧洲联盟": ("EU", "欧盟"),
    "美国": ("US", "美国"),
    "中国": ("CN", "中国"),
    "英国": ("UK", "英国"),
    "日本": ("JP", "日本"),
    "韩国": ("KR", "韩国"),
    "加拿大": ("CA", "加拿大"),
    "澳大利亚": ("AU", "澳大利亚"),
    "印度": ("IN", "印度"),
}

TYPE_KEYWORDS = {
    "regulation": ("法规", "条例", "法令", "规章"),
    "standard": ("标准", "规范"),
    "certification": ("认证", "证书", "准入"),
    "requirement": ("要求", "义务", "限制", "禁止"),
    "test_item": ("检测", "测试", "试验"),
}

TYPE_LABELS = {
    "regulation": "法规",
    "standard": "标准",
    "certification": "认证",
    "requirement": "要求",
    "test_item": "检测项目",
}

# Only for UI structure demonstration. These records are never persisted and never used
# for formal answers unless the caller explicitly requests include_demo=true.
DEMO_RECORDS = (
    {"id": "demo-eu-reg", "record_type": "regulation", "code": "DEMO-EU-REG-001", "name": "欧盟示例法规节点", "region_code": "EU", "region_name": "欧盟", "product_class": "家用电器（演示）", "status": "active", "version": "demo", "source_document_id": None, "source_chunk_id": None, "review_status": "demo"},
    {"id": "demo-eu-cert", "record_type": "certification", "code": "DEMO-EU-CERT-001", "name": "欧盟示例认证节点", "region_code": "EU", "region_name": "欧盟", "product_class": "家用电器（演示）", "status": "active", "version": "demo", "source_document_id": None, "source_chunk_id": None, "review_status": "demo"},
    {"id": "demo-us-reg", "record_type": "regulation", "code": "DEMO-US-REG-001", "name": "美国示例法规节点", "region_code": "US", "region_name": "美国", "product_class": "家用电器（演示）", "status": "active", "version": "demo", "source_document_id": None, "source_chunk_id": None, "review_status": "demo"},
    {"id": "demo-cn-std", "record_type": "standard", "code": "DEMO-CN-STD-001", "name": "中国示例标准节点", "region_code": "CN", "region_name": "中国", "product_class": "家用电器（演示）", "status": "active", "version": "demo", "source_document_id": None, "source_chunk_id": None, "review_status": "demo"},
)


def _region_from_text(text: str) -> tuple[str, str]:
    for alias, value in REGION_ALIASES.items():
        if alias in text:
            return value
    return "", ""


def _product_class_from_text(text: str) -> str:
    match = re.search(r"(?:产品分类|产品类别|适用产品)\s*[:：]\s*([^\n，。；;]{2,40})", text)
    return match.group(1).strip() if match else ""


def _candidate_code(text: str) -> str:
    patterns = (
        r"\b(?:GB/T|GB|IEC|ISO|EN|EU|UL|ASTM|CSA)[\s-]*[A-Z0-9][A-Z0-9./():-]{1,32}\b",
        r"\b[A-Z]{2,8}-\d{2,}[A-Z0-9./-]*\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(0).strip()
    return ""


def _candidate_name(text: str, keyword: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip(" -—:：；;")
    if len(normalized) <= 90:
        return normalized
    pos = normalized.find(keyword)
    start = max(0, pos - 30)
    return normalized[start : start + 90] + "…"


def extract_review_candidates(store: KnowledgeStore, document_id: int) -> dict[str, Any]:
    """Create conservative, auditable review tasks from one ingested document."""
    chunks = store.document_chunks(document_id)
    created_ids: list[int] = []
    for chunk in chunks:
        text = chunk["text"]
        region_code, region_name = _region_from_text(text)
        product_class = _product_class_from_text(text)
        code = _candidate_code(text)
        segments = [part.strip() for part in re.split(r"[\n。；;]", text) if part.strip()]
        for segment in segments:
            for candidate_type, keywords in TYPE_KEYWORDS.items():
                keyword = next((item for item in keywords if item in segment), None)
                if not keyword:
                    continue
                seg_region_code, seg_region_name = _region_from_text(segment)
                payload = {
                    "record_type": candidate_type,
                    "code": _candidate_code(segment) or code,
                    "name": _candidate_name(segment, keyword),
                    "region_code": seg_region_code or region_code,
                    "region_name": seg_region_name or region_name,
                    "product_class": _product_class_from_text(segment) or product_class,
                    "status": "unknown",
                    "version": "",
                    "effective_from": "",
                    "effective_to": "",
                    "source_document_id": document_id,
                    "source_chunk_id": chunk["id"],
                    "attributes": {"excerpt": segment[:360], "extraction_method": "rule_constrained_v1", "source_chunk_index": chunk["chunk_index"]},
                }
                confidence = 0.62 + (0.1 if payload["region_code"] else 0) + (0.12 if payload["code"] else 0) + (0.08 if payload["product_class"] else 0)
                task_id = store.create_extraction_task(document_id=document_id, chunk_id=chunk["id"], candidate_type=candidate_type, candidate_name=payload["name"], candidate_code=payload["code"], region_code=payload["region_code"], region_name=payload["region_name"], product_class=payload["product_class"], confidence=min(confidence, 0.95), candidate_payload=payload)
                if task_id is not None:
                    created_ids.append(task_id)
    return {"document_id": document_id, "created": len(created_ids), "task_ids": created_ids, "method": "rule_constrained_v1", "note": "候选关系必须经过人工审核后才进入正式法规/认证知识目录。"}


def build_world_map(store: KnowledgeStore, *, include_demo: bool = False) -> dict[str, Any]:
    records = store.list_compliance_records(review_status="approved")
    demo_data = False
    if include_demo and not records:
        records = [dict(item) for item in DEMO_RECORDS]
        demo_data = True
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[(record.get("region_code") or "UNSPECIFIED", record.get("region_name") or "未标注地区")].append(record)
    regions = []
    for (code, name), items in sorted(grouped.items()):
        type_counts = Counter(item["record_type"] for item in items)
        evidence_count = sum(1 for item in items if item.get("source_document_id") and item.get("source_chunk_id"))
        regions.append({"region_code": code, "region_name": name, "record_count": len(items), "type_counts": dict(type_counts), "product_classes": sorted({item.get("product_class") for item in items if item.get("product_class")}), "evidence_coverage": round(evidence_count / len(items), 4) if items else 0.0, "records": items})
    return {"demo_data": demo_data, "warning": "当前为演示结构数据，不代表真实法规或认证结论。" if demo_data else "", "regions": regions, "summary": {"regions": len(regions), "records": len(records), "pending_review": len(store.list_extraction_tasks(status="pending")), "evidence_backed": sum(1 for item in records if item.get("source_document_id") and item.get("source_chunk_id"))}}


def analyze_product_access(store: KnowledgeStore, *, product: str, product_class: str, region_code: str, include_demo: bool = False) -> dict[str, Any]:
    records = store.list_compliance_records(review_status="approved", region_code=region_code or None)
    demo_data = False
    if include_demo and not records:
        records = [dict(item) for item in DEMO_RECORDS if not region_code or item["region_code"] == region_code]
        demo_data = True
    if product_class:
        normalized = product_class.strip().lower()
        records = [item for item in records if not item.get("product_class") or item.get("product_class") == "*" or normalized in item.get("product_class", "").lower() or item.get("product_class", "").lower() in normalized]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in records:
        groups[item["record_type"]].append(item)
    evidence_backed = sum(1 for item in records if item.get("source_document_id") and item.get("source_chunk_id"))
    status = "insufficient_data" if not records else ("evidence_ready" if evidence_backed == len(records) else "needs_evidence_review")
    return {"product": product, "product_class": product_class, "region_code": region_code, "status": status, "demo_data": demo_data, "summary": {"matched_records": len(records), "evidence_backed": evidence_backed, "regulations": len(groups.get("regulation", [])), "standards": len(groups.get("standard", [])), "certifications": len(groups.get("certification", [])), "requirements": len(groups.get("requirement", [])), "test_items": len(groups.get("test_item", []))}, "groups": {key: value for key, value in groups.items()}, "decision_boundary": "这里只返回已审核知识记录及其证据覆盖情况，不自动给出“已合规/可出口”法律结论。", "next_action": "请先导入并审核该地区/产品分类的法规认证资料。" if status == "insufficient_data" else "核对记录版本、生效状态、适用范围与证据后，再形成正式准入结论。"}
