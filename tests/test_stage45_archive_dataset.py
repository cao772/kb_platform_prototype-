from __future__ import annotations

import io
import tarfile
import tempfile
from pathlib import Path

from app.site_extraction import SiteExtractionService
from app.source_registry import SourceRegistryService
from app.store import KnowledgeStore


def build_archive() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:bz2") as archive:
        files = {
            "nl/law-001.xml": b"<law><title>Product Safety Act</title><body>Current product safety regulation 2026-01-01.</body></law>",
            "sf/reg-002.xml": b"<regulation><title>Electrical Product Regulation</title><body>Current safety requirement 2026-02-02.</body></regulation>",
            "../escape.xml": b"<law>must never be extracted</law>",
            "binary/image.bin": b"not relevant",
        }
        for name, body in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
    return buffer.getvalue()


def main() -> None:
    archive_url = "https://official.example/open/current-laws.tar.bz2"
    start_url = "https://official.example/open/"
    archive_body = build_archive()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = KnowledgeStore(root / "archive.db")
        registry = SourceRegistryService(store)

        def fetcher(url: str, headers: dict[str, str], timeout: int, max_bytes: int):
            if url == archive_url:
                return archive_body, 200, "application/x-bzip2"
            if url == start_url:
                return b"<html><title>Official open legal data</title><body>Open legal dataset</body></html>", 200, "text/html"
            raise ValueError(f"unexpected url: {url}")

        service = SiteExtractionService(
            store,
            registry,
            root / "downloads",
            fetcher=fetcher,
        )
        plan = service.plan("NO-LAW")
        config = plan["config"]
        config["start_urls"] = [start_url]
        config["limits"]["max_pages"] = 5
        config["limits"]["max_items"] = 20
        config["request"]["max_bytes"] = 20 * 1024 * 1024
        config["archives"] = {
            "enabled": True,
            "urls": [archive_url],
            "member_extensions": [".xml"],
            "max_archives": 2,
            "max_members": 10,
            "max_member_bytes": 1024 * 1024,
            "max_total_member_bytes": 4 * 1024 * 1024,
            "trusted_scope": True,
        }
        service.update_plan("NO-LAW", config=config, enabled=True)

        run = service.run("NO-LAW", auto_ingest=False)
        assert run["status"] == "completed"
        assert run["pages_fetched"] == 2
        assert run["items_discovered"] == 4
        assert run["changed_items"] == 4

        items = service.list_items(source_key="NO-LAW", limit=20)
        details = [item for item in items["items"] if item["item_type"] == "detail"]
        assert len(details) == 2
        assert all(item["effective_relevance"]["status"] == "relevant" for item in details)
        assert all("archive_member=" in item["source_url"] for item in details)
        assert all(item["metadata"]["archive_dataset"] is True for item in details)
        assert {item["metadata"]["archive_member"] for item in details} == {
            "nl/law-001.xml",
            "sf/reg-002.xml",
        }
        assert not any("escape.xml" in item["source_url"] for item in items["items"])
        assert all(Path(item["raw_path"]).is_file() for item in details)

        # Same archive/member hashes must remain stable on subsequent runs.
        second = service.run("NO-LAW", auto_ingest=False)
        assert second["status"] == "completed"
        assert second["changed_items"] == 0
        second_items = service.list_items(source_key="NO-LAW", limit=20)
        assert second_items["summary"]["details"] == 2

        # Config validation caps unsafe archive settings and rejects unsupported members.
        clean = service.plan("NO-LAW")["config"]
        assert clean["archives"]["trusted_scope"] is True
        assert clean["archives"]["member_extensions"] == [".xml"]

    source = Path("app/site_extraction.py").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/quality.yml").read_text(encoding="utf-8")
    assert "_iter_archive_members" in source
    assert "_safe_archive_member_name" in source
    assert "trusted_official_archive_scope" in source
    assert "Stage45 archive dataset tests" in workflow

    print("OK: stage45 reusable official archive dataset extraction passed")


if __name__ == "__main__":
    main()
