from __future__ import annotations

import tempfile
from pathlib import Path

from app.compliance import analyze_product_access, build_world_map, extract_review_candidates
from app.store import KnowledgeStore


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = KnowledgeStore(Path(tmp) / "knowledge.db")
        text = (
            "产品分类：家用电器。欧盟法规 DEMO-EU-100 要求制造商保存技术资料。\n"
            "相关认证要求应在产品进入市场前完成核对。\n"
            "检测项目包括安全测试。"
        )
        document_id = store.upsert_document(
            filename="eu_demo.txt",
            title="欧盟法规认证资料",
            knowledge_type="法规知识",
            tags=["法规", "欧盟"],
            source_path="/tmp/eu_demo.txt",
            full_text=text,
            mime_type="text/plain",
            parser="test",
            metadata={"region": "EU"},
            chunks=[{"chunk_index": 1, "text": text, "metadata": {"region": "EU"}}],
        )

        extraction = extract_review_candidates(store, document_id)
        assert extraction["created"] >= 3
        pending = store.list_extraction_tasks(status="pending")
        assert pending
        regulation = next(item for item in pending if item["candidate_type"] == "regulation")
        approved = store.review_extraction_task(regulation["id"], action="approve", reviewer_note="test")
        assert approved["status"] == "approved"
        assert approved["compliance_record_id"]

        records = store.list_compliance_records(region_code="EU")
        assert len(records) == 1
        assert records[0]["source_document_id"] == document_id

        world_map = build_world_map(store)
        assert world_map["demo_data"] is False
        assert world_map["summary"]["regions"] == 1
        assert world_map["summary"]["evidence_backed"] == 1

        access = analyze_product_access(store, product="演示电器", product_class="家用电器", region_code="EU")
        assert access["status"] == "evidence_ready"
        assert access["summary"]["regulations"] == 1
        assert access["decision_boundary"]

        empty_store = KnowledgeStore(Path(tmp) / "empty.db")
        demo_map = build_world_map(empty_store, include_demo=True)
        assert demo_map["demo_data"] is True
        assert demo_map["summary"]["records"] > 0
        assert empty_store.list_compliance_records() == []

    print("OK: compliance governance/access workflow passed")


if __name__ == "__main__":
    main()
