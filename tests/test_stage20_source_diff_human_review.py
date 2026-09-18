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
</law>
"""

V2 = b"""<?xml version="1.0" encoding="UTF-8"?>
<law>
  <id>TEST-REG-1</id>
  <version>Version 2</version>
  <effective>2027-01-01</effective>
  <requirement>Product must comply with electrical safety and EMC requirements.</requirement>
</law>
"""


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = KnowledgeStore(root / "human_review.db")
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
        state["payload"] = V2
        second = collection.run(profile["id"], auto_ingest=True)
        assert first["content_status"] == "initial"
        assert second["content_status"] == "changed"

        event = next(
            item for item in collection.list_updates()["items"]
            if item["change_type"] == "changed"
        )
        service = SourceDifferenceService(store)

        initial = service.review_state(event["id"])
        assert initial["review_status"] == "draft"
        assert initial["items"]
        version_item = next(item for item in initial["items"] if item["field"] == "version")
        assert "Version 1" in version_item["before"]
        assert "Version 2" in version_item["after"]

        edited_items = [dict(item) for item in initial["items"]]
        edited = next(item for item in edited_items if item["field"] == "version")
        edited["label"] = "法规版本"
        edited["after"] = "Version 2（人工确认）"
        edited["impact_note"] = "需核对认证路径是否引用旧版本"

        draft = service.save_review(
            event["id"],
            items=edited_items,
            action="save",
            operator="业务复核员",
            note="已完成第一轮人工核对",
        )
        assert draft["review_status"] == "draft"
        saved = next(item for item in draft["items"] if item["field"] == "version")
        assert saved["label"] == "法规版本"
        assert saved["after"] == "Version 2（人工确认）"
        assert "认证路径" in saved["impact_note"]

        confirmed = service.save_review(
            event["id"],
            items=draft["items"],
            action="confirm",
            operator="业务复核员",
            note="已对照原文确认",
        )
        assert confirmed["review_status"] == "confirmed"
        assert confirmed["confirmed_at"]
        assert confirmed["operator"] == "业务复核员"

        with store.lock:
            formal_count = store.conn.execute(
                "SELECT COUNT(*) count FROM compliance_records"
            ).fetchone()["count"]
            review_count = store.conn.execute(
                "SELECT COUNT(*) count FROM source_diff_reviews"
            ).fetchone()["count"]
        assert formal_count == 0
        assert review_count == 1

    page = Path("static/source_diff.html").read_text(encoding="utf-8")
    runtime = Path("app/platform_server.py").read_text(encoding="utf-8")
    code = Path("app/source_diff.py").read_text(encoding="utf-8")

    assert "人工复核与修改" in page
    assert "新增变化项" in page
    assert "保存草稿" in page
    assert "确认复核结果" in page
    assert "标记无需处理" in page
    assert "/api/collection/diff-review" in page
    assert '"/api/collection/diff-review"' in runtime
    assert "source_diff_reviews" in code
    assert "save_review" in code
    assert "review_state" in code

    print("OK: stage20 editable human source-difference review passed")


if __name__ == "__main__":
    main()
