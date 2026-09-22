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
        store = KnowledgeStore(root / "stage44.db")
        registry = SourceRegistryService(store)
        site = SiteExtractionService(store, registry, root / "downloads")
        service = CollectionExperienceService(registry, site)

        created = registry.create({
            "source_key": "TEST-NEW-SAFETY",
            "region_code": "US",
            "source_name": "Example Product Safety Portal",
            "source_type": "gma",
            "authority": "Example Authority",
            "base_url": "https://example.test/safety/",
            "access_method": "html",
            "priority": "A",
            "status": "research",
            "refresh_policy": "weekly",
            "crawl_scope": "product safety recall alerts and risk notices",
        })
        assert created["source_key"] == "TEST-NEW-SAFETY"

        recommendation = service.recommend("TEST-NEW-SAFETY")
        assert recommendation["recommended_template_id"] == "product_safety_notice"
        assert recommendation["items"][0]["recommended"] is True

        preview = service.preview_template(
            "TEST-NEW-SAFETY",
            "product_safety_notice",
        )
        assert preview["base_hash"]
        assert preview["current_config"]["start_urls"] == ["https://example.test/safety/"]
        assert preview["candidate_config"]["start_urls"] == ["https://example.test/safety/"]
        assert preview["candidate_config"]["limits"]["max_pages"] == 10
        assert preview["candidate_config"]["limits"]["max_items"] == 200
        assert preview["candidate_config"]["limits"]["max_details"] == 100
        assert preview["changed_paths"]

        applied = service.apply_template(
            "TEST-NEW-SAFETY",
            "product_safety_notice",
            expected_base_hash=preview["base_hash"],
            operator="tester",
            note="approved reusable template",
        )
        assert applied["next_step"] == "run_bounded_trial"
        plan = site.plan("TEST-NEW-SAFETY")
        assert plan["origin"] == "custom"
        assert plan["config"]["start_urls"] == ["https://example.test/safety/"]
        assert plan["config"]["limits"]["max_pages"] == 10

        history = service.list_applications(source_key="TEST-NEW-SAFETY")
        assert history["summary"]["applications"] == 1
        assert history["items"][0]["template_id"] == "product_safety_notice"
        assert history["items"][0]["operator"] == "tester"

        # Stale previews must never overwrite a plan that changed after preview.
        stale = service.preview_template(
            "TEST-NEW-SAFETY",
            "market_access_energy",
        )
        current = site.plan("TEST-NEW-SAFETY")
        config = dict(current["config"])
        config["limits"] = dict(config.get("limits") or {})
        config["limits"]["max_pages"] = 9
        site.update_plan("TEST-NEW-SAFETY", config=config, enabled=True)
        try:
            service.apply_template(
                "TEST-NEW-SAFETY",
                "market_access_energy",
                expected_base_hash=stale["base_hash"],
                operator="tester",
            )
        except ValueError as exc:
            assert "changed after preview" in str(exc)
        else:
            raise AssertionError("stale preview must be rejected")

        # Standard sources should recommend the metadata-only template.
        registry.create({
            "source_key": "TEST-NEW-STD",
            "region_code": "GB",
            "source_name": "Example Standards",
            "source_type": "standard",
            "base_url": "https://standards.example.test/",
            "access_method": "metadata_only",
            "priority": "A",
            "status": "research",
            "refresh_policy": "weekly",
            "crawl_scope": "standard metadata catalogue",
        })
        std = service.recommend("TEST-NEW-STD")
        assert std["recommended_template_id"] == "standards_metadata_catalog"
        std_preview = service.preview_template("TEST-NEW-STD", "standards_metadata_catalog")
        assert std_preview["candidate_config"]["document_policy"] == "metadata_only"
        assert std_preview["candidate_config"]["discovery"]["fetch_attachments"] is False

    runtime = Path("app/platform_server.py").read_text(encoding="utf-8")
    onboarding = Path("static/collection_onboarding.html").read_text(encoding="utf-8")
    collection = Path("static/collection.html").read_text(encoding="utf-8")
    experience = Path("static/collection_experience.html").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/quality.yml").read_text(encoding="utf-8")

    assert '"/api/collection-experience/recommend"' in runtime
    assert '"/api/collection-experience/template-preview"' in runtime
    assert '"/api/collection-experience/template-apply"' in runtime
    assert '"/collection-onboarding"' in runtime
    assert "把50站经验复用到新网站" in onboarding
    assert "确认套用模板" in onboarding
    assert "initialSourceKey" in collection
    assert "新站接入" in experience
    assert "Stage44 new-site experience reuse tests" in workflow

    print("OK: stage44 visual new-site onboarding and reusable template apply passed")


if __name__ == "__main__":
    main()
