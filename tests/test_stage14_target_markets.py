from __future__ import annotations

import tempfile
from pathlib import Path

from app.change_monitor import ChangeMonitorService
from app.governance import analyze_product_access_v2
from app.map_service import RegulationMapService
from app.regions import TARGET_MARKETS, canonical_region_code, knowledge_scope_codes, target_market_catalog
from app.store import KnowledgeStore


EXPECTED = {
    "DE", "FR", "IT", "ES", "NL", "BE", "SE", "DK", "FI", "AT", "IE",
    "NO", "CH", "GB", "US", "CA", "JP", "KR", "AU", "NZ", "SG",
}


def main() -> None:
    assert len(TARGET_MARKETS) == 21
    assert {item.code for item in TARGET_MARKETS} == EXPECTED
    assert canonical_region_code("UK") == "GB"
    assert knowledge_scope_codes("DE") == ("DE", "EU")
    assert knowledge_scope_codes("GB") == ("GB",)
    catalog = target_market_catalog()
    assert catalog["count"] == 21
    assert catalog["status"] == "预研暂定"

    with tempfile.TemporaryDirectory() as tmp:
        store = KnowledgeStore(Path(tmp) / "regions.db")
        ChangeMonitorService(store)
        doc = store.upsert_document(
            filename="eu.txt",
            title="欧盟共享法规",
            knowledge_type="法规知识",
            tags=["EU", "家用电器"],
            source_path="eu.txt",
            full_text="EU REG shared requirement",
            chunks=[{"chunk_index": 0, "text": "EU REG shared requirement", "metadata": {}}],
        )
        chunk = store.document_chunks(doc)[0]["id"]
        with store.lock:
            store._upsert_compliance_record_locked({
                "record_type": "regulation",
                "code": "EU-REG-1",
                "name": "欧盟共享法规",
                "region_code": "EU",
                "region_name": "欧盟",
                "product_class": "家用电器",
                "status": "active",
                "version": "2026",
                "effective_from": "2026-01-01",
                "source_document_id": doc,
                "source_chunk_id": chunk,
                "attributes": {},
            })
            store._upsert_compliance_record_locked({
                "record_type": "standard",
                "code": "DE-STD-1",
                "name": "德国本地标准",
                "region_code": "DE",
                "region_name": "德国",
                "product_class": "家用电器",
                "status": "active",
                "version": "2026",
                "source_document_id": doc,
                "source_chunk_id": chunk,
                "attributes": {},
            })
            store.conn.commit()

        access = analyze_product_access_v2(
            store,
            product="家用电器",
            product_class="家用电器",
            region_code="DE",
            as_of="2026-09-18",
        )
        assert set(access["knowledge_scope_codes"]) == {"DE", "EU"}
        assert access["summary"]["matched_records"] == 2
        assert access["summary"]["regulations"] == 1
        assert access["summary"]["standards"] == 1

        service = RegulationMapService(store)
        overview = service.overview(product_class="家用电器", as_of="2026-09-18")
        assert overview["summary"]["target_markets"] == 21
        assert len(overview["target_markets"]) == 21
        de = next(item for item in overview["target_markets"] if item["region_code"] == "DE")
        fr = next(item for item in overview["target_markets"] if item["region_code"] == "FR")
        gb = next(item for item in overview["target_markets"] if item["region_code"] == "GB")
        assert de["knowledge_count"] == 2
        assert fr["knowledge_count"] == 1
        assert gb["knowledge_count"] == 0
        assert gb["attention"] == "not_started"

        detail = service.detail("DE", product_class="家用电器", as_of="2026-09-18")
        assert detail["region_name"] == "德国"
        assert set(detail["knowledge_scope_codes"]) == {"DE", "EU"}
        assert detail["summary"]["knowledge"] == 2

    index = Path("static/index.html").read_text(encoding="utf-8")
    map_page = Path("static/map.html").read_text(encoding="utf-8")
    runtime = Path("app/platform_server.py").read_text(encoding="utf-8")
    for code in EXPECTED:
        assert f'value="{code}"' in index
    assert "中国 CN" not in index
    assert "印度 IN" not in index
    assert "目标国家/地区" in map_page
    assert "待建设" in map_page
    assert "target_markets" in map_page
    assert '"/api/regions"' in runtime

    print("OK: stage14 preliminary 21-country market master and EU inheritance passed")


if __name__ == "__main__":
    main()
