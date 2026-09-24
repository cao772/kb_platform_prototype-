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
        store = KnowledgeStore(root / "stage48.db")
        registry = SourceRegistryService(store)
        site = SiteExtractionService(store, registry, root / "downloads")
        service = CollectionExperienceService(registry, site)

        transfer = service.transferability()
        summary = transfer["summary"]
        assert summary["sources"] == 50
        assert summary["templates"] == 6
        assert summary["capabilities"] >= 9
        assert summary["tested"] == 50
        assert summary["passed"] == 44
        assert summary["partial"] == 2
        assert summary["restricted"] == 4
        assert summary["failed"] == 0

        capabilities = {item["capability_id"]: item for item in transfer["capabilities"]}
        assert "site_shape_matching" in capabilities
        assert "structured_source_first" in capabilities
        assert "rights_boundary" in capabilities
        assert "access_preconditions" in capabilities
        assert "version_feedback_loop" in capabilities
        assert capabilities["site_shape_matching"]["source_count"] == 50
        assert capabilities["relevance_governance"]["source_count"] == 50
        assert capabilities["access_preconditions"]["source_count"] == 6
        assert capabilities["rights_boundary"]["source_count"] > 0
        assert all(item["tested_count"] <= item["source_count"] for item in transfer["capabilities"])

        templates = {item["template_id"]: item for item in transfer["templates"]}
        assert len(templates) == 6
        assert sum(item["source_count"] for item in templates.values()) == 50
        assert sum(item["tested_count"] for item in templates.values()) == 50
        assert templates["standards_metadata_catalog"]["source_count"] > 0
        assert "rights_boundary" in templates["standards_metadata_catalog"]["capability_ids"]
        assert templates["structured_legal_api"]["source_count"] > 0
        assert "structured_source_first" in templates["structured_legal_api"]["capability_ids"]

        recommended = service.recommend("DE-STD")
        assert recommended["recommended_template_id"] == "standards_metadata_catalog"
        top = recommended["items"][0]
        assert top["recommended"] is True
        support = top["experience_support"]
        assert support["source_count"] == templates["standards_metadata_catalog"]["source_count"]
        assert support["tested_count"] == support["source_count"]
        assert "rights_boundary" in support["capability_ids"]
        assert support["examples"]

        assert transfer["governance"]["bounded_trial_required"] is True
        assert transfer["governance"]["human_review_required"] is True
        assert transfer["governance"]["formal_knowledge_auto_promotion"] is False
        assert len(transfer["workflow"]) >= 7

    runtime = Path("app/platform_server.py").read_text(encoding="utf-8")
    experience_page = Path("static/collection_experience.html").read_text(encoding="utf-8")
    onboarding_page = Path("static/collection_onboarding.html").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/quality.yml").read_text(encoding="utf-8")

    assert '"/api/collection-experience/transferability"' in runtime
    assert "通用复用能力地图" in experience_page
    assert "50站通用能力地图" in experience_page
    assert "/api/collection-experience/transferability" in experience_page
    assert "经验支撑：" in onboarding_page
    assert "首批50站同类经验" in onboarding_page
    assert "Stage48 transferable experience map tests" in workflow

    print("OK: stage48 converts first50 experience into visual reusable capability blocks")


if __name__ == "__main__":
    main()
