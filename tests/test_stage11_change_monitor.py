from __future__ import annotations

import tempfile
from pathlib import Path

from app.catalog_maintenance import CatalogMaintenanceService
from app.change_monitor import ChangeMonitorService
from app.graph_governance import GraphGovernanceService
from app.processing import DocumentProcessingService
from app.store import KnowledgeStore


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = KnowledgeStore(root / "watch.db")
        doc_id = store.upsert_document(
            filename="new_regulation.txt",
            title="法规更新资料",
            knowledge_type="法规知识",
            tags=["EU", "家用电器"],
            source_path=str(root / "new_regulation.txt"),
            full_text="EU-REG-100 发布 2027 版本，并调整适用范围。新增 CERT-NEW 认证要求。",
            mime_type="text/plain",
            parser="test",
            metadata={},
            chunks=[{
                "chunk_index": 1,
                "text": "EU-REG-100 发布 2027 版本，并调整适用范围。新增 CERT-NEW 认证要求。",
                "metadata": {},
            }],
        )
        chunk_id = store.document_chunks(doc_id)[0]["id"]
        with store.lock:
            current_id = store._upsert_compliance_record_locked({
                "record_type": "regulation",
                "code": "EU-REG-100",
                "name": "家电市场准入法规",
                "region_code": "EU",
                "region_name": "欧盟",
                "product_class": "家用电器",
                "status": "active",
                "version": "2026",
                "effective_from": "2026-01-01",
                "effective_to": "",
                "source_document_id": doc_id,
                "source_chunk_id": chunk_id,
                "attributes": {"applicability_scope": "普通家用电器"},
            })
            cert_id = store._upsert_compliance_record_locked({
                "record_type": "certification",
                "code": "CERT-OLD",
                "name": "现有认证",
                "region_code": "EU",
                "region_name": "欧盟",
                "product_class": "家用电器",
                "status": "active",
                "version": "2026",
                "effective_from": "2026-01-01",
                "effective_to": "",
                "source_document_id": doc_id,
                "source_chunk_id": chunk_id,
                "attributes": {},
            })
            store.conn.commit()

        version_candidate = {
            "record_type": "regulation",
            "code": "EU-REG-100",
            "name": "家电市场准入法规",
            "region_code": "EU",
            "region_name": "欧盟",
            "product_class": "家用电器",
            "status": "active",
            "version": "2027",
            "effective_from": "2027-01-01",
            "effective_to": "",
            "source_document_id": doc_id,
            "source_chunk_id": chunk_id,
            "attributes": {"applicability_scope": "家用电器及智能家电"},
        }
        version_task = store.create_extraction_task(
            document_id=doc_id,
            chunk_id=chunk_id,
            candidate_type="regulation",
            candidate_name="家电市场准入法规",
            candidate_code="EU-REG-100",
            region_code="EU",
            region_name="欧盟",
            product_class="家用电器",
            confidence=0.95,
            candidate_payload=version_candidate,
        )
        assert version_task

        new_req_payload = {
            "record_type": "certification",
            "code": "CERT-NEW",
            "name": "新增认证要求",
            "region_code": "EU",
            "region_name": "欧盟",
            "product_class": "家用电器",
            "status": "active",
            "version": "2027",
            "effective_from": "2027-01-01",
            "effective_to": "",
            "source_document_id": doc_id,
            "source_chunk_id": chunk_id,
            "attributes": {},
        }
        new_task = store.create_extraction_task(
            document_id=doc_id,
            chunk_id=chunk_id,
            candidate_type="certification",
            candidate_name="新增认证要求",
            candidate_code="CERT-NEW",
            region_code="EU",
            region_name="欧盟",
            product_class="家用电器",
            confidence=0.9,
            candidate_payload=new_req_payload,
        )
        assert new_task

        GraphGovernanceService(store)
        monitor = ChangeMonitorService(store)
        result = monitor.scan(document_id=doc_id)
        assert result["created"] >= 3
        open_tasks = monitor.list_tasks(status="open", limit=100)
        event_types = {item["event_type"] for item in open_tasks}
        assert "version_difference" in event_types
        assert "effective_date_difference" in event_types
        assert "content_change" in event_types
        assert "possible_new_requirement" in event_types
        assert monitor.summary()["open_high"] >= 1

        # Re-scanning the same candidate set must be idempotent.
        again = monitor.scan(document_id=doc_id)
        assert again["created"] == 0

        version_watch = next(item for item in open_tasks if item["event_type"] == "version_difference")
        detail = monitor.detail(version_watch["id"])
        assert detail["existing"]["id"] == current_id
        assert detail["candidate"]["id"] == version_task
        assert "EU-REG-100" in detail["evidence_excerpt"]
        reviewed = monitor.review(version_watch["id"], action="resolve", operator="tester", note="已核对版本")
        assert reviewed["status"] == "resolved"

        # A formal status/version maintenance operation should create an impact-review todo.
        maintenance = CatalogMaintenanceService(store)
        with store.lock:
            store.conn.execute(
                "INSERT INTO graph_relations(source_record_id,relation_type,target_record_id,confidence,status) VALUES(?,?,?,?,?)",
                (current_id, "REQUIRES", cert_id, 1.0, "approved"),
            )
            store.conn.commit()
        status_result = maintenance.set_status(
            current_id, status="transition", operator="tester", note="进入过渡期"
        )
        impact = monitor.create_impact_review(
            record_id=current_id,
            change_id=status_result["change_id"],
            action="status",
        )
        assert impact["created"] is True
        assert impact["relation_count"] == 1
        assert any(item["event_type"] == "impact_review" for item in monitor.list_tasks(status="open", limit=100))

        # Processing service remains backwards-compatible and can accept the monitor.
        processing = DocumentProcessingService(store, root / "uploads", root / "tasks.json", change_monitor=monitor)
        assert processing.change_monitor is monitor

        print("OK: stage11 regulation change monitoring and todo workflow passed")


if __name__ == "__main__":
    main()
