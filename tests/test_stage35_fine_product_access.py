from __future__ import annotations

import tempfile
from pathlib import Path

from app.compliance import extract_review_candidates
from app.graph_governance import GraphGovernanceService
from app.market_access import MarketAccessService
from app.product_taxonomy import ProductTaxonomyService
from app.regions import TARGET_MARKETS
from app.source_registry import SourceRegistryService
from app.store import KnowledgeStore


def main() -> None:
    taxonomy = ProductTaxonomyService()

    de = taxonomy.resolve(
        product="电冰箱",
        product_class="家用制冷器具",
        region_code="DE",
        attributes={
            "use_context": "household",
            "product_form": "refrigerator_freezer",
            "total_volume_l": 450,
            "built_in": False,
            "wireless": True,
        },
    )
    assert de["matched"] is True
    assert de["family_id"] == "household_refrigeration"
    assert de["product_type_id"] == "refrigerator_freezer"
    assert de["classification_path"][-1] == "冷藏冷冻组合式冰箱"
    assert de["market_mapping"]["inherited_from"] == "EU"
    assert de["market_mapping"]["requested_region_code"] == "DE"
    assert de["market_mapping"]["mapping_status"] == "official_reference_confirmed"
    assert any("无线" in item for item in de["market_mapping"]["regulatory_dimensions"])
    assert "家用电器" in de["formal_match_terms"]

    us = taxonomy.resolve(
        product="refrigerator",
        product_class="household refrigerating appliances",
        region_code="US",
        attributes={"use_context": "household", "product_form": "refrigerator"},
    )
    assert "Refrigerators" in us["market_mapping"]["market_category"]
    assert any("10 CFR Part 430" in item for item in us["market_mapping"]["research_leads"])

    jp = taxonomy.resolve(
        product="电冰箱",
        product_class="家用制冷器具",
        region_code="JP",
        attributes={"use_context": "household", "product_form": "refrigerator", "total_volume_l": 400},
    )
    assert "電気冷蔵庫" in jp["market_mapping"]["market_category"]
    assert "Top Runner" in jp["market_mapping"]["classification_basis"]

    for market in TARGET_MARKETS:
        resolved = taxonomy.resolve(
            product="电冰箱",
            product_class="家用制冷器具",
            region_code=market.code,
            attributes={"use_context": "household", "product_form": "refrigerator"},
        )
        assert resolved["matched"] is True, market.code
        assert resolved["market_mapping"].get("market_category"), market.code
        assert resolved["market_mapping"].get("classification_basis"), market.code

    with tempfile.TemporaryDirectory() as tmp:
        store = KnowledgeStore(Path(tmp) / "taxonomy_access.db")
        registry = SourceRegistryService(store)
        for mapping in taxonomy.data.get("market_mappings", {}).values():
            for source_key in mapping.get("source_keys", []):
                assert registry.by_key(source_key)["source_key"] == source_key

        text = (
            "产品分类：家用电器。欧盟法规 TEST-EU-REF-001 要求制造商保存技术资料。\n"
            "该法规适用于相关家用电器。"
        )
        document_id = store.upsert_document(
            filename="eu_refrigerator_rule.txt",
            title="欧盟家用电器法规",
            knowledge_type="法规知识",
            tags=["法规", "欧盟", "家用电器"],
            source_path="/tmp/eu_refrigerator_rule.txt",
            full_text=text,
            mime_type="text/plain",
            parser="test",
            metadata={"region": "EU"},
            chunks=[{"chunk_index": 1, "text": text, "metadata": {"region": "EU"}}],
        )
        extraction = extract_review_candidates(store, document_id)
        pending = store.list_extraction_tasks(status="pending")
        regulation = next(item for item in pending if item["candidate_type"] == "regulation")
        approved = store.review_extraction_task(regulation["id"], action="approve", reviewer_note="taxonomy test")
        assert approved["compliance_record_id"]

        service = MarketAccessService(store, GraphGovernanceService(store))
        result = service.evaluate(
            product="电冰箱",
            product_class="家用制冷器具",
            region_code="DE",
            product_attributes={
                "use_context": "household",
                "product_form": "refrigerator",
                "total_volume_l": 450,
                "wireless": False,
            },
        )
        assert result["product_classification"]["matched"] is True
        assert result["product_classification"]["market_mapping"]["inherited_from"] == "EU"
        assert result["summary"]["matched_records"] == 1
        assert result["stages"][0]["count"] == 1
        graph = service.graph_service.project_graph(
            region_code="DE",
            product_class="家用制冷器具",
            product_class_terms=result["product_classification"]["formal_match_terms"],
        )
        assert any(node["product_class"] == "家用电器" for node in graph["nodes"])

    page = Path("static/index.html").read_text(encoding="utf-8")
    server = Path("app/server.py").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/quality.yml").read_text(encoding="utf-8")
    assert 'value="电冰箱"' in page
    assert "标准产品分类" in page
    assert "/api/product-taxonomy" in page
    assert "renderAccessAttributes" in page
    assert "产品分类与市场映射" in page
    assert "product_attributes" in page
    assert any(
        child.get("name_zh") == "冷藏冷冻组合式冰箱"
        for child in taxonomy.data["product_families"][0]["children"]
    )
    assert "product_attributes" in server
    assert "Stage35 fine-grained product market access tests" in workflow

    print("OK: stage35 fine-grained refrigerator product classification and market mapping passed")


if __name__ == "__main__":
    main()
