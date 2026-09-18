from __future__ import annotations

import tempfile
from pathlib import Path

from app.ontology import ontology_schema
from app.ontology_registry import OntologyRegistryService
from app.store import KnowledgeStore


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = KnowledgeStore(Path(tmp) / "ontology_registry.db")
        service = OntologyRegistryService(store)

        versions = service.list_versions()
        assert versions["summary"]["versions"] >= 1
        assert versions["summary"]["active_version"] == ontology_schema()["version"]
        active = next(item for item in versions["items"] if item["status"] == "active")

        draft = service.create_version(
            version_code="2026.10-business-ontology-v4",
            change_note="增加术语治理样板",
            created_by="test",
            source_version_id=active["id"],
        )
        assert draft["status"] == "draft"
        assert draft["schema"]["version"] == "2026.10-business-ontology-v4"
        assert service.list_versions()["summary"]["drafts"] == 1

        activated = service.activate_version(draft["id"], operator="tester")
        assert activated["status"] == "active"
        versions2 = service.list_versions()
        assert versions2["summary"]["active_version"] == "2026.10-business-ontology-v4"
        previous = next(item for item in versions2["items"] if item["id"] == active["id"])
        assert previous["status"] == "retired"

        terms = service.list_terms(term_type="record_type")
        assert any(item["canonical_name"] == "法规" for item in terms["items"])
        norm = service.normalize("regulation", term_type="record_type")
        assert norm["matches"]
        assert norm["matches"][0]["canonical_name"] == "法规"
        assert norm["matches"][0]["score"] == 1.0

        custom = service.upsert_term({
            "term_type": "custom",
            "canonical_name": "市场准入要求",
            "code": "market_access_requirement",
            "language": "zh-CN",
            "aliases": "GMA requirement，市场进入要求; market access requirement",
            "source_note": "业务沟通确认前的预研术语",
        })
        assert custom["canonical_name"] == "市场准入要求"
        assert len(custom["aliases"]) == 3
        custom_norm = service.normalize("GMA requirement")
        assert custom_norm["matches"][0]["canonical_name"] == "市场准入要求"

        updated = service.upsert_term({
            "term_type": "custom",
            "canonical_name": "市场准入要求",
            "code": "market_access_requirement",
            "language": "zh-CN",
            "aliases": ["GMA requirement", "准入要求"],
            "source_note": "更新别名",
        })
        assert "准入要求" in updated["aliases"]
        assert "市场进入要求" not in updated["aliases"]

        with store.lock:
            formal_count = store.conn.execute(
                "SELECT COUNT(*) count FROM compliance_records"
            ).fetchone()["count"]
        assert formal_count == 0

    page = Path("static/ontology_governance.html").read_text(encoding="utf-8")
    ontology_page = Path("static/ontology.html").read_text(encoding="utf-8")
    runtime = Path("app/platform_server.py").read_text(encoding="utf-8")
    code = Path("app/ontology_registry.py").read_text(encoding="utf-8")

    assert "本体版本与术语治理" in page
    assert "新建本体版本" in page
    assert "术语与同义词" in page
    assert "/api/ontology/version/create" in page
    assert "/api/ontology/terms/upsert" in page
    assert 'href="/ontology-governance"' in ontology_page
    assert '"/ontology-governance", "/ontology-governance.html"' in runtime
    assert '"/api/ontology/versions"' in runtime
    assert '"/api/ontology/normalize"' in runtime
    assert "ontology_versions" in code
    assert "ontology_terms" in code

    print("OK: stage19 ontology version and terminology governance passed")


if __name__ == "__main__":
    main()
