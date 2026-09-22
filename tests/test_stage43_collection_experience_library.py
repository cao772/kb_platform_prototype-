from __future__ import annotations

import tempfile
from pathlib import Path

from app.collection_experience import CollectionExperienceService
from app.site_extraction import SiteExtractionService
from app.source_registry import SourceRegistryService
from app.store import KnowledgeStore


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = KnowledgeStore(root / "stage43.db")
        registry = SourceRegistryService(store)
        site = SiteExtractionService(store, registry, root / "downloads")
        service = CollectionExperienceService(registry, site)

        overview = service.overview()
        assert overview["summary"]["sources"] == 50
        assert overview["summary"]["templates"] == 6
        assert len(overview["templates"]) == 6
        assert len(overview["common_lessons"]) >= 10
        assert len(overview["access_lessons"]) >= 4
        assert sum(int(x["source_count"]) for x in overview["templates"]) == 50
        assert all(int(x["source_count"]) > 0 for x in overview["templates"])

        by_key = {x["source_key"]: x for x in overview["sources"]}
        assert by_key["DE-LAW"]["template_id"] == "legal_portal_fulltext"
        assert by_key["US-FEDREG"]["template_id"] == "structured_legal_api"
        assert by_key["NL-LAW"]["template_id"] == "structured_legal_api"
        assert by_key["DE-STD"]["template_id"] == "standards_metadata_catalog"
        assert by_key["DE-STD"]["document_policy"] == "metadata_only"
        assert by_key["EU-NANDO"]["template_id"] == "certification_registry"
        assert by_key["EU-SAFETY-GATE"]["template_id"] == "product_safety_notice"
        assert by_key["EU-ECHA"]["template_id"] == "market_access_energy"

        # Repository evidence must remain explicit: Stage38 did not prove all
        # 50 sites fully crawled. Documented samples are marked separately and
        # all other sites remain configured-only until runtime evidence exists.
        assert by_key["DE-LAW"]["evidence"]["level"] == "verified_sample"
        assert by_key["US-DOE-EFFICIENCY"]["evidence"]["level"] == "partial_sample"
        assert by_key["US-ECFR"]["evidence"]["level"] == "restricted_sample"
        assert by_key["US-FEDREG"]["evidence"]["level"] == "restricted_sample"
        assert by_key["FR-LAW"]["evidence"]["level"] == "configured"
        assert overview["summary"]["configured_only"] > 0

        # Runtime evidence upgrades the experience level automatically.
        site._upsert_item(
            source_key="FR-LAW",
            url="https://example.test/fr/law/1",
            item_type="detail",
            title="French law",
            content_type="text/html",
            sha256="fr-law-1",
            fields={},
            metadata={"relevance": {"status": "relevant", "reasons": ["business_keyword"]}},
            text_excerpt="official law content",
            raw_path="",
            document_id=None,
        )
        with store.lock:
            store.conn.execute(
                """INSERT INTO source_deep_runs(
                       source_key,status,started_at,finished_at,pages_fetched,
                       items_discovered,attachments_discovered,documents_ingested,
                       changed_items,error
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    "FR-LAW",
                    "completed",
                    "2026-09-22T00:00:00+00:00",
                    "2026-09-22T00:01:00+00:00",
                    2,
                    1,
                    0,
                    0,
                    1,
                    "",
                ),
            )
            store.conn.commit()
        refreshed = {x["source_key"]: x for x in service.source_experiences()}
        assert refreshed["FR-LAW"]["evidence"]["level"] == "operational_verified"

    runtime = Path("app/platform_server.py").read_text(encoding="utf-8")
    collection_page = Path("static/collection.html").read_text(encoding="utf-8")
    experience_page = Path("static/collection_experience.html").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/quality.yml").read_text(encoding="utf-8")
    assert '"/api/collection-experience/overview"' in runtime
    assert '"/collection-experience"' in runtime
    assert "采集经验" in collection_page
    assert "首批50站采集经验总结与复用" in experience_page
    assert "可复用采集模式" in experience_page
    assert "50站经验矩阵" in experience_page
    assert "访问受限处置经验" in experience_page
    assert "Stage43 collection experience library tests" in workflow

    print("OK: stage43 first50 experience library and reusable archetypes passed")


if __name__ == "__main__":
    main()
