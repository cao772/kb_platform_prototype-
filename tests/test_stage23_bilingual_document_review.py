from __future__ import annotations

import json
import tempfile
from pathlib import Path

from app.document_translation import DocumentTranslationService
from app.source_collection import SourceCollectionService
from app.source_registry import SourceRegistryService
from app.store import KnowledgeStore


PAYLOAD = b"""<?xml version="1.0" encoding="UTF-8"?>
<law>
  <id>TEST-001</id>
  <title>Product Safety Requirement</title>
  <article>Product must comply with electrical safety requirements.</article>
</law>
"""


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = KnowledgeStore(root / "document_translation.db")
        registry = SourceRegistryService(store)

        def fetcher(url: str, headers: dict[str, str], timeout: int, max_bytes: int):
            return PAYLOAD, 200, "application/xml"

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
        run = collection.run(profile["id"], auto_ingest=True)
        document_id = int(run["document_id"])
        original_path = Path(run["saved_path"])
        original_bytes = original_path.read_bytes()

        service = DocumentTranslationService(store)
        initial = service.detail(document_id)
        assert initial["source_preserved"] is True
        assert initial["summary"]["segments"] >= 1
        assert initial["summary"]["pending"] >= 1
        assert service.raw_path(document_id) == original_path

        def translator(texts: list[str], target_language: str):
            assert target_language == "zh-CN"
            output = []
            for text in texts:
                output.append(
                    text.replace("Product Safety Requirement", "产品安全要求")
                        .replace(
                            "Product must comply with electrical safety requirements.",
                            "产品必须符合电气安全要求。",
                        )
                )
            return output

        translated = service.translate(
            document_id,
            target_language="zh-CN",
            translator=translator,
        )
        assert translated["summary"]["translated"] == translated["summary"]["segments"]
        assert translated["translation"]["translated_now"] >= 1
        assert any("产品" in item["translated_text"] for item in translated["segments"])

        editable = []
        for item in translated["segments"]:
            value = item["translated_text"]
            if "产品必须符合电气安全要求" in value:
                value = value.replace("电气安全要求", "电气安全要求（人工校对）")
            editable.append({
                "chunk_id": item["chunk_id"],
                "translated_text": value,
            })

        draft = service.save_review(
            document_id,
            target_language="zh-CN",
            segments=editable,
            action="save",
            operator="法规翻译复核员",
            note="第一轮人工校对",
        )
        assert draft["review"]["review_status"] == "draft"
        assert draft["review"]["operator"] == "法规翻译复核员"
        assert any("人工校对" in item["translated_text"] for item in draft["segments"])

        confirmed = service.save_review(
            document_id,
            target_language="zh-CN",
            segments=editable,
            action="confirm",
            operator="法规翻译复核员",
            note="已对照原文确认",
        )
        assert confirmed["review"]["review_status"] == "confirmed"
        assert confirmed["review"]["confirmed_at"]

        # Translation is derived data; original file and parsed source text remain unchanged.
        assert original_path.read_bytes() == original_bytes
        with store.lock:
            doc = store.conn.execute(
                "SELECT full_text,metadata FROM documents WHERE id=?",
                (document_id,),
            ).fetchone()
        metadata = json.loads(doc["metadata"] or "{}")
        assert metadata["original_content_preserved"] is True
        assert "Product must comply" in doc["full_text"]

    page = Path("static/source_document.html").read_text(encoding="utf-8")
    collection_page = Path("static/collection.html").read_text(encoding="utf-8")
    runtime = Path("app/platform_server.py").read_text(encoding="utf-8")
    code = Path("app/document_translation.py").read_text(encoding="utf-8")

    assert "原始文件、原始解析文本和原始分片永久保留" in page
    assert "原文" in page and "中文" in page
    assert "查看原始文件" in page
    assert "生成中文翻译" in page
    assert "确认双语内容" in page
    assert "/api/source-document/translate" in page
    assert "/api/source-document/review" in page
    assert "/source-document?id=" in collection_page
    assert '"/api/source-document/raw"' in runtime
    assert '"/source-document", "/source-document.html"' in runtime
    assert "document_translation_segments" in code
    assert "document_translation_reviews" in code

    print("OK: stage23 bilingual original-document review passed")


if __name__ == "__main__":
    main()
