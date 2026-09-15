from __future__ import annotations

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
    """Auditable agent workflow: route -> retrieve -> optional graph -> answer -> verify."""

    def __init__(self, store: KnowledgeStore):
        self.store = store
        self.router = QueryRouter()
        self.retrieval = RetrievalPipeline(store)

    def answer(self, question: str, *, knowledge_type: str | None = None) -> dict:
        plan = self.router.plan(question)
        results, retrieval_trace = self.retrieval.retrieve(
            question,
            plan=plan,
            knowledge_type=knowledge_type,
            limit=8,
        )

        workflow = [
            {"step": "route", "status": "done", "detail": plan.reason},
            {"step": "retrieve", "status": "done", "detail": retrieval_trace},
        ]

        graph_trace = {
            "requested": plan.needs_graph,
            "status": "schema_ready_data_not_connected" if plan.needs_graph else "not_needed",
            "note": "本体/图谱能力已接入编排位；只有正式图谱数据接通后才参与事实回答，避免用演示图谱替代真实法规依据。",
        }
        workflow.append({"step": "graph_expand", "status": graph_trace["status"], "detail": graph_trace})

        if not results:
            workflow.append({"step": "answer", "status": "abstained", "detail": "no_evidence"})
            return {
                "question": question,
                "answer": "当前知识库没有检索到足够依据，本次不生成推断性结论。请补充资料或缩小问题范围。",
                "citations": [],
                "query_plan": plan.to_dict(),
                "retrieval_trace": retrieval_trace,
                "graph_trace": graph_trace,
                "workflow": workflow,
                "verification": {"grounded": False, "reason": "no_evidence"},
            }

        llm_answer, model_trace = call_chat_model(build_rag_prompt(question, results[:6], query_plan=plan.to_dict()))
        if llm_answer:
            answer = llm_answer
            answer_mode = "llm_grounded"
        else:
            key_points = [_best_excerpt(question, item["text"]) for item in results[:3]]
            answer = "基于当前知识库证据，可以确认：\n" + "\n".join(f"- {point}" for point in key_points)
            if plan.route == "compliance_path":
                answer += "\n\n法规/认证问题仍需核对适用地区、产品分类、版本状态和生效日期；当前未接入正式图谱数据时，不扩展未被文档证据支持的要求。"
            answer_mode = "extractive_grounded"

        citations = [
            {
                "evidence_id": f"doc-{item['document_id']}-chunk-{item['chunk_id']}",
                "title": item["title"],
                "filename": item["filename"],
                "chunk": item["chunk_index"],
                "score": item["score"],
                "channels": item.get("retrieval_channels", []),
            }
            for item in results[:6]
        ]
        verification = {
            "grounded": bool(citations),
            "citation_count": len(citations),
            "answer_mode": answer_mode,
            "abstention_rule": "无证据不作答；法规版本/适用性由结构化状态与正式图谱控制",
        }
        workflow.append({"step": "answer", "status": "done", "detail": answer_mode})
        workflow.append({"step": "verify", "status": "done", "detail": verification})

        return {
            "question": question,
            "answer": answer,
            "citations": citations,
            "query_plan": plan.to_dict(),
            "retrieval_trace": retrieval_trace,
            "graph_trace": graph_trace,
            "workflow": workflow,
            "verification": verification,
            "model_trace": model_trace,
        }
