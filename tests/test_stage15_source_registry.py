from __future__ import annotations

import tempfile
from pathlib import Path

from app.regions import TARGET_MARKETS
from app.source_registry import SEED_SOURCES, SourceRegistryService
from app.store import KnowledgeStore


def main() -> None:
    assert len(TARGET_MARKETS) == 21
    assert len(SEED_SOURCES) >= 40

    with tempfile.TemporaryDirectory() as tmp:
        store = KnowledgeStore(Path(tmp) / "sources.db")
        service = SourceRegistryService(store)

        listing = service.list_sources()
        summary = listing["summary"]
        assert summary["target_sources"] == 300
        assert summary["registered_sources"] >= 40
        assert summary["target_markets"] == 21
        assert summary["target_markets_with_sources"] == 21
        assert summary["by_type"]["regulation"] >= 20
        assert summary["by_type"]["standard"] >= 10
        assert summary["shared_sources"] >= 5

        expected_markets = {item.code for item in TARGET_MARKETS}
        law_markets = {
            item["region_code"]
            for item in listing["items"]
            if item["source_type"] == "regulation" and item["region_code"] != "EU"
        }
        assert expected_markets <= law_markets

        eu = service.list_sources(region_code="EU")
        assert any(item["source_key"] == "EU-EURLEX" for item in eu["items"])
        assert any(item["source_type"] == "gma" for item in eu["items"])

        germany = service.list_sources(region_code="DE")
        assert any(item["source_key"] == "DE-LAW" for item in germany["items"])
        assert any(item["source_key"] == "DE-STD" for item in germany["items"])

        standard = next(item for item in germany["items"] if item["source_key"] == "DE-STD")
        assert standard["access_method"] == "metadata_only"
        assert "licence" in standard["notes"]

        updated = service.update(standard["id"], {
            "status": "validated",
            "owner_note": "来源已完成业务确认",
            "refresh_policy": "weekly",
        })
        assert updated["status"] == "validated"
        assert updated["owner_note"] == "来源已完成业务确认"

    page = Path("static/sources.html").read_text(encoding="utf-8")
    runtime = Path("app/platform_server.py").read_text(encoding="utf-8")
    assert "来源网站管理" in page
    assert "目标来源规模" in page
    assert "法规" in page and "标准" in page and "GMA/市场准入" in page
    assert "/api/sources" in page
    assert '"/sources", "/sources.html"' in runtime
    assert '"/api/sources/update"' in runtime

    print("OK: stage15 source registry and 300-site planning baseline passed")


if __name__ == "__main__":
    main()
