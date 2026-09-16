from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from app.graph_context import graph_context_as_prompt
from app.runtime_settings import MODEL_LABELS, model_profile


@dataclass
class ModelConfig:
    purpose: str
    provider: str
    base_url: str
    model: str
    api_key: str
    timeout_seconds: float
    enabled: bool
    configured: bool


def current_model_config(purpose: str = "qa") -> ModelConfig:
    profile = model_profile(purpose)
    return ModelConfig(
        purpose=purpose,
        provider=str(profile.get("provider") or "openai-compatible"),
        base_url=str(profile.get("base_url") or ""),
        model=str(profile.get("model") or ""),
        api_key=str(profile.get("api_key") or ""),
        timeout_seconds=float(profile.get("timeout_seconds") or 60),
        enabled=bool(profile.get("enabled")),
        configured=bool(profile.get("configured")),
    )


def _chat_completions_url(base_url: str) -> str:
    value = base_url.rstrip("/")
    if value.endswith("/chat/completions"):
        return value
    if value.endswith("/v1"):
        return value + "/chat/completions"
    return value + "/v1/chat/completions"


def _request_chat(config: ModelConfig, messages: list[dict[str, Any]], *, temperature: float = 0) -> tuple[str, dict[str, Any]]:
    if not config.configured:
        raise ValueError(f"{MODEL_LABELS.get(config.purpose, config.purpose)}未配置或未启用")
    payload = json.dumps({
        "model": config.model,
        "messages": messages,
        "temperature": temperature,
    }, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    req = urllib.request.Request(_chat_completions_url(config.base_url), data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=config.timeout_seconds) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = data["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "\n".join(str(item.get("text") or "") if isinstance(item, dict) else str(item) for item in content)
    return str(content or "").strip(), {
        "mode": "model",
        "purpose": config.purpose,
        "provider": config.provider,
        "model": config.model,
    }


def test_model_connection(purpose: str) -> dict[str, Any]:
    config = current_model_config(purpose)
    if not config.configured:
        return {
            "ok": False,
            "purpose": purpose,
            "label": MODEL_LABELS.get(purpose, purpose),
            "reason": "请先启用并填写服务地址和模型名称",
        }
    try:
        text, trace = _request_chat(
            config,
            [
                {"role": "system", "content": "你正在执行模型连接测试。"},
                {"role": "user", "content": "仅回复：连接成功"},
            ],
        )
        return {"ok": True, "purpose": purpose, "label": MODEL_LABELS.get(purpose, purpose), "reply": text[:120], **trace}
    except Exception as exc:
        return {
            "ok": False,
            "purpose": purpose,
            "label": MODEL_LABELS.get(purpose, purpose),
            "reason": str(exc),
            "error_type": type(exc).__name__,
            "model": config.model,
        }


def build_rag_prompt(
    question: str,
    contexts: list[dict],
    *,
    query_plan: dict | None = None,
    graph_context: dict | None = None,
) -> list[dict[str, str]]:
    context_text = "\n\n".join(
        f"[来源{i}] 标题：{item['title']}；文件：{item['filename']}；分片：{item['chunk_index']}；证据ID：doc-{item.get('document_id')}-chunk-{item.get('chunk_id')}\n{item['text']}"
        for i, item in enumerate(contexts, start=1)
    )
    graph_text = graph_context_as_prompt(graph_context or {})
    system = (
        "你是企业法规认证知识平台的严谨问答助手。"
        "只能根据提供的文档证据与人工确认的知识关系回答；证据不足时必须说明缺口。"
        "未经人工确认的候选内容不得作为事实。"
        "法规认证问题必须区分产品分类、国家地区、版本状态、生效和废止状态。"
    )
    user = (
        f"查询计划：{json.dumps(query_plan or {}, ensure_ascii=False)}\n"
        f"问题：{question}\n\n"
        f"文档依据：\n{context_text or '[无直接文档召回]'}\n\n"
        f"已确认知识关系：\n{graph_text or '[无已确认关系]'}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def call_chat_model(messages: list[dict[str, str]]) -> tuple[str | None, dict]:
    config = current_model_config("qa")
    if not config.configured:
        return None, {
            "mode": "extractive_fallback",
            "reason": "法规问答模型未配置或未启用",
            "provider": config.provider,
            "model": config.model,
        }
    try:
        return _request_chat(config, messages)
    except (urllib.error.URLError, TimeoutError, KeyError, IndexError, ValueError, json.JSONDecodeError) as exc:
        return None, {
            "mode": "extractive_fallback",
            "reason": str(exc),
            "error_type": type(exc).__name__,
            "provider": config.provider,
            "model": config.model,
        }


def call_vision_ocr(image_bytes: bytes, mime_type: str, *, instruction: str | None = None) -> tuple[str | None, dict[str, Any]]:
    config = current_model_config("vision")
    if not config.configured:
        return None, {"mode": "vision_not_configured", "model": config.model}
    data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": instruction or (
                        "请识别图片中的法规、标准、认证或技术资料文字。"
                        "尽量保持原有段落、编号、表格行列关系；无法确认的内容不要猜测。"
                    ),
                },
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }
    ]
    try:
        return _request_chat(config, messages)
    except Exception as exc:
        return None, {
            "mode": "vision_failed",
            "reason": str(exc),
            "error_type": type(exc).__name__,
            "model": config.model,
        }
