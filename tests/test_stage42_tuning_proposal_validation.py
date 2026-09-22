from __future__ import annotations

import tempfile
from pathlib import Path

from app.site_extraction import SiteExtractionService
from app.source_registry import SourceRegistryService
from app.store import KnowledgeStore


def add_item(service: SiteExtractionService, source_key: str, suffix: str, automatic_status: str) -> int:
    service._upsert_item(
        source_key=source_key,
        url=f"https://example.test/{source_key.lower()}/{suffix}",
        item_type="detail",
        title=f"{source_key} {suffix}",
        content_type="text/html",
        sha256=f"{source_key}-{suffix}",
        fields={},
        metadata={
            "relevance": {
                "status": automatic_status,
                "reasons": ["test_signal"],
            }
        },
        text_excerpt="official regulatory content",
        raw_path="",
        document_id=None,
    )
    return service.list_items(source_key=source_key, limit=20)["items"][0]["id"]


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = KnowledgeStore(root / "stage42.db")
        service = SiteExtractionService(
            store,
            SourceRegistryService(store),
            root / "downloads",
        )

        over_id = add_item(service, "DE-LAW", "navigation", "relevant")
        service.review_item(
            over_id,
            status="irrelevant",
            operator="tester",
            note="navigation noise",
        )
        under_id = add_item(service, "DE-LAW", "prodsg", "needs_review")
        service.review_item(
            under_id,
            status="relevant",
            operator="tester",
            note="official law",
        )

        before = service.plan("DE-LAW")
        proposal = service.generate_tuning_proposal("DE-LAW")
        after_generate = service.plan("DE-LAW")

        assert proposal["status"] == "draft"
        assert proposal["baseline_metrics"]["reviewed_samples"] == 2
        assert proposal["baseline_metrics"]["agreement_rate"] == 0.0
        assert proposal["candidate_metrics"]["agreement_rate"] == 100.0
        assert len(proposal["rationale"]) == 2
        assert before["config"] == after_generate["config"], "proposal generation must not change active rules"

        candidate_rel = proposal["candidate_config"]["relevance"]
        assert len(candidate_rel["confirmed_include_url_patterns"]) == 1
        assert len(candidate_rel["confirmed_exclude_url_patterns"]) == 1

        applied = service.decide_tuning_proposal(
            proposal["id"],
            action="apply",
            operator="tester",
            note="validated against reviewed samples",
        )
        assert applied["status"] == "applied"
        active = service.plan("DE-LAW")
        assert active["origin"] == "custom"
        assert active["config"]["relevance"]["confirmed_include_url_patterns"]
        assert active["config"]["relevance"]["confirmed_exclude_url_patterns"]

        # Already-applied exact overrides must not generate a duplicate proposal
        # merely because historical automatic labels still contain old results.
        try:
            service.generate_tuning_proposal("DE-LAW")
        except ValueError as exc:
            assert "no reviewed rule disagreements available" in str(exc)
        else:
            raise AssertionError("duplicate proposal should not be generated")

        # A proposal can also be explicitly rejected without changing the plan.
        noise2 = add_item(service, "FR-LAW", "noise", "relevant")
        service.review_item(noise2, status="irrelevant", operator="tester", note="noise")
        fr_before = service.plan("FR-LAW")
        fr_proposal = service.generate_tuning_proposal("FR-LAW")
        rejected = service.decide_tuning_proposal(
            fr_proposal["id"],
            action="reject",
            operator="tester",
            note="manual rejection",
        )
        assert rejected["status"] == "rejected"
        assert service.plan("FR-LAW")["config"] == fr_before["config"]

        proposals = service.list_tuning_proposals(limit=20)
        assert proposals["summary"]["applied"] == 1
        assert proposals["summary"]["rejected"] == 1

    runtime = Path("app/platform_server.py").read_text(encoding="utf-8")
    page = Path("static/collection.html").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/quality.yml").read_text(encoding="utf-8")
    assert '"/api/collection/site-tuning-proposals"' in runtime
    assert '"/api/collection/site-tuning-proposal/generate"' in runtime
    assert '"/api/collection/site-tuning-proposal/decision"' in runtime
    assert "规则修改候选" in page
    assert "确认并应用" in page
    assert "Stage42 tuning proposal validation tests" in workflow

    print("OK: stage42 tuning proposal validation and guarded apply passed")


if __name__ == "__main__":
    main()
