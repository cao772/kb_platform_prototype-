from __future__ import annotations

import tempfile
from pathlib import Path

from app.business_dashboard import BusinessDashboardService
from app.change_monitor import ChangeMonitorService
from app.map_service import RegulationMapService
from app.store import KnowledgeStore


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = KnowledgeStore(Path(tmp) / "map.db")
        ChangeMonitorService(store)
        doc_id = store.upsert_document(
            filename="map_source.txt",
            title="法规地图测试资料",
            knowledge_type="法规知识",
            tags=["EU", "US", "家用电器"],
            source_path="/tmp/map_source.txt",
            full_text="EU-REG-100 与 US-REG-200 法规认证资料。",
            mime_type="text/plain",
            parser="test",
            metadata={},
            chunks=[{"chunk_index": 1, "text": "EU-REG-100 与 US-REG-200 法规认证资料。", "metadata": {}}],
        )
        chunk_id = store.document_chunks(doc_id)[0]["id"]
        with store.lock:
            eu_reg = store._upsert_compliance_record_locked({
                "record_type": "regulation", "code": "EU-REG-100", "name": "欧盟家电准入法规",
                "region_code": "EU", "region_name": "欧盟", "product_class": "家用电器",
                "status": "active", "version": "2026", "effective_from": "2026-01-01", "effective_to": "",
                "source_document_id": doc_id, "source_chunk_id": chunk_id, "attributes": {},
            })
            store._upsert_compliance_record_locked({
                "record_type": "certification", "code": "EU-CERT-101", "name": "欧盟家电认证事项",
                "region_code": "EU", "region_name": "欧盟", "product_class": "家用电器",
                "status": "active", "version": "2026", "effective_from": "2026-01-01", "effective_to": "",
                "source_document_id": doc_id, "source_chunk_id": chunk_id, "attributes": {},
            })
            store._upsert_compliance_record_locked({
                "record_type": "regulation", "code": "US-REG-200", "name": "美国家电准入法规",
                "region_code": "US", "region_name": "美国", "product_class": "家用电器",
                "status": "active", "version": "2026", "effective_from": "2026-01-01", "effective_to": "",
                "source_document_id": None, "source_chunk_id": None, "attributes": {},
            })
            store._upsert_compliance_record_locked({
                "record_type": "standard", "code": "EU-STD-2027", "name": "欧盟即将生效标准",
                "region_code": "EU", "region_name": "欧盟", "product_class": "家用电器",
                "status": "active", "version": "2027", "effective_from": "2026-10-10", "effective_to": "",
                "source_document_id": doc_id, "source_chunk_id": chunk_id, "attributes": {},
            })
            store.conn.execute(
                """INSERT INTO knowledge_change_tasks(
                       fingerprint,event_type,severity,status,title,summary,existing_record_id,
                       region_code,product_class,payload_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    "map-change-eu", "version_difference", "high", "open",
                    "发现欧盟法规版本变化", "需核对新版本", eu_reg,
                    "EU", "家用电器", "{}",
                ),
            )
            store.conn.commit()

        service = RegulationMapService(store)
        overview = service.overview(product_class="家用电器", as_of="2026-09-16")
        assert overview["summary"]["regions"] == 2
        assert overview["summary"]["knowledge"] == 4
        assert overview["summary"]["pending_changes"] == 1
        assert overview["summary"]["upcoming_effective"] == 1
        assert overview["summary"]["evidence_missing"] == 1
        assert overview["summary"]["complete_markets"] == 0
        eu = next(item for item in overview["regions"] if item["region_code"] == "EU")
        assert eu["attention"] == "high"
        assert eu["mappable"] is True
        assert eu["knowledge_count"] == 3
        assert eu["type_counts"]["certification"] == 1

        de_market = next(item for item in overview["target_markets"] if item["region_code"] == "DE")
        assert de_market["chain_stage_count"] == 3
        assert de_market["chain_completeness"] == 0.6
        assert de_market["chain_complete"] is False
        assert de_market["chain_type_counts"]["standard"] == 1

        changed = service.overview(product_class="家用电器", as_of="2026-09-16", only_changed=True)
        assert [item["region_code"] for item in changed["regions"]] == ["EU"]

        detail = service.detail("EU", product_class="家用电器", as_of="2026-09-16")
        assert detail["region_name"] == "欧盟"
        assert detail["summary"]["knowledge"] == 3
        assert detail["summary"]["pending_changes"] == 1
        assert detail["summary"]["chain_stage_count"] == 3
        assert detail["summary"]["chain_completeness"] == 0.6
        assert detail["summary"]["chain_complete"] is False
        assert detail["changes"][0]["severity"] == "high"

        dashboard = BusinessDashboardService(store, ChangeMonitorService(store)).snapshot(as_of="2026-09-16")
        assert dashboard["headline"]["target_markets"] == 21
        assert dashboard["headline"]["formal_knowledge"] == 4
        assert dashboard["headline"]["open_changes"] == 1
        assert dashboard["headline"]["open_high"] == 1
        assert dashboard["market_progress"]["complete"] == 0
        assert dashboard["market_progress"]["building"] > 0
        assert dashboard["product_coverage"]["product_classes"] == 1
        libs = {item["key"]: item for item in dashboard["knowledge_libraries"]}
        assert libs["regulation"]["count"] == 2
        assert libs["standard"]["count"] == 1
        assert libs["certification"]["count"] == 1
        assert libs["gma"]["count"] == dashboard["headline"]["markets_with_data"]
        assert "不把候选内容计入正式统计" in dashboard["principle"]
        assert dashboard["recent_open_changes"][0]["severity"] == "high"

        page = Path("static/map.html").read_text(encoding="utf-8")
        assert "法规认证地图" in page
        assert "全球法规认证分布" in page
        assert "近期变化与待办" in page
        assert "市场准入链完整度" in page
        assert "准入链完整市场" in page
        assert "进入该市场GMA准入" in page
        assert "chain_completeness" in page
        assert "/api/map/overview" in page
        assert "/api/map/detail" in page
        assert "GraphRAG" not in page
        assert "Neo4j" not in page

        print("OK: stage12 business regulation certification map passed")


if __name__ == "__main__":
    main()
