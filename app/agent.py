from __future__ import annotations

from app.compliance import TYPE_LABELS
from app.model_gateway import build_rag_prompt, call_chat_model
from app.query_router import QueryRouter
from app.retrieval import RetrievalPipeline
from app.store import KnowledgeStore
from app.text_processing import cosine_score, tokenize


def _best_excerpt(question: str, text: str, *, max_chars: int = 280) -> str:
    query_tokens = tokenize(question)
    sentences = [part.strip() for part in text.replace("\n", " ").split("。") if part.strip()]
    if not sentences:
        return text[:max_chars]
    ranked = sorted(sentences, key=lambda item: cosine_score(query_tokens, item), reverse=True)
    excerpt = "。".join(ranked[:2])
    return excerpt if len(excerpt) <= max_chars else excerpt[:max_chars] + "..."


class KnowledgeAgent:
    """Auditable workflow: route -> retrieve -> structured catalog -> answer -> verify."""

    def __init__(self, store: KnowledgeStore):
        self.store = store
        self.router = QueryRouter()
        self.retrieval = RetrievalPipeline(store)

    def _structured_context(self, plan) -> list[dict]:
        if not plan.needs_graph:
            return []
        region_code = plan.regions[0] if len(plan.regions) == 1 else None
        return self.store.list_compliance_records(region_code=region_code, limit=12)

    def answer(self, question: str, *, knowledge_type: str | None = None) -> dict:
        plan = self.router.plan(question)
        results, retrieval_trace = self.retrieval.retrieve(question, plan=plan, knowledge_type=knowledge_type, limit=8)
        workflow = [
            {"step": "route", "status": "done", "detail": plan.reason},
            {"step": "retrieve", "status": "done", "detail": retrieval_trace},
        ]

        structured_records = self._structured_context(plan)
        if not plan.needs_graph:
            graph_status = "not_needed"
        elif structured_records:
            graph_status = "structured_catalog_connected"
        else:
            graph_status = "schema_ready_data_not_connected"
        graph_trace = {
            "requested": plan.needs_graph,
            "status": graph_status,
            "record_count": len(structured_records),
            "records": [
                {
                    "id": item["id"], "type": item["record_type"],
                    "type_label": TYPE_LABELS.get(item["record_type"], item["record_type"]),
                    "name": item["name"], "code": item.get("code", ""),
                    "region_code": item.get("region_code", ""), "product_class": item.get("product_class", ""),
                    "version": item.get("version", ""), "source_document_id": item.get("source_document_id"),
                    "source_chunk_id": item.get("source_chunk_id"),
                }
                for item in structured_records[:12]
            ],
            "note": "只使用人工审核通过的结构化记录；演示图谱不会参与正式回答。" if structured_records else "本体/图谱编排位已就绪；导入资料并审核抽取候选后，结构化知识目录才参与正式分析。",
        }
        workflow.append({"step": "graph_expand", "status": graph_trace["status"], "detail": graph_trace})

        if not results and not structured_records:
            workflow.append({"step": "answer", "status": "abstained", "detail": "no_evidence"})
            return {
                "question": question,
                "answer": "当前知识库没有检索到足够依据，本次不生成推断性结论。请补充资料或缩小问题范围。",
                "citations": [], "query_plan": plan.to_dict(), "retrieval_trace": retrieval_trace,
                "graph_trace": graph_trace, "workflow": workflow,
                "verification": {"grounded": False, "reason": "no_evidence"},
            }

        citations = [
            {"evidence_id": f"doc-{item['document_id']}-chunk-{item['chunk_id']}", "title": item["title"],
             "filename": item["filename"], "chunk": item["chunk_index"], "score": item["score"],
             "channels": item.get("retrieval_channels", [])}
            for item in results[:6]
        ]

        if results:
            llm_answer, model_trace = call_chat_model(build_rag_prompt(question, results[:6], query_plan=plan.to_dict()))
            if llm_answer:
                answer = llm_answer
                answer_mode = "llm_grounded"
            else:
                key_points = [_best_excerpt(question, item["text"]) for item in results[:3]]
                answer = "基于当前知识库证据，可以确认：\n" + "\n".join(f"- {point}" for point in key_points)
                if structured_records:
                    answer += f"\n\n已关联 {len(structured_records)} 条人工审核通过的法规/认证结构化记录，可用于继续核对地区、产品分类、版本和适用范围。"
                elif plan.route == "compliance_path":
                    answer += "\n\n法规/认证问题仍需核对适用地区、产品分类、版本状态和生效日期；当前没有已审核结构化目录时，不扩展未被文档证据支持的要求。"
                answer_mode = "extractive_grounded"
        else:
            model_trace = {"called": False, "reason": "structured_catalog_only"}
            answer = "根据人工审核通过的结构化知识目录，当前可核对的项目包括：\n" + "\n".join(
                f"- {TYPE_LABELS.get(item['record_type'], item['record_type'])}：{item['name']}" + (f"（{item['code']}）" if item.get("code") else "")
                for item in structured_records[:8]
            )
            answer += "\n\n这些记录仍需结合版本、生效状态、产品适用范围及原始证据形成正式准入结论。"
            answer_mode = "structured_catalog_grounded"
            citations = [
                {"evidence_id": f"doc-{item['source_document_id']}-chunk-{item['source_chunk_id']}",
                 "title": item.get("source_filename") or item["name"], "filename": item.get("source_filename") or "",
                 "chunk": item.get("source_chunk_index"), "score": 1.0, "channels": ["approved_structured_catalog"]}
                for item in structured_records[:6]
                if item.get("source_document_id") and item.get("source_chunk_id")
            ]

        verification = {
            "grounded": bool(citations), "citation_count": len(citations), "answer_mode": answer_mode,
            "structured_record_count": len(structured_records),
            "abstention_rule": "无证据不作答；法规版本/适用性只使用人工审核通过的结构化状态与原始证据",
        }
        workflow.append({"step": "answer", "status": "done", "detail": answer_mode})
        workflow.append({"step": "verify", "status": "done", "detail": verification})
        return {
            "question": question, "answer": answer, "citations": citations, "query_plan": plan.to_dict(),
            "retrieval_trace": retrieval_trace, "graph_trace": graph_trace, "workflow": workflow,
            "verification": verification, "model_trace": model_trace,
        }
