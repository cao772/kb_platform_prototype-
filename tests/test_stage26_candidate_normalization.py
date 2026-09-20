from __future__ import annotations

import tempfile
from pathlib import Path

from app.candidate_normalization import CandidateNormalizationService
from app.ontology_registry import OntologyRegistryService
from app.store import KnowledgeStore


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = KnowledgeStore(Path(tmp) / "candidate_norm.db")
        registry = OntologyRegistryService(store)
        registry.upsert_term({
            "term_type": "product_class",
            "canonical_name": "家用电器",
            "code": "home_appliance",
            "aliases": ["Home Appliance", "household appliance"],
            "source_note": "测试规范词",
        })
        registry.upsert_term({
            "term_type": "authority",
            "canonical_name": "产品安全主管机构",
            "code": "product_safety_authority",
            "aliases": ["Product Safety Authority", "PSA"],
            "source_note": "测试主管机构",
        })

        doc_id = store.upsert_document(
            filename="regulation.xml",
            title="Product Safety Regulation",
            knowledge_type="法规知识",
            tags=["DE"],
            source_path="regulation.xml",
            full_text="Product Safety Regulation applies to Home Appliance products.",
            chunks=[{
                "chunk_index": 0,
                "text": "Product Safety Regulation applies to Home Appliance products.",
                "metadata": {},
            }],
        )
        chunk_id = store.document_chunks(doc_id)[0]["id"]
        task_id = store.create_extraction_task(
            document_id=doc_id,
            chunk_id=chunk_id,
            candidate_type="regulation",
            candidate_name="Product Safety Regulation",
            candidate_code="REG-1",
            region_code="de",
            region_name="Germany",
            product_class="Home Appliance",
            confidence=0.91,
            candidate_payload={
                "record_type": "regulation",
                "code": "REG-1",
                "name": "Product Safety Regulation",
                "region_code": "de",
                "region_name": "Germany",
                "product_class": "Home Appliance",
                "status": "active",
                "version": "1",
                "effective_from": "2026-01-01",
                "effective_to": "",
                "source_document_id": doc_id,
                "source_chunk_id": chunk_id,
                "attributes": {
                    "authority": "Product Safety Authority",
                    "applicability_scope": "",
                    "exceptions": "",
                },
            },
        )
        assert task_id is not None

        service = CandidateNormalizationService(store, registry)
        suggestion = service.suggestions(task_id)
        assert suggestion["status"] == "pending"
        assert suggestion["fields"]["record_type"]["matches"][0]["code"] == "regulation"
        assert suggestion["fields"]["product_class"]["matches"][0]["canonical_name"] == "家用电器"
        assert suggestion["fields"]["authority"]["matches"][0]["canonical_name"] == "产品安全主管机构"
        assert suggestion["fields"]["region"]["canonical_code"] == "DE"
        assert suggestion["fields"]["region"]["canonical_name"] == "德国"

        pending = service.list_pending()
        assert pending["summary"]["pending"] == 1
        assert pending["summary"]["with_suggestions"] == 1

        applied = service.apply(
            task_id,
            changes={
                "record_type": "regulation",
                "product_class": "家用电器",
                "authority": "产品安全主管机构",
                "region_code": "DE",
                "region_name": "德国",
            },
            operator="知识治理员",
            note="已核对别名与目标市场主数据",
        )
        assert applied["status"] == "pending"
        assert applied["candidate"]["product_class"] == "家用电器"
        assert applied["candidate"]["region_code"] == "DE"
        assert applied["candidate"]["region_name"] == "德国"
        assert applied["candidate"]["attributes"]["authority"] == "产品安全主管机构"
        assert applied["candidate"]["attributes"]["normalization_review"]["applied"] is True

        tasks = store.list_extraction_tasks(status="pending")
        task = next(item for item in tasks if int(item["id"]) == int(task_id))
        assert task["candidate_type"] == "regulation"
        assert task["product_class"] == "家用电器"
        assert task["region_code"] == "DE"

        # Normalization is not approval.
        assert store.stats()["compliance_records"] == 0
        with store.lock:
            log_count = store.conn.execute(
                "SELECT COUNT(*) count FROM candidate_normalization_log WHERE task_id=?",
                (task_id,),
            ).fetchone()["count"]
        assert log_count == 1

    page = Path("static/candidate_normalization.html").read_text(encoding="utf-8")
    ontology_page = Path("static/ontology_governance.html").read_text(encoding="utf-8")
    runtime = Path("app/platform_server.py").read_text(encoding="utf-8")
    code = Path("app/candidate_normalization.py").read_text(encoding="utf-8")

    assert "候选知识术语归一与人工复核" in page
    assert "保存归一结果" in page
    assert "批准为正式知识" in page
    assert "驳回候选" in page
    assert "/api/candidate-normalization/apply" in page
    assert 'href="/candidate-normalization"' in ontology_page
    assert '"/candidate-normalization", "/candidate-normalization.html"' in runtime
    assert '"/api/candidate-normalization/pending"' in runtime
    assert "candidate_normalization_log" in code

    print("OK: stage26 human-governed candidate terminology normalization passed")


if __name__ == "__main__":
    main()
