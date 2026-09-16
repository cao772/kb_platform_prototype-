from __future__ import annotations

import tempfile
from pathlib import Path

from app.catalog import catalog_detail, list_catalog
from app.graph_governance import GraphGovernanceService
from app.store import KnowledgeStore


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = KnowledgeStore(Path(tmp) / "catalog.db")
        doc_id = store.upsert_document(
            filename="catalog_source.txt",
            title="正式知识目录测试资料",
            knowledge_type="法规知识",
            tags=["EU", "家用电器"],
            source_path="/tmp/catalog_source.txt",
            full_text="EU-REG-100 2024版被2026版替代。2026版引用 EN-STD-200。",
            mime_type="text/plain",
            parser="test",
            metadata={"region": "EU"},
            chunks=[{"chunk_index": 1, "text": "EU-REG-100 2024版被2026版替代。2026版引用 EN-STD-200。", "metadata": {}}],
        )
        chunk_id = store.document_chunks(doc_id)[0]["id"]
        with store.lock:
            old_id = store._upsert_compliance_record_locked({
                "record_type": "regulation", "code": "EU-REG-100", "name": "家电市场准入法规",
                "region_code": "EU", "region_name": "欧盟", "product_class": "家用电器",
                "status": "superseded", "version": "2024", "effective_from": "2024-01-01", "effective_to": "2025-12-31",
                "source_document_id": doc_id, "source_chunk_id": chunk_id,
                "attributes": {"authority": "示例主管机构", "applicability_scope": "家用电器"},
            })
            new_id = store._upsert_compliance_record_locked({
                "record_type": "regulation", "code": "EU-REG-100", "name": "家电市场准入法规",
                "region_code": "EU", "region_name": "欧盟", "product_class": "家用电器",
                "status": "active", "version": "2026", "effective_from": "2026-01-01", "effective_to": "",
                "source_document_id": doc_id, "source_chunk_id": chunk_id,
                "attributes": {"authority": "示例主管机构", "applicability_scope": "家用电器", "exceptions": "特殊产品另行核对"},
            })
            std_id = store._upsert_compliance_record_locked({
                "record_type": "standard", "code": "EN-STD-200", "name": "家电安全标准",
                "region_code": "EU", "region_name": "欧盟", "product_class": "家用电器",
                "status": "active", "version": "2026", "effective_from": "2026-01-01", "effective_to": "",
                "source_document_id": doc_id, "source_chunk_id": chunk_id, "attributes": {},
            })
            store.conn.commit()

        graph = GraphGovernanceService(store)
        with store.lock:
            store.conn.execute(
                "INSERT INTO graph_relations(source_record_id,relation_type,target_record_id,confidence,evidence_document_id,evidence_chunk_id,status) VALUES(?,?,?,?,?,?,?)",
                (new_id, "REFERENCES", std_id, 0.95, doc_id, chunk_id, "approved"),
            )
            store.conn.commit()

        catalog = list_catalog(
            store,
            record_type="regulation",
            region_code="EU",
            product_class="家用电器",
            lifecycle="active",
            query="准入",
            evidence="with",
            as_of="2026-09-16",
        )
        assert catalog["summary"]["total"] == 3
        assert catalog["summary"]["type_counts"]["regulation"] == 2
        assert catalog["summary"]["evidence_coverage"] == 1.0
        assert catalog["summary"]["filtered"] == 1
        assert catalog["items"][0]["id"] == new_id
        assert catalog["items"][0]["authority"] == "示例主管机构"
        assert catalog["items"][0]["lifecycle_state"] == "active"

        detail = catalog_detail(store, new_id, as_of="2026-09-16")
        assert detail["item"]["exceptions"] == "特殊产品另行核对"
        assert len(detail["version_history"]) == 2
        assert {item["id"] for item in detail["version_history"]} == {old_id, new_id}
        assert "EU-REG-100" in detail["evidence_excerpt"]
        assert len(detail["relations"]) == 1
        assert detail["relations"][0]["target_record_id"] == std_id
        assert detail["relations"][0]["relation_label"] == "引用"

        print("OK: stage9 formal knowledge catalog passed")


if __name__ == "__main__":
    main()
