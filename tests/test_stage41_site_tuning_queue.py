from __future__ import annotations

import json
import tempfile
from pathlib import Path

from app.site_extraction import SiteExtractionService
from app.source_registry import SourceRegistryService
from app.store import KnowledgeStore


def seed_run(store: KnowledgeStore, source_key: str, status: str, pages: int = 1, error: str = "") -> None:
    with store.lock:
        store.conn.execute(
            """INSERT INTO source_deep_runs(
                   source_key,status,started_at,finished_at,pages_fetched,
                   items_discovered,attachments_discovered,documents_ingested,
                   changed_items,error
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                source_key,
                status,
                "2026-09-22T00:00:00+00:00",
                "2026-09-22T00:01:00+00:00",
                pages,
                3,
                0,
                0,
                3,
                error,
            ),
        )
        store.conn.commit()


def add_item(
    service: SiteExtractionService,
    source_key: str,
    suffix: str,
    automatic_status: str,
) -> int:
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
        text_excerpt="regulatory test content",
        raw_path="",
        document_id=None,
    )
    return service.list_items(source_key=source_key, limit=20)["items"][0]["id"]


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = KnowledgeStore(root / "stage41.db")
        service = SiteExtractionService(
            store,
            SourceRegistryService(store),
            root / "downloads",
        )

        # Failed crawl + one over-capture + one under-capture + one unresolved
        # review item should create a high-priority, explainable tuning case.
        seed_run(store, "DE-LAW", "failed", pages=0, error="request blocked")
        over_id = add_item(service, "DE-LAW", "noise", "relevant")
        service.review_item(
            over_id,
            status="irrelevant",
            operator="tester",
            note="navigation page",
        )
        under_id = add_item(service, "DE-LAW", "law", "needs_review")
        service.review_item(
            under_id,
            status="relevant",
            operator="tester",
            note="official regulation",
        )
        add_item(service, "DE-LAW", "pending", "needs_review")

        # A completed site with matching human feedback and no unresolved
        # samples should remain low priority.
        seed_run(store, "EU-EURLEX", "completed", pages=3)
        stable_id = add_item(service, "EU-EURLEX", "regulation", "relevant")
        service.review_item(
            stable_id,
            status="relevant",
            operator="tester",
            note="confirmed regulation",
        )

        queue = service.tuning_queue()
        assert queue["summary"]["sources"] == 50
        assert queue["summary"]["not_run"] == 48
        assert queue["summary"]["failed"] == 1
        assert queue["summary"]["disagreements"] == 2

        by_key = {item["source_key"]: item for item in queue["items"]}
        de = by_key["DE-LAW"]
        assert de["priority"] == "high"
        assert de["latest_run_status"] == "failed"
        assert de["over_capture"] == 1
        assert de["under_capture"] == 1
        assert de["needs_review"] == 1
        assert de["disagreements"] == 2
        joined = " ".join(de["suggestions"])
        assert "收紧" in joined
        assert "扩展" in joined
        assert "复核" in joined

        eurlex = by_key["EU-EURLEX"]
        assert eurlex["priority"] == "low"
        assert eurlex["relevant_rate"] == 100.0
        assert eurlex["review_rate"] == 100.0
        assert eurlex["disagreement_rate"] == 0.0
        assert any("稳定" in item for item in eurlex["suggestions"])

        # The queue is deterministic: higher score first, then first-wave order.
        scores = [int(item["priority_score"]) for item in queue["items"]]
        assert scores == sorted(scores, reverse=True)

    runtime = Path("app/platform_server.py").read_text(encoding="utf-8")
    page = Path("static/collection.html").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/quality.yml").read_text(encoding="utf-8")
    assert '"/api/collection/site-tuning-queue"' in runtime
    assert "逐站调优队列" in page
    assert "loadTuningQueue" in page
    assert "Stage41 site tuning queue tests" in workflow

    print("OK: stage41 deterministic site tuning queue passed")


if __name__ == "__main__":
    main()
