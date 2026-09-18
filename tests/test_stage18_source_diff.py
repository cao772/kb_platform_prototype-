from __future__ import annotations

import tempfile
from pathlib import Path

from app.source_collection import SourceCollectionService
from app.source_diff import SourceDifferenceService
from app.source_registry import SourceRegistryService
from app.store import KnowledgeStore


V1 = b"""<?xml version="1.0" encoding="UTF-8"?>
<law>
  <id>TEST-REG-1</id>
  <version>Version 1</version>
  <effective>2026-01-01</effective>
  <requirement>Product must comply with electrical safety requirements.</requirement>
  <status>effective</status>
</law>
"""

V2 = b"""<?xml version="1.0" encoding="UTF-8"?>
<law>
  <id>TEST-REG-1</id>
  <version>Version 2</version>
  <effective>2027-01-01</effective>
  <requirement>Product must comply with electrical safety and EMC requirements.</requirement>
  <label>Energy label is required before market placement.</label>
  <status>effective</status>
</law>
"""


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = KnowledgeStore(root / "diff.db")
        registry = SourceRegistryService(store)
        state = {"payload": V1}

        def fetcher(url: str, headers: dict[str, str], timeout: int, max_bytes: int):
            return state["payload"], 200, "application/xml"

        collection = SourceCollectionService(
            store,
            registry,
            root / "downloads",
            fetcher=fetcher,
        )
        profile = next(
            item for item in collection.list_profiles()["items"]
            if item["profile_key"] == "JP-EGOV-LAWLIST-XML"
        )
        first = collection.run(profile["id"], auto_ingest=True)
        assert first["content_status"] == "initial"

        state["payload"] = V2
        second = collection.run(profile["id"], auto_ingest=True)
        assert second["content_status"] == "changed"

        updates = collection.list_updates()
        changed = next(item for item in updates["items"] if item["change_type"] == "changed")

        diff = SourceDifferenceService(store)
        report = diff.analyze(changed["id"])
        assert report["review_status"] == "needs_human_review"
        assert report["previous"]["document_id"] == first["document_id"]
        assert report["current"]["document_id"] == second["document_id"]
        assert report["summary"]["change_blocks"] >= 1
        assert report["summary"]["signal_changes"] >= 1
        assert "2026-01-01" in report["signals"]["dates"]["removed"]
        assert "2027-01-01" in report["signals"]["dates"]["added"]
        assert any("Version 1" in value for value in report["signals"]["versions"]["removed"])
        assert any("Version 2" in value for value in report["signals"]["versions"]["added"])
        assert any(
            "EMC" in value or "Energy label" in value
            for value in report["signals"]["requirement_lines"]["added"]
        )
        assert any(block["change_type"] in {"modified", "added"} for block in report["blocks"])
        assert "不自动修改正式知识" in report["notice"]

        cached = diff.analyze(changed["id"])
        assert cached["current"]["sha256"] == report["current"]["sha256"]
        reports = diff.list_reports()
        assert reports["summary"]["reports"] == 1
        assert reports["items"][0]["update_event_id"] == changed["id"]

        with store.lock:
            approved_count = store.conn.execute(
                "SELECT COUNT(*) count FROM compliance_records"
            ).fetchone()["count"]
        assert approved_count == 0

    diff_page = Path("static/source_diff.html").read_text(encoding="utf-8")
    collection_page = Path("static/collection.html").read_text(encoding="utf-8")
    runtime = Path("app/platform_server.py").read_text(encoding="utf-8")
    code = Path("app/source_diff.py").read_text(encoding="utf-8")

    assert "来源版本差异" in diff_page
    assert "关键信息变化" in diff_page
    assert "上一版本" in diff_page and "当前版本" in diff_page
    assert "/api/collection/diff?event_id=" in diff_page
    assert "查看差异" in collection_page
    assert "/source-diff?event_id=" in collection_page
    assert '"/api/collection/diff"' in runtime
    assert '"/source-diff", "/source-diff.html"' in runtime
    assert "needs_human_review" in code

    print("OK: stage18 source version difference review passed")


if __name__ == "__main__":
    main()
