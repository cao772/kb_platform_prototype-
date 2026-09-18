from __future__ import annotations

import tempfile
from pathlib import Path

from app.ontology import ontology_schema
from app.ontology_governance import OntologyGovernanceService
from app.store import KnowledgeStore


def main() -> None:
    schema = ontology_schema()
    domain_keys = {item["key"] for item in schema["domains"]}
    assert domain_keys == {"regulation", "standard", "certification", "gma"}
    gma = next(item for item in schema["domains"] if item["key"] == "gma")
    assert gma["derived"] is True
    assert schema["market_access_path"][:3] == ["product_class", "region", "regulation"]
    assert "evidence" in schema["market_access_path"]

    with tempfile.TemporaryDirectory() as tmp:
        store = KnowledgeStore(Path(tmp) / "ontology.db")
        document_id = store.upsert_document(
            filename="sample.pdf",
            title="法规样本文档",
            knowledge_type="法规知识",
            tags=["法规"],
            source_path="sample.pdf",
            full_text="REG-1 要求采用 STD-1。",
            mime_type="application/pdf",
            parser="test",
            chunks=[{"chunk_index": 0, "text": "REG-1 要求采用 STD-1。", "metadata": {}}],
        )
        chunk_id = store.document_chunks(document_id)[0]["id"]

        with store.lock:
            store._upsert_compliance_record_locked({
                "record_type": "regulation",
                "code": "REG-1",
                "name": "市场准入法规",
                "region_code": "T1",
                "region_name": "测试地区",
                "product_class": "家用电器",
                "status": "active",
                "version": "2026",
                "effective_from": "2026-01-01",
                "source_document_id": document_id,
                "source_chunk_id": chunk_id,
                "attributes": {"authority": "主管机构"},
            })
            store._upsert_compliance_record_locked({
                "record_type": "standard",
                "code": "STD-1",
                "name": "产品安全标准",
                "region_code": "T1",
                "region_name": "测试地区",
                "product_class": "家用电器",
                "status": "active",
                "version": "2026",
                "source_document_id": document_id,
                "source_chunk_id": chunk_id,
                "attributes": {},
            })
            store.conn.commit()

        store.create_extraction_task(
            document_id=document_id,
            chunk_id=chunk_id,
            candidate_type="certification",
            candidate_name="产品认证",
            candidate_code="CERT-1",
            region_code="T1",
            region_name="测试地区",
            product_class="家用电器",
            confidence=0.9,
            candidate_payload={
                "record_type": "certification",
                "name": "产品认证",
                "code": "CERT-1",
                "region_code": "T1",
                "region_name": "测试地区",
                "product_class": "家用电器",
                "status": "active",
                "source_document_id": document_id,
                "source_chunk_id": chunk_id,
                "attributes": {},
            },
        )

        service = OntologyGovernanceService(store)
        invalid = service.validate_payload({"record_type": "regulation"})
        assert invalid["valid"] is False
        assert "name" in invalid["missing_required"]

        valid = service.validate_payload({
            "record_type": "certification",
            "name": "产品认证",
            "region_code": "T1",
            "product_class": "家用电器",
        })
        assert valid["valid"] is True
        assert set(valid["domains"]) == {"certification", "gma"}

        coverage = service.coverage(region_code="T1", product_class="家用电器")
        assert coverage["summary"]["records"] == 2
        assert coverage["summary"]["evidence_backed"] == 2
        assert coverage["summary"]["evidence_coverage"] == 1.0
        domain_counts = {item["key"]: item["record_count"] for item in coverage["domains"]}
        assert domain_counts["regulation"] == 1
        assert domain_counts["standard"] == 1
        assert domain_counts["gma"] == 2

        queue = service.candidate_queue()
        assert queue["summary"]["pending"] == 1
        assert queue["items"][0]["validation"]["valid"] is True

        gma_view = service.domain_view("gma", region_code="T1", product_class="家用电器")
        assert gma_view["summary"]["records"] == 2

        blueprint = service.blueprint()
        assert "正式知识目录" in blueprint["business_flow"]
        assert blueprint["implementation"]["gma_strategy"].startswith("派生视图")

    page = Path("static/ontology.html").read_text(encoding="utf-8")
    runtime = Path("app/platform_server.py").read_text(encoding="utf-8")
    assert "四类知识库" in page
    assert "法规知识库" in page and "标准知识库" not in page  # labels come from API
    assert "/api/ontology/coverage" in page
    assert "/api/ontology/candidates" in page
    assert '"/ontology", "/ontology.html"' in runtime
    assert "/api/ontology/validate" in runtime

    print("OK: stage13 ontology governance and four-domain application passed")


if __name__ == "__main__":
    main()
