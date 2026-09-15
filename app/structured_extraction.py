from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from app.compliance import extract_review_candidates
from app.model_gateway import current_model_config
from app.store import KnowledgeStore

ALLOWED_TYPES = {"regulation", "standard", "certification", "requirement", "test_item"}


def _infer_lifecycle(text: str) -> dict[str, str]:
    status = "unknown"
    if any(word in text for word in ("废止", "失效", "撤销")):
        status = "repealed"
    elif any(word in text for word in ("过渡期", "过渡阶段")):
        status = "transition"
    elif any(word in text for word in ("生效", "施行", "有效")):
        status = "active"

    version = ""
    match = re.search(r"(?:版本|版次|修订版)\s*[:：]?\s*([A-Za-z0-9._/-]{1,30})", text, re.IGNORECASE)
    if match:
        version = match.group(1)

    dates = re.findall(r"(20\d{2})[年\-/\.](\d{1,2})[月\-/\.](\d{1,2})日?", text)
    normalized = [f"{int(y):04d}-{int(m):02d}-{int(d):02d}" for y, m, d in dates]
    effective_from = normalized[0] if normalized and any(k in text for k in ("生效", "实施", "施行")) else ""
    effective_to = normalized[-1] if normalized and any(k in text for k in ("截止", "废止", "失效")) else ""
    return {
        "status": status,
        "version": version,
        "effective_from": effective_from,
        "effective_to": effective_to,
    }


def _enrich_rule_tasks(store: KnowledgeStore, document_id: int) -> int:
    changed = 0
    with store.lock:
        rows = store.conn.execute(
            "SELECT id,candidate_payload FROM extraction_tasks WHERE document_id=? AND status='pending'",
            (document_id,),
        ).fetchall()
        for row in rows:
            payload = json.loads(row["candidate_payload"] or "{}")
            excerpt = str((payload.get("attributes") or {}).get("excerpt") or "")
            lifecycle = _infer_lifecycle(excerpt)
            dirty = False
            for key, value in lifecycle.items():
                if value and not payload.get(key):
                    payload[key] = value
                    dirty = True
            if dirty:
                store.conn.execute(
                    "UPDATE extraction_tasks SET candidate_payload=? WHERE id=?",
                    (json.dumps(payload, ensure_ascii=False), row["id"]),
                )
                changed += 1
        store.conn.commit()
    return changed


def _chat_completions_url(base_url: str) -> str:
    value = base_url.rstrip("/")
    if value.endswith("/chat/completions"):
        return value
    if value.endswith("/v1"):
        return value + "/chat/completions"
    return value + "/v1/chat/completions"


def _extract_json_object(text: str) -> dict[str, Any]:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    try:
        data = json.loads(value)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        start, end = value.find("{"), value.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(value[start:end + 1])
            return data if isinstance(data, dict) else {}
        raise


def _call_structured_model(chunks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    config = current_model_config()
    if not config.configured:
        return [], {"mode": "rule_only", "reason": "model gateway not configured"}

    body_chunks = [
        {"chunk_index": item["chunk_index"], "text": item["text"][:5500]}
        for item in chunks[:10]
    ]
    system = (
        "你是法规认证知识治理抽取器。只抽取文本明确表达的事实，不补充常识。"
        "输出严格JSON对象，格式为 {\"candidates\":[...]}。"
        "candidate字段：record_type(regulation|standard|certification|requirement|test_item),"
        "name,code,region_code,region_name,product_class,status(unknown|draft|active|transition|repealed|withdrawn|superseded),"
        "version,effective_from,effective_to,authority,applicability_scope,exceptions,source_chunk_index,confidence。"
        "日期使用YYYY-MM-DD；没有依据的字段留空。"
    )
    user = "请从以下证据分片抽取候选，候选后续还会人工审核：\n" + json.dumps(body_chunks, ensure_ascii=False)
    request_body = json.dumps({
        "model": config.model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("KB_LLM_API_KEY", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(_chat_completions_url(config.base_url), data=request_body, headers=headers, method="POST")
    timeout = float(os.getenv("KB_LLM_TIMEOUT", "60"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        content = payload["choices"][0]["message"]["content"]
        data = _extract_json_object(content)
        candidates = data.get("candidates") if isinstance(data.get("candidates"), list) else []
        return candidates, {
            "mode": "rule_plus_llm",
            "provider": config.provider,
            "model": config.model,
            "candidate_count": len(candidates),
        }
    except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError, json.JSONDecodeError) as exc:
        return [], {"mode": "rule_only_fallback", "reason": str(exc), "model": config.model}


def _create_llm_tasks(store: KnowledgeStore, document_id: int, chunks: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> int:
    by_index = {int(item["chunk_index"]): item for item in chunks}
    created = 0
    for raw in candidates:
        record_type = str(raw.get("record_type") or "")
        if record_type not in ALLOWED_TYPES:
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        try:
            chunk_index = int(raw.get("source_chunk_index"))
        except (TypeError, ValueError):
            continue
        chunk = by_index.get(chunk_index)
        if not chunk:
            continue
        confidence = max(0.0, min(float(raw.get("confidence") or 0.75), 0.99))
        attributes = {
            "excerpt": chunk["text"][:500],
            "extraction_method": "llm_constrained_v1",
            "source_chunk_index": chunk_index,
            "authority": str(raw.get("authority") or ""),
            "applicability_scope": str(raw.get("applicability_scope") or ""),
            "exceptions": str(raw.get("exceptions") or ""),
        }
        candidate_payload = {
            "record_type": record_type,
            "code": str(raw.get("code") or "").strip(),
            "name": name,
            "region_code": str(raw.get("region_code") or "").strip().upper(),
            "region_name": str(raw.get("region_name") or "").strip(),
            "product_class": str(raw.get("product_class") or "").strip(),
            "status": str(raw.get("status") or "unknown").strip(),
            "version": str(raw.get("version") or "").strip(),
            "effective_from": str(raw.get("effective_from") or "").strip(),
            "effective_to": str(raw.get("effective_to") or "").strip(),
            "source_document_id": document_id,
            "source_chunk_id": chunk["id"],
            "attributes": attributes,
        }
        task_id = store.create_extraction_task(
            document_id=document_id,
            chunk_id=chunk["id"],
            candidate_type=record_type,
            candidate_name=name,
            candidate_code=candidate_payload["code"],
            region_code=candidate_payload["region_code"],
            region_name=candidate_payload["region_name"],
            product_class=candidate_payload["product_class"],
            confidence=confidence,
            candidate_payload=candidate_payload,
        )
        if task_id is not None:
            created += 1
    return created


def extract_review_candidates_v2(
    store: KnowledgeStore,
    document_id: int,
    *,
    use_llm: bool = True,
) -> dict[str, Any]:
    rule = extract_review_candidates(store, document_id)
    enriched = _enrich_rule_tasks(store, document_id)
    chunks = store.document_chunks(document_id)
    llm_candidates: list[dict[str, Any]] = []
    llm_trace: dict[str, Any] = {"mode": "disabled"}
    if use_llm:
        llm_candidates, llm_trace = _call_structured_model(chunks)
    llm_created = _create_llm_tasks(store, document_id, chunks, llm_candidates) if llm_candidates else 0
    return {
        "document_id": document_id,
        "rule_created": rule["created"],
        "rule_enriched": enriched,
        "llm_created": llm_created,
        "total_created": rule["created"] + llm_created,
        "llm_trace": llm_trace,
        "note": "规则和模型只生成候选；人工审核通过后才进入正式知识目录。",
    }
