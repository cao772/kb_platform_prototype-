from __future__ import annotations

import tempfile
from pathlib import Path

from app.graph_governance import GraphGovernanceService
from app.store import KnowledgeStore


def add_catalog_records(store: KnowledgeStore) -> list[int]:
    doc_id = store.upsert_document(
        filename="eu_graph_source.txt",
        title="欧盟法规认证关系样例",
        knowledge_type="法规知识",
        tags=["欧盟", "法规", "认证"],
        source_path="/tmp/eu_graph_source.txt",
        full_text=(
            "产品分类：家用电器。EU-REG-100 法规引用 EN-STD-200 标准，"
            "并要求 CERT-300 认证；CERT-300 认证要求 TEST-400 检测。"
        ),
        mime_type="text/plain",
        parser="test",
        metadata={"region": "EU"},
        chunks=[{
            "chunk_index": 1,
            "text": "产品分类：家用电器。EU-REG-100 法规引用 EN-STD-200 标准，并要求 CERT-300 认证；CERT-300 认证要求 TEST-400 检测。",
            "metadata": {"region": "EU"},
        }],
    )
    chunk_id = store.document_chunks(doc_id)[0]["id"]
    payloads = [
        {"record_type": "regulation", "code": "EU-REG-100", "name": "欧盟家电法规", "region_code": "EU", "region_name": "欧盟", "product_class": "家用电器", "status": "superseded", "version": "2024", "effective_from": "2024-01-01", "effective_to": "2025-12-31"},
        {"record_type": "regulation", "code": "EU-REG-100", "name": "欧盟家电法规", "region_code": "EU", "region_name": "欧盟", "product_class": "家用电器", "status": "active", "version": "2026", "effective_from": "2026-01-01", "effective_to": ""},
        {"record_type": "standard", "code": "EN-STD-200", "name": "欧盟家电标准", "region_code": "EU", "region_name": "欧盟", "product_class": "家用电器", "status": "active", "version": "2026", "effective_from": "2026-01-01", "effective_to": ""},
        {"record_type": "certification", "code": "CERT-300", "name": "欧盟家电认证", "region_code": "EU", "region_name": "欧盟", "product_class": "家用电器", "status": "active", "version": "2026", "effective_from": "2026-01-01", "effective_to": ""},
        {"record_type": "test_item", "code": "TEST-400", "name": "安全检测", "region_code": "EU", "region_name": "欧盟", "product_class": "家用电器", "status": "active", "version": "2026", "effective_from": "2026-01-01", "effective_to": ""},
    ]
    ids = []
    with store.lock:
        for payload in payloads:
            payload.update({
                "source_document_id": doc_id,
                "source_chunk_id": chunk_id,
                "attributes": {"review_basis": "stage3-test"},
            })
            ids.append(store._upsert_compliance_record_locked(payload))
        store.conn.commit()
    return ids


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = KnowledgeStore(Path(tmp) / "knowledge.db")
        record_ids = add_catalog_records(store)
        graph = GraphGovernanceService(store)

        result = graph.suggest_relations(region_code="EU", product_class="家用电器")
        assert result["created"] >= 4
        pending = graph.list_relations(status="pending")
        assert pending
        assert any(item["relation_type"] == "REPLACED_BY" for item in pending)

        for item in list(pending):
            graph.review_relation(int(item["id"]), action="approve", note="test approval")
        assert graph.summary()["approved"] == len(pending)

        projection = graph.project_graph(region_code="EU", product_class="家用电器", as_of="2026-09-15")
        assert projection["summary"]["nodes"] >= 4
        assert projection["summary"]["edges"] >= 2
        assert all(node["version"] != "2024" for node in projection["nodes"])

        paths = graph.certification_paths(region_code="EU", product_class="家用电器", as_of="2026-09-15")
        assert paths["status"] in {"ready", "partial"}
        assert paths["paths"]
        assert any(any(node["node_type"] == "certification" for node in path["nodes"]) for path in paths["paths"])

        # EU member market projections inherit approved EU shared knowledge.
        de_projection = graph.project_graph(region_code="DE", product_class="家用电器", as_of="2026-09-15")
        assert de_projection["summary"]["nodes"] == projection["summary"]["nodes"]
        assert {node["region_code"] for node in de_projection["nodes"]} == {"EU"}
        de_paths = graph.certification_paths(region_code="DE", product_class="家用电器", as_of="2026-09-15")
        assert de_paths["paths"]
        assert any(any(node["node_type"] == "certification" for node in path["nodes"]) for path in de_paths["paths"])

        impact = graph.impact_analysis(record_ids[1])
        assert impact["record"]["code"] == "EU-REG-100"
        assert impact["summary"]["downstream_count"] >= 1
        assert "certification" in impact["summary"]["affected_types"] or "standard" in impact["summary"]["affected_types"]

        graph.reset()
        assert graph.summary()["relations"] == 0

    print("OK: stage3 governed graph workflow passed")


if __name__ == "__main__":
    main()
