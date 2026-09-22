from __future__ import annotations

import os
import tempfile
from pathlib import Path

from app.agent import KnowledgeAgent
from app.graph_backend import GraphBackendRouter
from app.graph_governance import GraphGovernanceService
from app.model_gateway import build_rag_prompt
from app.query_router import QueryRouter
from app.store import KnowledgeStore


def seed(store: KnowledgeStore) -> None:
    doc_id = store.upsert_document(
        filename="eu_graphrag.txt",
        title="欧盟家电法规认证知识",
        knowledge_type="法规知识",
        tags=["欧盟", "家用电器", "认证"],
        source_path="/tmp/eu_graphrag.txt",
        full_text=(
            "产品分类：家用电器。EU-REG-900 法规引用 EN-STD-901 标准，"
            "EN-STD-901 标准要求 CERT-902 认证，CERT-902 认证要求 TEST-903 检测。"
        ),
        mime_type="text/plain",
        parser="test",
        metadata={"region": "EU"},
        chunks=[{
            "chunk_index": 1,
            "text": (
                "产品分类：家用电器。EU-REG-900 法规引用 EN-STD-901 标准，"
                "EN-STD-901 标准要求 CERT-902 认证，CERT-902 认证要求 TEST-903 检测。"
            ),
            "metadata": {"region": "EU"},
        }],
    )
    chunk_id = store.document_chunks(doc_id)[0]["id"]
    records = [
        ("regulation", "EU-REG-900", "欧盟家电法规"),
        ("standard", "EN-STD-901", "欧盟家电标准"),
        ("certification", "CERT-902", "欧盟家电认证"),
        ("test_item", "TEST-903", "安全检测"),
    ]
    with store.lock:
        for record_type, code, name in records:
            store._upsert_compliance_record_locked({
                "record_type": record_type,
                "code": code,
                "name": name,
                "region_code": "EU",
                "region_name": "欧盟",
                "product_class": "家用电器",
                "status": "active",
                "version": "2026",
                "effective_from": "2026-01-01",
                "effective_to": "",
                "source_document_id": doc_id,
                "source_chunk_id": chunk_id,
                "attributes": {"review_basis": "stage4-test"},
            })
        store.conn.commit()


def main() -> None:
    old_backend = os.environ.pop("KB_GRAPH_BACKEND", None)
    old_settings = os.environ.get("KB_SETTINGS_PATH")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            # Keep the graph fallback assertion deterministic when a developer
            # has a real QA model configured in the local runtime settings.
            os.environ["KB_SETTINGS_PATH"] = str(Path(tmp) / "runtime_settings.json")
            store = KnowledgeStore(Path(tmp) / "knowledge.db")
            seed(store)
            graph = GraphGovernanceService(store)
            suggestions = graph.suggest_relations(region_code="EU", product_class="家用电器")
            assert suggestions["created"] >= 3
            for item in graph.list_relations(status="pending"):
                graph.review_relation(int(item["id"]), action="approve", note="stage4 test approval")

            backend = GraphBackendRouter(graph)
            status = backend.status()
            assert status["active_default"] == "sqlite-governed"
            projection, trace = backend.projection(region_code="EU", product_class="家用电器")
            assert trace["backend"] == "sqlite-governed"
            assert projection["edges"]

            agent = KnowledgeAgent(store, graph_service=graph, graph_backend=backend)
            answer = agent.answer("家用电器出口欧盟需要经过哪些认证和检测路径？", knowledge_type="法规知识")
            assert answer["graph_trace"]["status"] == "governed_graph_connected"
            assert answer["verification"]["graph_fact_count"] >= 1
            assert answer["verification"]["graph_path_count"] >= 1
            assert any("approved_graph" in item.get("channels", []) for item in answer["citations"])
            assert "人工审核通过的知识图谱" in answer["answer"]

            prompt = build_rag_prompt(
                "家用电器出口欧盟需要哪些认证？",
                [],
                query_plan=QueryRouter().plan("家用电器出口欧盟需要哪些认证？").to_dict(),
                graph_context={
                    "facts": [{
                        "source_type": "regulation",
                        "source": "欧盟家电法规",
                        "relation_label": "要求",
                        "target_type": "certification",
                        "target": "欧盟家电认证",
                    }],
                    "paths": [],
                },
            )
            assert "已审核图谱事实" in prompt[1]["content"]
            assert "欧盟家电认证" in prompt[1]["content"]

        print("OK: stage4 GraphRAG/backend workflow passed")
    finally:
        if old_backend is not None:
            os.environ["KB_GRAPH_BACKEND"] = old_backend
        if old_settings is None:
            os.environ.pop("KB_SETTINGS_PATH", None)
        else:
            os.environ["KB_SETTINGS_PATH"] = old_settings


if __name__ == "__main__":
    main()
