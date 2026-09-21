from __future__ import annotations

import json
import tempfile
from pathlib import Path

from app.source_collection import FIRST_WAVE_PATH, SourceCollectionService
from app.source_registry import SourceRegistryService
from app.store import KnowledgeStore


def main() -> None:
    catalog = json.loads(FIRST_WAVE_PATH.read_text(encoding="utf-8"))
    assert len(catalog) == 50
    assert len({item["source_key"] for item in catalog}) == 50
    assert [item["order"] for item in catalog] == list(range(1, 51))

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = KnowledgeStore(root / "first50.db")
        registry = SourceRegistryService(store)

        def fetcher(url: str, headers: dict[str, str], timeout: int, max_bytes: int):
            body = f"<html><body><h1>{url}</h1><p>official source entry</p></body></html>".encode()
            return body, 200, "text/html; charset=utf-8"

        collection = SourceCollectionService(
            store,
            registry,
            root / "downloads",
            fetcher=fetcher,
        )
        wave = collection.first_wave_profiles()
        assert wave["summary"]["target_sources"] == 50
        assert wave["summary"]["profile_ready"] == 50
        assert wave["summary"]["regions"] == 21
        assert len(wave["items"]) == 50
        assert len({item["source_key"] for item in wave["items"]}) == 50

        adapters = {item["profile"]["adapter"] for item in wave["items"]}
        assert {"html", "pdf", "json_api", "xml_api"} <= adapters

        first_keys = [item["source_key"] for item in wave["items"][:5]]
        assert first_keys == [
            "EU-EURLEX",
            "EU-ECHA",
            "EU-EPREL",
            "EU-ACCESS2MARKETS",
            "EU-HARMONISED-STANDARDS",
        ]

        result = collection.run_first_wave_batch(
            offset=0,
            limit=3,
            auto_ingest=False,
            auto_extract=False,
        )
        assert result["summary"]["requested"] == 3
        assert result["summary"]["success"] == 3
        assert result["summary"]["failed"] == 0
        assert result["summary"]["completed_sources"] == 3
        assert result["next_offset"] == 3
        assert result["done"] is False

        runs = collection.list_runs(limit=10)
        assert runs["summary"]["success"] == 3
        assert {item["source_key"] for item in runs["items"]} == set(first_keys[:3])

    page = Path("static/collection.html").read_text(encoding="utf-8")
    runtime = Path("app/platform_server.py").read_text(encoding="utf-8")
    command = Path("scripts/collect_first50.py").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/quality.yml").read_text(encoding="utf-8")

    assert "首批50站采集" in page
    assert "执行下一批5站" in page
    assert "/api/collection/first-wave" in page
    assert '"/api/collection/first-wave"' in runtime
    assert '"/api/collection/first-wave/run"' in runtime
    assert "first_wave_profiles" in command
    assert "Stage34 first-wave 50 source collection tests" in workflow

    print("OK: stage34 first-wave 50-source profiles and controlled batch collection passed")


if __name__ == "__main__":
    main()
