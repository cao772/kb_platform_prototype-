from __future__ import annotations

import tempfile
from pathlib import Path

from app.agent import KnowledgeAgent
from app.query_router import QueryRouter
from app.retrieval import RetrievalPipeline
from app.store import KnowledgeStore


def add_doc(store: KnowledgeStore, filename: str, title: str, knowledge_type: str, text: str) -> None:
    store.upsert_document(
        filename=filename,
        title=title,
        knowledge_type=knowledge_type,
        tags=["法规"] if knowledge_type == "法规知识" else ["知识治理"],
        source_path=f"/tmp/{filename}",
        full_text=text,
        mime_type="text/plain",
        parser="test",
        metadata={"region": "EU" if "欧盟" in text else ""},
        chunks=[{"chunk_index": 1, "text": text, "metadata": {"region": "EU" if "欧盟" in text else ""}}],
    )


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = KnowledgeStore(Path(tmp) / "knowledge.db")
        add_doc(store, "eu_rules.txt", "欧盟法规认证资料", "法规知识", "家用电器出口欧盟时，应按产品分类核对适用法规、认证要求、检测项目、版本状态和生效日期。")
        add_doc(store, "general.txt", "通用知识平台建设说明", "通用知识", "通用知识平台包括文档治理、混合检索、RRF融合、重排、带来源智能问答、权限和审计能力。")

        plan = QueryRouter().plan("家用电器出口欧盟需要哪些法规和认证依据？")
        assert plan.route == "compliance_path"
        assert plan.needs_graph is True
        assert "EU" in plan.regions

        hits, trace = RetrievalPipeline(store).retrieve("家用电器出口欧盟法规认证", plan=plan, knowledge_type="法规知识")
        assert hits and hits[0]["filename"] == "eu_rules.txt"
        assert trace["strategy"] == "hybrid_rrf_rerank"

        answer = KnowledgeAgent(store).answer("家用电器出口欧盟需要核对什么？", knowledge_type="法规知识")
        assert answer["citations"]
        assert answer["verification"]["grounded"] is True
        assert answer["query_plan"]["route"] == "compliance_path"
        assert answer["graph_trace"]["status"] == "schema_ready_data_not_connected"
        assert len(answer["workflow"]) >= 5

    print("OK: modern routing/retrieval/agent chain passed")


if __name__ == "__main__":
    main()
