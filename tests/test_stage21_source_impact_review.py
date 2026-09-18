from __future__ import annotations

import tempfile
from pathlib import Path

from app.graph_governance import GraphGovernanceService
from app.source_collection import SourceCollectionService
from app.source_diff import SourceDifferenceService
from app.source_impact import SourceImpactService
from app.source_registry import SourceRegistryService
from app.store import KnowledgeStore


V1 = b"""<?xml version="1.0" encoding="UTF-8"?>
<law>
  <id>REG-1</id>
  <name>Product Safety Rule</name>
  <version>Version 1</version>
  <effective>2026-01-01</effective>
  <requirement>Product must comply with electrical safety requirements and CERT-1.</requirement>
</law>
"""

V2 = b"""<?xml version="1.0" encoding="UTF-8"?>
<law>
  <id>REG-1</id>
  <name>Product Safety Rule</name>
  <version>Version 2</version>
  <effective>2027-01-01</effective>
  <requirement>Product must comply with electrical safety, EMC requirements and CERT-1.</requirement>
</law>
"""


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = KnowledgeStore(root / "impact.db")
        registry = SourceRegistryService(store)
        state = {"payload": V1}

        def fetcher(url: str, headers: dict[str, str], timeout: int, max_bytes: int):
            return state["payload"], 200, "application/xml"

        collection = SourceCollectionService(
            store,
            registry,
            root / "downloads",
            fetcher=fetcher,
        )
        profile = next(
            item for item in collection.list_profiles()["items"]
            if item["profile_key"] == "JP-EGOV-LAWLIST-XML"
        )
        first = collection.run(profile["id"], auto_ingest=True)
        assert first["content_status"] == "initial"
        first_doc = int(first["document_id"])
        first_chunk = store.document_chunks(first_doc)[0]["id"]

        with store.lock:
            reg_id = store._upsert_compliance_record_locked({
                "record_type": "regulation",
                "code": "REG-1",
                "name": "Product Safety Rule",
                "region_code": "JP",
                "region_name": "日本",
                "product_class": "电气产品",
                "status": "active",
                "version": "Version 1",
                "effective_from": "2026-01-01",
                "source_document_id": first_doc,
                "source_chunk_id": first_chunk,
                "attributes": {"authority": "测试主管机构"},
            })
            cert_id = store._upsert_compliance_record_locked({
                "record_type": "certification",
                "code": "CERT-1",
                "name": "Product Certification",
                "region_code": "JP",
                "region_name": "日本",
                "product_class": "电气产品",
                "status": "active",
                "version": "2026",
                "source_document_id": first_doc,
                "source_chunk_id": first_chunk,
                "attributes": {},
            })
            store.conn.commit()

        graph = GraphGovernanceService(store)
        with store.lock:
            store.conn.execute(
                """
                INSERT INTO graph_relations(
                    source_record_id,relation_type,target_record_id,confidence,
                    evidence_document_id,evidence_chunk_id,status,reviewer_note,reviewed_at
                ) VALUES(?,?,?,?,?,?, 'approved','测试确认','2026-09-18T00:00:00Z')
                """,
                (int(reg_id), "REQUIRES", int(cert_id), 0.99, first_doc, first_chunk),
            )
            store.conn.commit()

        state["payload"] = V2
        second = collection.run(profile["id"], auto_ingest=True)
        assert second["content_status"] == "changed"
        event = next(
            item for item in collection.list_updates()["items"]
            if item["change_type"] == "changed"
        )

        diff = SourceDifferenceService(store)
        review = diff.review_state(event["id"])
        confirmed_diff = diff.save_review(
            event["id"],
            items=review["items"],
            action="confirm",
            operator="法规业务人员",
            note="已核对版本、日期及要求变化",
        )
        assert confirmed_diff["review_status"] == "confirmed"

        impact = SourceImpactService(store, diff, graph)
        matches = impact.match_formal_records(event["id"])
        assert matches["items"]
        best = matches["items"][0]
        assert best["record_id"] == int(reg_id)
        assert best["score"] >= 0.75
        assert any("上一版本文档" in reason for reason in best["reasons"])

        case = impact.build_case(event["id"], record_id=int(reg_id))
        assert case["exists"] is True
        assert case["review_status"] == "draft"
        assert case["source_record_id"] == int(reg_id)
        assert len(case["impact_items"]) >= 2

        source_item = next(
            item for item in case["impact_items"]
            if item["impact_kind"] == "formal_record_review"
        )
        cert_item = next(
            item for item in case["impact_items"]
            if item["record_id"] == int(cert_id)
        )
        assert source_item["impact_level"] == "direct"
        assert cert_item["impact_kind"] == "related_knowledge_review"
        assert cert_item["graph_depth"] == 1

        edited = [dict(item) for item in case["impact_items"]]
        cert_edit = next(item for item in edited if item["record_id"] == int(cert_id))
        cert_edit["impact_kind"] = "gma_path_review"
        cert_edit["action"] = "重新确认认证要求及GMA准入路径"
        cert_edit["note"] = "人工确认需要继续处理"

        draft = impact.save_case(
            event["id"],
            items=edited,
            action="save",
            operator="准入业务人员",
            note="已完成第一轮影响复核",
        )
        assert draft["review_status"] == "draft"
        saved_cert = next(
            item for item in draft["impact_items"]
            if item["record_id"] == int(cert_id)
        )
        assert saved_cert["impact_kind"] == "gma_path_review"
        assert "GMA" in saved_cert["action"]

        confirmed = impact.save_case(
            event["id"],
            items=draft["impact_items"],
            action="confirm",
            operator="准入业务人员",
            note="确认影响候选",
        )
        assert confirmed["review_status"] == "confirmed"
        assert confirmed["confirmed_at"]

        # Impact confirmation remains a review result; it must not rewrite formal facts.
        records = store.list_compliance_records(review_status="approved", limit=100)
        reg = next(item for item in records if int(item["id"]) == int(reg_id))
        cert = next(item for item in records if int(item["id"]) == int(cert_id))
        assert reg["version"] == "Version 1"
        assert cert["version"] == "2026"
        assert graph.summary()["approved"] == 1

        cases = impact.list_cases()
        assert cases["summary"]["confirmed"] == 1

    page = Path("static/source_impact.html").read_text(encoding="utf-8")
    diff_page = Path("static/source_diff.html").read_text(encoding="utf-8")
    runtime = Path("app/platform_server.py").read_text(encoding="utf-8")
    code = Path("app/source_impact.py").read_text(encoding="utf-8")

    assert "正式知识匹配与影响复核" in page
    assert "人工复核影响候选" in page
    assert "人工新增影响项" in page
    assert "确认影响复核" in page
    assert "/api/collection/impact-matches" in page
    assert "/api/collection/impact-build" in page
    assert "/api/collection/impact-review" in page
    assert "进入影响分析" in diff_page
    assert "/source-impact?event_id=" in diff_page
    assert '"/source-impact", "/source-impact.html"' in runtime
    assert '"/api/collection/impact-case"' in runtime
    assert "source_impact_cases" in code
    assert "match_formal_records" in code
    assert "save_case" in code

    print("OK: stage21 confirmed changes to editable formal-knowledge impact review passed")


if __name__ == "__main__":
    main()
