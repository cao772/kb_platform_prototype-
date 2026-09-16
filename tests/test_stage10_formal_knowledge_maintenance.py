from __future__ import annotations

import tempfile
from pathlib import Path

from app.catalog import catalog_detail, list_catalog
from app.catalog_maintenance import CatalogMaintenanceService
from app.graph_governance import GraphGovernanceService
from app.store import KnowledgeStore


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = KnowledgeStore(Path(tmp) / "maintenance.db")
        doc1 = store.upsert_document(
            filename="old.txt",
            title="旧版法规资料",
            knowledge_type="法规知识",
            tags=["EU", "家用电器"],
            source_path="/tmp/old.txt",
            full_text="EU-REG-100 2026版法规正文。",
            mime_type="text/plain",
            parser="test",
            metadata={},
            chunks=[{"chunk_index": 1, "text": "EU-REG-100 2026版法规正文。", "metadata": {}}],
        )
        doc2 = store.upsert_document(
            filename="new.txt",
            title="新版法规资料",
            knowledge_type="法规知识",
            tags=["EU", "家用电器"],
            source_path="/tmp/new.txt",
            full_text="EU-REG-100 2027版正式依据。",
            mime_type="text/plain",
            parser="test",
            metadata={},
            chunks=[{"chunk_index": 1, "text": "EU-REG-100 2027版正式依据。", "metadata": {}}],
        )
        chunk1 = store.document_chunks(doc1)[0]["id"]
        chunk2 = store.document_chunks(doc2)[0]["id"]
        with store.lock:
            record_id = store._upsert_compliance_record_locked({
                "record_type": "regulation",
                "code": "EU-REG-100",
                "name": "家用电器市场准入法规",
                "region_code": "EU",
                "region_name": "欧盟",
                "product_class": "家用电器",
                "status": "active",
                "version": "2026",
                "effective_from": "2026-01-01",
                "effective_to": "",
                "source_document_id": doc1,
                "source_chunk_id": chunk1,
                "attributes": {"authority": "原主管机构"},
            })
            store.conn.commit()

        # Ensure graph table exists before replacement version maintenance.
        GraphGovernanceService(store)
        service = CatalogMaintenanceService(store)

        edited = service.update_record(
            record_id,
            changes={
                "authority": "欧盟示例主管机构",
                "applicability_scope": "家用电器及相关产品",
                "exceptions": "特殊用途产品另行核对",
                "legal_effect": "市场准入依据",
            },
            operator="测试维护人",
            note="补充业务字段",
        )
        assert edited["item"]["attributes"]["authority"] == "欧盟示例主管机构"

        status = service.set_status(
            record_id,
            status="transition",
            effective_to="2026-12-31",
            operator="测试维护人",
            note="进入过渡期",
        )
        assert status["item"]["status"] == "transition"
        assert status["item"]["effective_to"] == "2026-12-31"

        replacement = service.create_replacement_version(
            record_id,
            version="2027",
            effective_from="2027-01-01",
            status="active",
            operator="测试维护人",
            note="2027版替代2026版",
            copy_evidence=False,
            mark_source_superseded=True,
            close_source_day_before=True,
        )
        new_id = replacement["replacement_record_id"]
        assert replacement["source"]["status"] == "superseded"
        assert replacement["source"]["effective_to"] == "2026-12-31"
        assert replacement["replacement"]["version"] == "2027"
        assert replacement["replacement"]["source_document_id"] is None

        evidence = service.attach_evidence(
            new_id,
            document_id=doc2,
            chunk_id=chunk2,
            source_url="https://example.invalid/eu-reg-100",
            review_basis="新版正式文件",
            operator="测试维护人",
            note="补充新版依据",
        )
        assert evidence["item"]["source_document_id"] == doc2
        assert evidence["item"]["source_chunk_id"] == chunk2
        assert evidence["item"]["attributes"]["review_basis"] == "新版正式文件"

        history_old = service.history(record_id)
        history_new = service.history(new_id)
        assert {item["action"] for item in history_old} >= {"update", "status", "create_version"}
        assert {item["action"] for item in history_new} >= {"create_version", "attach_evidence"}
        assert all("before" in item and "after" in item for item in history_new)

        with store.lock:
            rel = store.conn.execute(
                "SELECT relation_type,status FROM graph_relations WHERE source_record_id=? AND target_record_id=?",
                (record_id, new_id),
            ).fetchone()
        assert rel is not None
        assert rel["relation_type"] == "REPLACED_BY"
        assert rel["status"] == "approved"

        catalog = list_catalog(store, as_of="2027-09-16")
        assert catalog["summary"]["total"] == 2
        detail = catalog_detail(store, new_id, as_of="2027-09-16")
        assert len(detail["version_history"]) == 2
        assert detail["item"]["source_filename"] == "new.txt"
        assert "2027版正式依据" in detail["evidence_excerpt"]

        # Formal knowledge is retired by state, not hard-deleted.
        retired = service.set_status(
            new_id,
            status="repealed",
            operator="测试维护人",
            note="测试废止保留历史",
        )
        assert retired["item"]["status"] == "repealed"
        assert len(store.list_compliance_records(review_status="approved", limit=20)) == 2

        options = service.evidence_options(document_id=doc2)
        assert len(options["documents"]) == 2
        assert options["chunks"][0]["id"] == chunk2

        print("OK: stage10 formal knowledge maintenance passed")


if __name__ == "__main__":
    main()
