from __future__ import annotations

import json
import tempfile
from pathlib import Path

from app.source_collection import SourceCollectionService
from app.source_diff import SourceDifferenceService
from app.source_registry import SourceRegistryService
from app.store import KnowledgeStore


V1 = b"""<?xml version="1.0" encoding="UTF-8"?>
<law>
  <id>REG-1</id>
  <version>Version 1</version>
  <requirement>Product must comply with electrical safety requirements.</requirement>
</law>
"""

V2 = b"""<?xml version="1.0" encoding="UTF-8"?>
<law>
  <id>REG-1</id>
  <version>Version 2</version>
  <requirement>Product must comply with electrical safety and EMC requirements.</requirement>
</law>
"""


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = KnowledgeStore(root / "bilingual.db")
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
        first_path = Path(first["saved_path"])
        assert first_path.read_bytes() == V1

        with store.lock:
            first_doc = store.conn.execute(
                "SELECT metadata,full_text FROM documents WHERE id=?",
                (int(first["document_id"]),),
            ).fetchone()
        metadata = json.loads(first_doc["metadata"] or "{}")
        assert metadata["original_content_preserved"] is True
        assert metadata["translation_policy"] == "source_original_plus_zh-CN"
        assert metadata["original_file_path"] == str(first_path.resolve())
        original_parsed_text = first_doc["full_text"]

        state["payload"] = V2
        second = collection.run(profile["id"], auto_ingest=True)
        second_path = Path(second["saved_path"])
        assert second_path.read_bytes() == V2
        assert first_path.read_bytes() == V1
        assert first_path != second_path

        event = next(
            item for item in collection.list_updates()["items"]
            if item["change_type"] == "changed"
        )
        diff = SourceDifferenceService(store)
        review = diff.review_state(event["id"])

        def translator(texts: list[str], target_language: str):
            assert target_language == "zh-CN"
            output = []
            for text in texts:
                output.append(
                    text.replace("Version 1", "Version 1（版本1）")
                        .replace("Version 2", "Version 2（版本2）")
                        .replace(
                            "Product must comply with electrical safety requirements.",
                            "产品必须符合电气安全要求。",
                        )
                        .replace(
                            "Product must comply with electrical safety and EMC requirements.",
                            "产品必须符合电气安全和EMC要求。",
                        )
                )
            return output

        bilingual = diff.translate_review_items(
            event["id"],
            items=review["items"],
            target_language="zh-CN",
            translator=translator,
        )
        assert bilingual["source_preserved"] is True
        assert bilingual["target_language"] == "zh-CN"
        assert bilingual["items"]
        assert any(item.get("before_zh") or item.get("after_zh") for item in bilingual["items"])

        requirement_item = next(
            item for item in bilingual["items"]
            if item["field"] == "requirement" and item["change_type"] in {"added", "removed"}
        )
        if requirement_item.get("after"):
            assert "产品" in requirement_item["after_zh"]
            assert requirement_item["after"] != requirement_item["after_zh"]
            requirement_item["after_zh"] = requirement_item["after_zh"].replace("EMC", "电磁兼容（EMC）")
        else:
            assert "产品" in requirement_item["before_zh"]
            assert requirement_item["before"] != requirement_item["before_zh"]
            requirement_item["before_zh"] = requirement_item["before_zh"].replace("电气安全", "电气安全要求")

        saved = diff.save_review(
            event["id"],
            items=bilingual["items"],
            action="save",
            operator="双语复核员",
            note="已核对原文并修正中文译文",
        )
        saved_requirement = next(
            item for item in saved["items"]
            if item["item_id"] == requirement_item["item_id"]
        )
        assert saved_requirement.get("before_zh") or saved_requirement.get("after_zh")

        # Bilingual review is a derivative layer; original evidence is immutable.
        assert first_path.read_bytes() == V1
        with store.lock:
            first_doc_after = store.conn.execute(
                "SELECT full_text FROM documents WHERE id=?",
                (int(first["document_id"]),),
            ).fetchone()
        assert first_doc_after["full_text"] == original_parsed_text

    page = Path("static/source_diff.html").read_text(encoding="utf-8")
    runtime = Path("app/platform_server.py").read_text(encoding="utf-8")
    collection_code = Path("app/source_collection.py").read_text(encoding="utf-8")
    diff_code = Path("app/source_diff.py").read_text(encoding="utf-8")
    gateway = Path("app/model_gateway.py").read_text(encoding="utf-8")

    assert "原始文件和原始解析文本始终保留" in page
    assert "原文 + 中文" in page
    assert "生成中文翻译" in page
    assert "重新翻译" in page
    assert "before_zh" in page and "after_zh" in page
    assert "/api/collection/diff-translate" in page
    assert '"/api/collection/diff-translate"' in runtime
    assert "original_content_preserved" in collection_code
    assert "translation_policy" in collection_code
    assert "translate_review_items" in diff_code
    assert "call_translation_model" in gateway

    print("OK: stage22 immutable source evidence and editable bilingual review passed")


if __name__ == "__main__":
    main()
