from __future__ import annotations

import json
import tempfile
from pathlib import Path

from app.collection_experience import CollectionExperienceService
from app.site_extraction import SiteExtractionService
from app.source_collection import FIRST_WAVE_PATH
from app.source_registry import SourceRegistryService
from app.store import KnowledgeStore


CANDIDATE_PATH = Path("data/stage50_reuse_candidates.json")


def main() -> None:
    payload = json.loads(CANDIDATE_PATH.read_text(encoding="utf-8"))
    items = payload["items"]
    assert len(items) == 5
    assert [int(item["order"]) for item in items] == [51, 52, 53, 54, 55]

    first50 = {
        str(item["source_key"])
        for item in json.loads(FIRST_WAVE_PATH.read_text(encoding="utf-8"))
    }
    keys = [str(item["source_key"]) for item in items]
    assert len(keys) == len(set(keys))
    assert not (set(keys) & first50)

    result_payload = json.loads(Path("data/stage50_reuse_results.json").read_text(encoding="utf-8"))
    assert result_payload["stage"] == 50
    assert result_payload["summary"]["processed"] == 5
    assert result_payload["summary"]["template_matches"] == 5
    assert result_payload["summary"]["direct_reuse"] == 4
    assert result_payload["summary"]["minor_adjustment"] == 0
    assert result_payload["summary"]["needs_rework"] == 1
    assert result_payload["evidence"]["real_network_trial"] is True
    assert result_payload["evidence"]["outside_first50"] is True
    assert result_payload["governance"]["formal_knowledge_auto_promotion"] is False

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = KnowledgeStore(root / "stage50.db")
        registry = SourceRegistryService(store)
        site = SiteExtractionService(store, registry, root / "downloads")
        service = CollectionExperienceService(registry, site)

        migration = service.migration_validation()
        assert migration["summary"]["processed"] == 5
        assert migration["summary"]["direct_reuse"] == 4
        assert migration["summary"]["needs_rework"] == 1

        for item in items:
            source_key = str(item["source_key"])
            source = registry.by_key(source_key)
            assert source["status"] == "research"
            recommendation = service.recommend(source_key)
            assert recommendation["recommended_template_id"] == item["expected_template"], source_key
            top = recommendation["items"][0]
            assert top["recommended"] is True
            assert int((top.get("experience_support") or {}).get("source_count") or 0) > 0

    script = Path("scripts/validate_stage50_reuse.py").read_text(encoding="utf-8")
    real_workflow = Path(".github/workflows/stage50_real_reuse.yml").read_text(encoding="utf-8")
    quality = Path(".github/workflows/quality.yml").read_text(encoding="utf-8")

    assert "ResilientFetcher" in script
    assert "apply_template" in script
    assert "reuse_outcomes" in script
    assert "outside_first50" in script
    assert "real_network_trial" in script
    assert "formal_knowledge_auto_promotion" in script
    runtime = Path("app/platform_server.py").read_text(encoding="utf-8")
    page = Path("static/collection_experience.html").read_text(encoding="utf-8")
    assert '"/api/collection-experience/migration-validation"' in runtime
    assert "Stage50 · 第51～55站真实迁移验证" in page
    assert "/api/collection-experience/migration-validation" in page
    assert "真实迁移证据" in page
    assert "stage50-real-reuse-results" in real_workflow
    assert '"通用知识库平台"' in real_workflow
    assert "Stage50 real reuse migration tests" in quality

    print("OK: Stage50 candidates are outside first50 and reuse the validated template/outcome chain")


if __name__ == "__main__":
    main()
