from __future__ import annotations

import tempfile
from pathlib import Path

from app.site_extraction import SiteExtractionService
from app.source_registry import SourceRegistryService
from app.store import KnowledgeStore


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = KnowledgeStore(root / "deep.db")
        registry = SourceRegistryService(store)

        pages = {
            "https://example.test/list?page=1": (
                b"""
                <html><head><title>Official Laws</title></head><body>
                <a href="/detail/1">Regulation 2026/100</a>
                <a href="/detail/2">Directive 2026/20</a>
                <a href="/list?page=2">Next</a>
                </body></html>
                """,
                "text/html; charset=utf-8",
            ),
            "https://example.test/list?page=2": (
                b"""
                <html><body>
                <a href="/detail/3">Decision 2026/30</a>
                <a href="/docs/annex.pdf">Download PDF Annex</a>
                </body></html>
                """,
                "text/html",
            ),
            "https://example.test/detail/1": (
                b"<html><title>Regulation 2026/100</title><h1>Regulation 2026/100</h1><p>Effective 2026-08-01. Current rule.</p></html>",
                "text/html",
            ),
            "https://example.test/detail/2": (
                b"<html><title>Directive 2026/20</title><h1>Directive 2026/20</h1><p>Effective 2026-09-02. In force.</p></html>",
                "text/html",
            ),
            "https://example.test/detail/3": (
                b"<html><title>Decision 2026/30</title><h1>Decision 2026/30</h1><p>Published 2026-09-03. Current.</p></html>",
                "text/html",
            ),
            "https://example.test/docs/annex.pdf": (
                b"%PDF-test-annex",
                "application/pdf",
            ),
        }

        def fetcher(url: str, headers: dict[str, str], timeout: int, max_bytes: int):
            if url not in pages:
                raise ValueError(f"unexpected url {url}")
            body, content_type = pages[url]
            return body, 200, content_type

        service = SiteExtractionService(
            store,
            registry,
            root / "downloads",
            fetcher=fetcher,
        )
        plans = service.list_plans()
        assert plans["summary"]["plans"] == 50
        assert plans["summary"]["enabled"] == 50

        plan = service.plan("EU-EURLEX")
        config = plan["config"]
        config["start_urls"] = ["https://example.test/list?page=1"]
        config["limits"] = {
            "max_pages": 6,
            "max_items": 20,
            "max_details": 5,
            "max_attachments": 2,
        }
        config["discovery"]["same_host"] = True
        config["discovery"]["follow_details"] = True
        config["discovery"]["fetch_attachments"] = True
        config["discovery"]["include_url_patterns"] = [r"/detail/"]
        config["discovery"]["detail_text_keywords"] = ["regulation", "directive", "decision"]
        config["pagination"]["next_text_keywords"] = ["next"]
        config["pagination"]["url_patterns"] = [r"[?&]page=\d+"]
        config["pagination"]["max_pages"] = 3
        config["fields"] = {
            "code": [r"\b(?:Regulation|Directive|Decision)\s+\d{4}/\d+\b"],
            "date": [r"\b20\d{2}-\d{2}-\d{2}\b"],
            "status": [r"\b(?:current|in force|effective)\b"],
        }
        saved = service.update_plan("EU-EURLEX", config=config, enabled=True)
        assert saved["origin"] == "custom"
        assert saved["config"]["limits"]["max_pages"] == 6

        run = service.run("EU-EURLEX", auto_ingest=False)
        assert run["status"] == "completed"
        assert run["pages_fetched"] == 6
        assert run["attachments_discovered"] == 1
        assert run["items_discovered"] == 6

        items = service.list_items(source_key="EU-EURLEX", limit=20)
        assert items["summary"]["details"] == 3
        assert items["summary"]["attachments"] == 1
        assert items["summary"]["documents"] == 0
        detail = next(item for item in items["items"] if item["item_type"] == "detail" and "/detail/1" in item["source_url"])
        assert detail["title"] == "Regulation 2026/100"
        assert detail["fields"]["code"] == ["Regulation 2026/100"]
        assert "2026-08-01" in detail["fields"]["date"]

        second = service.run("EU-EURLEX", max_pages=2, auto_ingest=False)
        assert second["status"] == "completed"
        assert second["pages_fetched"] == 2
        recent = service.list_runs(source_key="EU-EURLEX", limit=10)
        assert recent["summary"]["success"] == 2

        def blocked(*args):
            raise ValueError("HTTP 403")
        service.fetcher = blocked
        failed = service.run("EU-EURLEX")
        assert failed["status"] == "failed" and failed["pages_fetched"] == 0
        def partially_blocked(url, *args):
            if "/detail/1" in url:
                raise ValueError("HTTP 403")
            return fetcher(url, *args)
        service.fetcher = partially_blocked
        partial = service.run("EU-EURLEX")
        assert partial["status"] == "partial" and partial["pages_fetched"] == 5

    page = Path("static/collection.html").read_text(encoding="utf-8")
    runtime = Path("app/platform_server.py").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/quality.yml").read_text(encoding="utf-8")
    assert "站点级深采集与规则定制" in page
    assert "当前站点采集规则" in page
    assert "/api/collection/site-plans" in page
    assert '"/api/collection/site-plans"' in runtime
    assert '"/api/collection/site-plan/update"' in runtime
    assert '"/api/collection/site-crawl/run"' in runtime
    assert "Stage36 configurable deep site extraction tests" in workflow

    print("OK: stage36 per-site pagination/detail/attachment extraction and customization passed")


if __name__ == "__main__":
    main()
