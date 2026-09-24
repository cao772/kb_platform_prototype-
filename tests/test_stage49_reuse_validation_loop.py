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
        store = KnowledgeStore(root / "stage49.db")
        registry = SourceRegistryService(store)
        site = SiteExtractionService(store, registry, root / "downloads")
        service = CollectionExperienceService(registry, site)

        preview = service.preview_template("FR-LAW", "legal_portal_fulltext")
        applied = service.apply_template(
            "FR-LAW",
            "legal_portal_fulltext",
            expected_base_hash=preview["base_hash"],
            operator="stage49-test",
            note="validate reuse loop",
        )
        application_id = int(applied["application_id"])

        before = service.reuse_outcomes(source_key="FR-LAW")
        assert before["summary"]["applications"] == 1
        assert before["summary"]["awaiting_trial"] == 1
        first = before["items"][0]
        assert first["application_id"] == application_id
        assert first["auto_outcome"] == "awaiting_trial"
        assert first["trial_run_id"] == 0
        assert first["needs_human_review"] is False

        site._upsert_item(
            source_key="FR-LAW",
            url="https://example.test/fr/law/stage49",
            item_type="detail",
            title="Stage49 law",
            content_type="text/html",
            sha256="stage49-fr-law",
            fields={},
            metadata={"relevance": {"status": "relevant", "reasons": ["business_keyword"]}},
            text_excerpt="official law content",
            raw_path="",
            document_id=None,
        )
        with store.lock:
            cur = store.conn.execute(
                """INSERT INTO source_deep_runs(
                       source_key,status,started_at,finished_at,pages_fetched,
                       items_discovered,attachments_discovered,documents_ingested,
                       changed_items,error
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    "FR-LAW",
                    "completed",
                    "2026-09-24T03:00:00+00:00",
                    "2026-09-24T03:01:00+00:00",
                    2,
                    1,
                    0,
                    0,
                    1,
                    "",
                ),
            )
            run_id = int(cur.lastrowid)
            store.conn.commit()

        after = service.reuse_outcomes(source_key="FR-LAW")
        item = after["items"][0]
        assert item["trial_run_id"] == run_id
        assert item["auto_outcome"] == "direct_reuse"
        assert item["effective_outcome"] == "direct_reuse"
        assert item["needs_human_review"] is True
        assert after["summary"]["direct_reuse"] == 1
        assert after["summary"]["human_reviewed"] == 0

        reviewed = service.save_reuse_outcome(
            application_id,
            outcome="minor_adjustment",
            operator="reviewer",
            note="keep template but tune one rule",
        )
        assert reviewed["auto_outcome"] == "direct_reuse"
        assert reviewed["human_outcome"] == "minor_adjustment"
        assert reviewed["effective_outcome"] == "minor_adjustment"

        final = service.reuse_outcomes(source_key="FR-LAW")
        item = final["items"][0]
        assert item["human_outcome"] == "minor_adjustment"
        assert item["effective_outcome"] == "minor_adjustment"
        assert item["needs_human_review"] is False
        assert final["summary"]["minor_adjustment"] == 1
        assert final["summary"]["human_reviewed"] == 1
        assert final["governance"]["automatic_outcome_is_advisory"] is True
        assert final["governance"]["formal_knowledge_auto_promotion"] is False

        try:
            service.save_reuse_outcome(
                application_id,
                outcome="invalid",
                operator="reviewer",
            )
            raise AssertionError("invalid outcome should fail")
        except ValueError:
            pass

    runtime = Path("app/platform_server.py").read_text(encoding="utf-8")
    page = Path("static/collection_onboarding.html").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/quality.yml").read_text(encoding="utf-8")

    assert '"/api/collection-experience/reuse-outcomes"' in runtime
    assert '"/api/collection-experience/reuse-outcome-review"' in runtime
    assert "复用效果验证" in page
    assert "可直接复用" in page
    assert "需小幅调整" in page
    assert "需重新处理" in page
    assert "/api/collection-experience/reuse-outcomes" in page
    assert "/api/collection-experience/reuse-outcome-review" in page
    assert "Stage49 reuse validation loop tests" in workflow

    print("OK: stage49 validates template reuse with post-application trials and human confirmation")


if __name__ == "__main__":
    main()
