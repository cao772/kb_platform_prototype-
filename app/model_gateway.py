from __future__ import annotations

import json
import os
from dataclasses import dataclass

from app.graph_context import graph_context_as_prompt


@dataclass
class ModelConfig:
    provider: str
    base_url: str
    model: str
    configured: bool


def current_model_config() -> ModelConfig:
    return ModelConfig(
        provider=os.getenv("KB_LLM_PROVIDER", "openai-compatible"),
        base_url=os.getenv("KB_LLM_BASE_URL", ""),
        model=os.getenv("KB_LLM_MODEL", ""),
        configured=bool(os.getenv("KB_LLM_BASE_URL") and os.getenv("KB_LLM_MODEL")),
    )


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
        "你是企业通用知识库与法规/认证知识库的严谨问答助手。"
        "只能根据提供的文档证据与人工审核通过的图谱事实回答；证据不足时必须说明缺口。"
        "未经人工审核的候选实体或候选关系不得作为事实。"
        "法规/认证问题必须区分产品分类、国家地区、版本状态、生效/废止状态。"
        "图谱关系只能用于解释已审核法规、标准、认证、要求和检测项目之间的关联，不能替代最终法律判断。"
    )
    user = (
        f"查询计划：{json.dumps(query_plan or {}, ensure_ascii=False)}\n"
        f"问题：{question}\n\n"
        f"文档证据：\n{context_text or '[无直接文档召回]'}\n\n"
        f"已审核图谱事实：\n{graph_text or '[无已审核图谱关系]'}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def call_chat_model(messages: list[dict[str, str]]) -> tuple[str | None, dict]:
    """Integration seam for the enterprise model gateway.

    The public prototype deliberately does not embed credentials or invoke an external
    endpoint. Wire the organization gateway here in deployment; until then the agent
    falls back to evidence-extractive answers.
    """
    config = current_model_config()
    return None, {
        "mode": "extractive_fallback",
        "reason": "external model gateway adapter is intentionally disabled in this public prototype",
        "provider": config.provider,
        "model": config.model,
    }
