from __future__ import annotations

import tempfile
from pathlib import Path

from app.source_collection import SourceCollectionService
from app.source_registry import SourceRegistryService
from app.store import KnowledgeStore


PAYLOAD_V1 = b"""<?xml version="1.0" encoding="UTF-8"?>
<laws><law><id>A-1</id><name>Product Safety Rule</name><version>1</version></law></laws>
"""
PAYLOAD_V2 = b"""<?xml version="1.0" encoding="UTF-8"?>
<laws><law><id>A-1</id><name>Product Safety Rule</name><version>2</version></law></laws>
"""


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = KnowledgeStore(root / "incremental.db")
        registry = SourceRegistryService(store)
        state = {"payload": PAYLOAD_V1, "calls": 0}

        def fetcher(url: str, headers: dict[str, str], timeout: int, max_bytes: int):
            state["calls"] += 1
            return state["payload"], 200, "application/xml"

        service = SourceCollectionService(
            store,
            registry,
            root / "downloads",
            fetcher=fetcher,
        )
        profile = next(
            item for item in service.list_profiles()["items"]
            if item["profile_key"] == "JP-EGOV-LAWLIST-XML"
        )

        first = service.run(profile["id"], auto_ingest=True, auto_extract=False)
        assert first["status"] == "completed"
        assert first["content_status"] == "initial"
        assert first["document_id"]
        first_path = Path(first["saved_path"])
        assert first_path.exists()
        first_doc_id = int(first["document_id"])
        assert store.stats()["documents"] == 1

        second = service.run(profile["id"], auto_ingest=True, auto_extract=False)
        assert second["status"] == "completed"
        assert second["content_status"] == "unchanged"
        assert not second["saved_path"]
        assert second["document_id"] is None
        assert store.stats()["documents"] == 1

        state["payload"] = PAYLOAD_V2
        third = service.run(profile["id"], auto_ingest=True, auto_extract=False)
        assert third["status"] == "completed"
        assert third["content_status"] == "changed"
        assert third["document_id"]
        assert int(third["document_id"]) != first_doc_id
        assert Path(third["saved_path"]).exists()
        assert Path(third["saved_path"]).name != first_path.name
        assert store.stats()["documents"] == 2

        runs = service.list_runs(limit=10)
        assert runs["summary"]["initial"] == 1
        assert runs["summary"]["unchanged"] == 1
        assert runs["summary"]["changed"] == 1
        assert runs["summary"]["failed"] == 0

        updates = service.list_updates(limit=10)
        assert updates["summary"]["events"] == 2
        assert updates["summary"]["initial"] == 1
        assert updates["summary"]["changed"] == 1
        assert {item["change_type"] for item in updates["items"]} == {"initial", "changed"}

        with store.lock:
            snapshot = store.conn.execute(
                "SELECT * FROM source_snapshots WHERE profile_id=?",
                (int(profile["id"]),),
            ).fetchone()
        assert snapshot is not None
        assert snapshot["document_id"] == third["document_id"]
        assert snapshot["sha256"] == third["sha256"]
        assert state["calls"] == 3

    page = Path("static/collection.html").read_text(encoding="utf-8")
    runtime = Path("app/platform_server.py").read_text(encoding="utf-8")
    source_code = Path("app/source_collection.py").read_text(encoding="utf-8")

    assert "指纹比对" in page
    assert "无变化则不重复解析入库" in page
    assert "来源变化记录" in page
    assert "/api/collection/updates" in page
    assert '"/api/collection/updates"' in runtime
    assert "content_status='unchanged'" in source_code
    assert "source_snapshots" in source_code
    assert "source_update_events" in source_code
    assert "sha256[:12]" in source_code

    print("OK: stage17 incremental fingerprints and changed-only ingestion passed")


if __name__ == "__main__":
    main()
