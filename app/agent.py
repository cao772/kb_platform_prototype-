from __future__ import annotations

from app.compliance import TYPE_LABELS
from app.graph_backend import GraphBackendRouter
from app.graph_context import GovernedGraphContextProvider
from app.graph_governance import GraphGovernanceService
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


def _dedupe_citations(items: list[dict]) -> list[dict]:
    output: list[dict] = []
    index: dict[object, dict] = {}
    for source in items:
        item = dict(source)
        key = item.get("evidence_id") or (item.get("filename"), item.get("chunk"))
        if key not in index:
            item["channels"] = list(dict.fromkeys(item.get("channels", [])))
            index[key] = item
            output.append(item)
            continue
        existing = index[key]
        existing["channels"] = list(dict.fromkeys(existing.get("channels", []) + item.get("channels", [])))
        existing["score"] = max(float(existing.get("score") or 0), float(item.get("score") or 0))
    return output


class KnowledgeAgent:
    """Auditable workflow: route -> hybrid retrieve -> approved GraphRAG -> answer -> verify."""

    def __init__(
        self,
        store: KnowledgeStore,
        *,
        graph_service: GraphGovernanceService | None = None,
        graph_backend: GraphBackendRouter | None = None,
    ):
        self.store = store
        self.router = QueryRouter()
        self.retrieval = RetrievalPipeline(store)
        self.graph_service = graph_service or GraphGovernanceService(store)
        self.graph_backend = graph_backend or GraphBackendRouter(self.graph_service)
        self.graph_context = GovernedGraphContextProvider(store, self.graph_backend)

    def _structured_context(self, plan) -> list[dict]:
        if not plan.needs_graph:
            return []
        region_code = plan.regions[0] if len(plan.regions) == 1 else None
        return self.store.list_compliance_records(region_code=region_code, limit=12)

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

        structured_records = self._structured_context(plan)
        approved_graph = self.graph_context.build(question, plan)
        graph_trace = {
            "requested": approved_graph.get("requested", False),
            "status": approved_graph.get("status", "not_needed"),
            "backend": approved_graph.get("backend", {}),
            "region_code": approved_graph.get("region_code", ""),
            "product_class": approved_graph.get("product_class", ""),
            "node_count": approved_graph.get("node_count", 0),
            "edge_count": approved_graph.get("edge_count", 0),
            "facts": approved_graph.get("facts", [])[:16],
            "paths": approved_graph.get("paths", [])[:8],
            "structured_record_count": len(structured_records),
            "note": approved_graph.get("note", "普通问答不需要图谱扩展。"),
        }
        workflow.append({"step": "graph_expand", "status": graph_trace["status"], "detail": graph_trace})

        graph_facts = approved_graph.get("facts", [])
        if not results and not structured_records and not graph_facts:
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

        direct_citations = [
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
        citations = _dedupe_citations(direct_citations + approved_graph.get("citations", []))

        if results or graph_facts:
            llm_answer, model_trace = call_chat_model(
                build_rag_prompt(
                    question,
                    results[:6],
                    query_plan=plan.to_dict(),
                    graph_context=approved_graph,
                )
            )
            if llm_answer:
                answer = llm_answer
                answer_mode = "llm_document_plus_graph_grounded" if graph_facts else "llm_grounded"
            elif results:
                key_points = [_best_excerpt(question, item["text"]) for item in results[:3]]
                answer = "基于当前知识库证据，可以确认：\n" + "\n".join(f"- {point}" for point in key_points)
                if graph_facts:
                    answer += "\n\n人工审核通过的知识图谱进一步给出以下关系：\n" + "\n".join(
                        f"- {fact['source']} --{fact['relation_label']}--> {fact['target']}"
                        for fact in graph_facts[:6]
                    )
                    if approved_graph.get("paths"):
                        answer += "\n\n认证/要求路径：\n" + "\n".join(
                            "- " + " → ".join(path.get("labels", []))
                            for path in approved_graph["paths"][:4]
                        )
                    answer_mode = "document_plus_approved_graph_grounded"
                else:
                    if structured_records:
                        answer += f"\n\n已关联 {len(structured_records)} 条人工审核通过的法规/认证结构化记录，可继续核对地区、产品分类、版本和适用范围。"
                    elif plan.route == "compliance_path":
                        answer += "\n\n当前没有已审核图谱关系，不扩展未被文档或正式目录支持的认证路径。"
                    answer_mode = "extractive_grounded"
            else:
                answer = "根据人工审核通过的知识图谱，当前可以确认以下正式关系：\n" + "\n".join(
                    f"- {fact['source']} --{fact['relation_label']}--> {fact['target']}"
                    for fact in graph_facts[:8]
                )
                if approved_graph.get("paths"):
                    answer += "\n\n可追溯路径：\n" + "\n".join(
                        "- " + " → ".join(path.get("labels", []))
                        for path in approved_graph["paths"][:5]
                    )
                answer += "\n\n以上关系均来自人工审核后的正式知识节点和正式关系，仍需结合版本、生效状态、适用范围与例外条件形成最终准入结论。"
                answer_mode = "approved_graph_grounded"
        else:
            model_trace = {"called": False, "reason": "structured_catalog_only"}
            answer = "根据人工审核通过的结构化知识目录，当前可核对的项目包括：\n" + "\n".join(
                f"- {TYPE_LABELS.get(item['record_type'], item['record_type'])}：{item['name']}" + (f"（{item['code']}）" if item.get("code") else "")
                for item in structured_records[:8]
            )
            answer += "\n\n这些记录仍需结合版本、生效状态、产品适用范围及原始证据形成正式准入结论。"
            answer_mode = "structured_catalog_grounded"
            citations = _dedupe_citations([
                {
                    "evidence_id": f"doc-{item['source_document_id']}-chunk-{item['source_chunk_id']}",
                    "title": item.get("source_filename") or item["name"],
                    "filename": item.get("source_filename") or "",
                    "chunk": item.get("source_chunk_index"),
                    "score": 1.0,
                    "channels": ["approved_structured_catalog"],
                }
                for item in structured_records[:6]
                if item.get("source_document_id") and item.get("source_chunk_id")
            ])

        verification = {
            "grounded": bool(citations),
            "citation_count": len(citations),
            "answer_mode": answer_mode,
            "structured_record_count": len(structured_records),
            "graph_fact_count": len(graph_facts),
            "graph_path_count": len(approved_graph.get("paths", [])),
            "graph_backend": approved_graph.get("backend", {}).get("backend", "not_used"),
            "governance_boundary": "GraphRAG仅使用人工审核通过的正式节点与正式关系；候选关系不得参与回答。",
            "abstention_rule": "无文档证据、正式目录或已审核图谱事实时不作答；法规版本/适用性以结构化状态与原始证据为准",
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
