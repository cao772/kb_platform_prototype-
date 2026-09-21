from __future__ import annotations

import tempfile
from pathlib import Path

from app.compliance import extract_review_candidates
from app.graph_governance import GraphGovernanceService
from app.market_access import MarketAccessService
from app.product_taxonomy import ProductTaxonomyService
from app.store import KnowledgeStore


def main() -> None:
    taxonomy = ProductTaxonomyService()
    catalog = taxonomy.catalog()
    families = {item["id"]: item for item in catalog["product_families"]}
    assert len(families) >= 6
    for key in (
        "household_refrigeration",
        "room_air_conditioners",
        "household_laundry",
        "household_dishwashers",
        "electronic_displays",
        "light_sources",
    ):
        assert key in families

    air = taxonomy.resolve(
        product="分体式空调",
        product_class="房间空调器",
        region_code="DE",
        attributes={
            "use_context": "household",
            "product_form": "split_air_conditioner",
            "rated_cooling_capacity_kw": 3.5,
            "heating_function": True,
            "wireless": True,
        },
    )
    assert air["matched"] is True
    assert air["family_id"] == "room_air_conditioners"
    assert air["market_mapping"]["inherited_from"] == "EU"
    assert air["market_mapping"]["mapping_status"] == "official_reference_confirmed"
    assert any("206/2012" in item for item in air["market_mapping"]["research_leads"])
    assert any("无线" in item for item in air["market_mapping"]["regulatory_dimensions"])

    washer = taxonomy.resolve(
        product="滚筒洗衣机",
        product_class="家用洗衣机和洗烘一体机",
        region_code="US",
        attributes={"product_form": "front_load_washer", "rated_capacity_kg": 10},
    )
    assert washer["product_type_id"] == "front_load_washer"
    assert washer["market_mapping"]["mapping_status"] == "official_reference_confirmed"
    assert any("430.32(g)" in item for item in washer["market_mapping"]["research_leads"])

    dishwasher = taxonomy.resolve(
        product="紧凑型洗碗机",
        product_class="家用洗碗机",
        region_code="CA",
        attributes={"product_form": "compact_dishwasher", "place_settings": 6},
    )
    assert dishwasher["product_type_id"] == "compact_dishwasher"
    assert dishwasher["market_mapping"]["mapping_status"] == "official_reference_confirmed"
    assert "compact" in dishwasher["market_mapping"]["market_category"].lower()

    display = taxonomy.resolve(
        product="OLED电视",
        product_class="电子显示设备",
        region_code="EU",
        attributes={"product_form": "television", "diagonal_in": 65, "display_technology": "oled", "hdr": True},
    )
    assert display["market_mapping"]["mapping_status"] == "official_reference_confirmed"
    assert any("2019/2021" in item for item in display["market_mapping"]["research_leads"])

    light = taxonomy.resolve(
        product="LED灯泡",
        product_class="光源和照明产品",
        region_code="SG",
        attributes={"product_form": "led_lamp", "rated_power_w": 9, "luminous_flux_lm": 800},
    )
    assert light["market_mapping"]["mapping_status"] == "official_reference_confirmed"
    assert any("MELS" in item for item in light["market_mapping"]["regulatory_dimensions"])

    # Markets that have not yet been formally mapped still receive a visible
    # research seed rather than a fabricated official classification.
    au_washer = taxonomy.resolve(
        product="洗衣机",
        product_class="家用洗衣机和洗烘一体机",
        region_code="AU",
        attributes={"product_form": "front_load_washer"},
    )
    assert au_washer["market_mapping"]["mapping_status"] == "research_seed"
    assert au_washer["market_mapping"]["classification_basis"]

    with tempfile.TemporaryDirectory() as tmp:
        store = KnowledgeStore(Path(tmp) / "expanded_access.db")
        text = (
            "产品分类：家用电器。欧盟法规 TEST-EU-HOUSEHOLD-001 要求制造商保存技术资料。\n"
            "相关产品进入欧盟市场前需要核对适用的安全和技术要求。"
        )
        document_id = store.upsert_document(
            filename="eu_household_rule.txt",
            title="欧盟家用电器法规",
            knowledge_type="法规知识",
            tags=["欧盟", "家用电器"],
            source_path="/tmp/eu_household_rule.txt",
            full_text=text,
            mime_type="text/plain",
            parser="test",
            metadata={"region": "EU"},
            chunks=[{"chunk_index": 1, "text": text, "metadata": {"region": "EU"}}],
        )
        extract_review_candidates(store, document_id)
        pending = store.list_extraction_tasks(status="pending")
        regulation = next(item for item in pending if item["candidate_type"] == "regulation")
        store.review_extraction_task(regulation["id"], action="approve", reviewer_note="expanded family test")

        service = MarketAccessService(store, GraphGovernanceService(store))
        result = service.evaluate(
            product="分体式空调",
            product_class="房间空调器",
            region_code="DE",
            product_attributes={
                "use_context": "household",
                "product_form": "split_air_conditioner",
                "rated_cooling_capacity_kw": 3.5,
            },
        )
        assert result["product_classification"]["family_id"] == "room_air_conditioners"
        assert result["summary"]["matched_records"] == 1
        assert result["stages"][0]["count"] == 1

    page = Path("static/index.html").read_text(encoding="utf-8")
    server = Path("app/server.py").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/quality.yml").read_text(encoding="utf-8")
    assert "/api/product-taxonomy" in page
    assert "renderAccessAttributes" in page
    assert "系统先按统一产品树识别产品类型和关键参数" in page
    assert '"/api/product-taxonomy"' in server
    assert "Stage37 expanded product access taxonomy tests" in workflow

    print("OK: stage37 expanded product taxonomy and market access mapping passed")


if __name__ == "__main__":
    main()
