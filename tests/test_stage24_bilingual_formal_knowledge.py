from __future__ import annotations

import json
import tempfile
from pathlib import Path

from app.formal_translation import FormalKnowledgeTranslationService
from app.store import KnowledgeStore


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = KnowledgeStore(Path(tmp) / "formal_translation.db")
        doc_id = store.upsert_document(
            filename="regulation.html",
            title="Product Safety Regulation",
            knowledge_type="法规知识",
            tags=["US"],
            source_path="regulation.html",
            full_text="Section 1. Products must comply with electrical safety requirements.",
            chunks=[{
                "chunk_index": 0,
                "text": "Section 1. Products must comply with electrical safety requirements.",
                "metadata": {},
            }],
        )
        chunk_id = store.document_chunks(doc_id)[0]["id"]
        with store.lock:
            record_id = store._upsert_compliance_record_locked({
                "record_type": "regulation",
                "code": "REG-100",
                "name": "Product Safety Regulation",
                "region_code": "US",
                "region_name": "美国",
                "product_class": "电气产品",
                "status": "active",
                "version": "2026",
                "effective_from": "2026-01-01",
                "source_document_id": doc_id,
                "source_chunk_id": chunk_id,
                "attributes": {
                    "authority": "Product Safety Authority",
                    "applicability_scope": "Electrical products placed on the market.",
                    "exceptions": "None.",
                    "transition_note": "",
                    "legal_effect": "Mandatory.",
                    "review_basis": "Official source document.",
                },
            })
            store.conn.commit()

        service = FormalKnowledgeTranslationService(store)
        initial = service.detail(record_id)
        assert initial["source_preserved"] is True
        assert initial["fields"]["name"]["source"] == "Product Safety Regulation"
        assert initial["fields"]["name"]["translated"] == ""
        assert "Products must comply" in initial["fields"]["evidence_excerpt"]["source"]

        def translator(texts: list[str], target_language: str):
            assert target_language == "zh-CN"
            mapping = {
                "Product Safety Regulation": "产品安全法规",
                "Product Safety Authority": "产品安全主管机构",
                "Electrical products placed on the market.": "投放市场的电气产品。",
                "None.": "无。",
                "Mandatory.": "强制性。",
                "Official source document.": "官方来源文件。",
                "Section 1. Products must comply with electrical safety requirements.": "第1条：产品必须符合电气安全要求。",
            }
            return [mapping.get(text, text) for text in texts]

        translated = service.translate(
            record_id,
            target_language="zh-CN",
            translator=translator,
        )
        assert translated["fields"]["name"]["translated"] == "产品安全法规"
        assert "电气安全" in translated["fields"]["evidence_excerpt"]["translated"]
        assert translated["review"]["review_status"] == "draft"

        translations = {
            key: value["translated"]
            for key, value in translated["fields"].items()
        }
        translations["name"] = "产品安全法规（人工校对）"
        translations["legal_effect"] = "强制性要求"

        confirmed = service.save_review(
            record_id,
            target_language="zh-CN",
            translations=translations,
            action="confirm",
            operator="法规业务人员",
            note="已对照原文确认中文翻译",
        )
        assert confirmed["review"]["review_status"] == "confirmed"
        assert confirmed["review"]["confirmed_at"]
        assert confirmed["review"]["operator"] == "法规业务人员"
        assert confirmed["fields"]["name"]["translated"] == "产品安全法规（人工校对）"
        assert confirmed["fields"]["legal_effect"]["translated"] == "强制性要求"

        # Formal record and source evidence remain unchanged.
        records = store.list_compliance_records(review_status="approved", limit=20)
        record = next(item for item in records if int(item["id"]) == int(record_id))
        assert record["name"] == "Product Safety Regulation"
        assert record["attributes"]["legal_effect"] == "Mandatory."
        with store.lock:
            chunk = store.conn.execute("SELECT text FROM chunks WHERE id=?", (chunk_id,)).fetchone()
        assert chunk["text"] == "Section 1. Products must comply with electrical safety requirements."

        # If the formal source field changes, the bilingual layer flags it for re-review.
        with store.lock:
            attrs = dict(record["attributes"])
            attrs["legal_effect"] = "Mandatory with transition."
            store.conn.execute(
                "UPDATE compliance_records SET attributes=? WHERE id=?",
                (json.dumps(attrs, ensure_ascii=False), record_id),
            )
            store.conn.commit()
        changed = service.detail(record_id)
        assert changed["fields"]["legal_effect"]["source_changed"] is True

    catalog = Path("static/catalog.html").read_text(encoding="utf-8")
    runtime = Path("app/platform_server.py").read_text(encoding="utf-8")
    code = Path("app/formal_translation.py").read_text(encoding="utf-8")

    assert "双语内容" in catalog
    assert "原名称" in catalog
    assert "中文名称" in catalog
    assert "生成中文翻译" in catalog
    assert "确认双语内容" in catalog
    assert "/api/formal-translation" in catalog
    assert '"/api/formal-translation"' in runtime
    assert '"/api/formal-translation/translate"' in runtime
    assert '"/api/formal-translation/review"' in runtime
    assert "formal_knowledge_translations" in code
    assert "source_preserved" in code

    print("OK: stage24 bilingual formal knowledge governance passed")


if __name__ == "__main__":
    main()
