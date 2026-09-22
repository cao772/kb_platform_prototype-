from __future__ import annotations

import tempfile
from pathlib import Path

from app.site_extraction import SiteExtractionService
from app.source_registry import SourceRegistryService
from app.store import KnowledgeStore


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = KnowledgeStore(root / "stage40.db")
        service = SiteExtractionService(
            store,
            SourceRegistryService(store),
            root / "downloads",
        )

        changed = service._upsert_item(
            source_key="EU-EURLEX",
            url="https://example.test/legal-content/100",
            item_type="detail",
            title="Regulation 2026/100",
            content_type="text/html",
            sha256="sha-1",
            fields={},
            metadata={
                "relevance": {
                    "status": "needs_review",
                    "reasons": ["no_business_signal"],
                }
            },
            text_excerpt="General regulatory page",
            raw_path="",
            document_id=None,
        )
        assert changed

        first = service.list_items(source_key="EU-EURLEX", limit=20)
        item = first["items"][0]
        assert item["effective_relevance"]["status"] == "needs_review"
        assert item["effective_relevance"]["origin"] == "rule"

        reviewed = service.review_item(
            item["id"],
            status="irrelevant",
            operator="tester",
            note="navigation content",
        )
        assert reviewed["effective_relevance"]["status"] == "irrelevant"
        assert reviewed["effective_relevance"]["origin"] == "human"
        assert reviewed["metadata"]["relevance"]["status"] == "needs_review"

        # A later recrawl can change the automatic result, but it must not
        # overwrite the human review already captured for the same canonical URL.
        service._upsert_item(
            source_key="EU-EURLEX",
            url="https://example.test/legal-content/100",
            item_type="detail",
            title="Regulation 2026/100",
            content_type="text/html",
            sha256="sha-2",
            fields={"code": ["Regulation 2026/100"]},
            metadata={
                "relevance": {
                    "status": "relevant",
                    "reasons": ["structured_field_match"],
                }
            },
            text_excerpt="Regulation 2026/100 current rule",
            raw_path="",
            document_id=None,
        )
        after_recrawl = service.item_detail(item["id"])
        assert after_recrawl["metadata"]["relevance"]["status"] == "relevant"
        assert after_recrawl["effective_relevance"]["status"] == "irrelevant"
        assert after_recrawl["effective_relevance"]["origin"] == "human"

        overview = service.relevance_overview(source_key="EU-EURLEX")
        assert overview["summary"]["sources"] == 1
        assert overview["summary"]["items"] == 1
        assert overview["summary"]["irrelevant"] == 1
        assert overview["summary"]["reviewed"] == 1
        assert overview["items"][0]["review_rate"] == 100.0

        service.review_item(
            item["id"],
            status="relevant",
            operator="tester",
            note="confirmed official regulation",
        )
        final_overview = service.relevance_overview(source_key="EU-EURLEX")
        assert final_overview["summary"]["relevant"] == 1
        assert final_overview["items"][0]["relevant_rate"] == 100.0

    runtime = Path("app/platform_server.py").read_text(encoding="utf-8")
    page = Path("static/collection.html").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/quality.yml").read_text(encoding="utf-8")
    assert '"/api/collection/site-relevance-overview"' in runtime
    assert '"/api/collection/site-item/review"' in runtime
    assert "首批站点相关性质量" in page
    assert "reviewDeepItem" in page
    assert "Stage40 relevance review loop tests" in workflow

    print("OK: stage40 relevance review persists across recrawls and drives site metrics")


if __name__ == "__main__":
    main()
