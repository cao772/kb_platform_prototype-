from __future__ import annotations

import tempfile
from pathlib import Path

from app.formal_translation import FormalKnowledgeTranslationService
from app.gma_bilingual import GmaBilingualService
from app.graph_governance import GraphGovernanceService
from app.store import KnowledgeStore


def add_record(store: KnowledgeStore, *, record_type: str, code: str, name: str, region: str) -> int:
    with store.lock:
        rid = store._upsert_compliance_record_locked({
            "record_type": record_type,
            "code": code,
            "name": name,
            "region_code": region,
            "region_name": "欧盟" if region == "EU" else "德国",
            "product_class": "家用电器",
            "status": "active",
            "version": "2026",
            "effective_from": "2026-01-01",
            "effective_to": "",
            "source_document_id": None,
            "source_chunk_id": None,
            "attributes": {},
        })
        store.conn.commit()
    return int(rid)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = KnowledgeStore(Path(tmp) / "gma_bilingual.db")
        graph = GraphGovernanceService(store)
        translation = FormalKnowledgeTranslationService(store)
        regulation_id = add_record(
            store,
            record_type="regulation",
            code="EU-REG-1",
            name="European Product Safety Regulation",
            region="EU",
        )
        certification_id = add_record(
            store,
            record_type="certification",
            code="DE-CERT-1",
            name="German Market Certification",
            region="DE",
        )
        with store.lock:
            store.conn.execute(
                """INSERT INTO graph_relations(
                       source_record_id,relation_type,target_record_id,confidence,status,reviewer_note
                   ) VALUES(?,?,?,0.99,'approved','业务确认')""",
                (regulation_id, "REQUIRES", certification_id),
            )
            store.conn.commit()

        translation.save_review(
            regulation_id,
            translations={
                "name": "欧洲产品安全法规",
                "authority": "",
                "applicability_scope": "",
                "exceptions": "",
                "transition_note": "",
                "legal_effect": "",
                "review_basis": "",
                "evidence_excerpt": "",
            },
            action="confirm",
            operator="翻译复核员",
            note="确认中文名称",
        )
        translation.save_review(
            certification_id,
            translations={
                "name": "德国市场认证",
                "authority": "",
                "applicability_scope": "",
                "exceptions": "",
                "transition_note": "",
                "legal_effect": "",
                "review_basis": "",
                "evidence_excerpt": "",
            },
            action="confirm",
            operator="翻译复核员",
            note="确认中文名称",
        )

        before_count = store.stats()["compliance_records"]
        service = GmaBilingualService(store, graph, translation)
        result = service.path(
            region_code="DE",
            product_class="家用电器",
            as_of="2026-09-18",
        )
        assert result["region_code"] == "DE"
        assert result["region_name"] == "德国"
        assert result["knowledge_scope_codes"] == ["DE", "EU"]
        assert result["summary"]["paths"] >= 1
        assert result["bilingual_summary"]["records"] == 2
        assert result["bilingual_summary"]["with_chinese"] == 2
        assert result["bilingual_summary"]["confirmed_chinese"] == 2

        nodes = result["paths"][0]["nodes"]
        reg = next(node for node in nodes if node["id"] == regulation_id)
        cert = next(node for node in nodes if node["id"] == certification_id)
        assert reg["name_original"] == "European Product Safety Regulation"
        assert reg["name_zh"] == "欧洲产品安全法规"
        assert cert["name_original"] == "German Market Certification"
        assert cert["name_zh"] == "德国市场认证"
        assert reg["translation_status"] == "confirmed"
        assert cert["translation_status"] == "confirmed"

        # GMA is a derived view and must not duplicate formal facts.
        assert store.stats()["compliance_records"] == before_count

    page = Path("static/gma_path.html").read_text(encoding="utf-8")
    runtime = Path("app/platform_server.py").read_text(encoding="utf-8")
    code = Path("app/gma_bilingual.py").read_text(encoding="utf-8")
    assert "产品市场准入知识路径" in page
    assert "原名称与中文名称" in page
    assert "补齐中文" in page
    assert "/api/gma/path" in page
    assert '"/gma-path", "/gma-path.html"' in runtime
    assert '"/api/gma/path"' in runtime
    assert '"/api/gma/path/translate"' in runtime
    assert "GMA does not duplicate facts" in code

    print("OK: stage27 bilingual derived GMA paths passed")


if __name__ == "__main__":
    main()
