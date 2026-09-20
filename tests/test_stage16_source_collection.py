from __future__ import annotations

import json
import tempfile
from pathlib import Path

from app.source_collection import PROFILE_SEEDS, SourceCollectionService
from app.source_registry import SourceRegistryService
from app.store import KnowledgeStore


def main() -> None:
    adapters = {item.adapter for item in PROFILE_SEEDS}
    assert {"html", "pdf", "json_api", "xml_api"} <= adapters
    assert any(item.profile_key == "EU-LVD-PDF" for item in PROFILE_SEEDS)
    assert any(item.profile_key == "US-FEDREGISTER-API" for item in PROFILE_SEEDS)
    assert any(item.profile_key == "JP-EGOV-LAWLIST-XML" for item in PROFILE_SEEDS)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = KnowledgeStore(root / "collection.db")
        registry = SourceRegistryService(store)

        xml_payload = """<?xml version="1.0" encoding="UTF-8"?>
<DataRoot>
  <Result><Code>0</Code><Message/></Result>
  <ApplData>
    <Category>3</Category>
    <LawNameListInfo>
      <LawId>TEST001</LawId>
      <LawName>電気用品安全法</LawName>
      <LawNo>平成十一年法律第百二十一号</LawNo>
      <PromulgationDate>19990806</PromulgationDate>
    </LawNameListInfo>
  </ApplData>
</DataRoot>
""".encode("utf-8")

        def fetcher(url: str, headers: dict[str, str], timeout: int, max_bytes: int):
            assert url == "https://elaws.e-gov.go.jp/api/1/lawlists/3"
            assert "User-Agent" in headers
            assert timeout > 0
            assert max_bytes >= len(xml_payload)
            return xml_payload, 200, "application/xml; charset=utf-8"

        service = SourceCollectionService(
            store,
            registry,
            root / "downloads",
            fetcher=fetcher,
        )
        profiles = service.list_profiles()
        assert profiles["summary"]["profiles"] >= 5
        jp = next(item for item in profiles["items"] if item["profile_key"] == "JP-EGOV-LAWLIST-XML")
        result = service.run(jp["id"], auto_ingest=True, auto_extract=False)
        assert result["status"] == "completed"
        assert result["http_status"] == 200
        assert result["document_id"]
        assert result["bytes_received"] == len(xml_payload)
        assert Path(result["saved_path"]).suffix == ".xml"
        assert Path(result["saved_path"]).exists()

        source = registry.by_key("JP-LAW")
        assert source["status"] == "connected"
        assert source["last_success_at"]

        with store.lock:
            doc = store.conn.execute(
                "SELECT filename,parser,metadata FROM documents WHERE id=?",
                (int(result["document_id"]),),
            ).fetchone()
        assert doc is not None
        assert doc["parser"] == "xml-etree"
        metadata = json.loads(doc["metadata"] or "{}")
        assert metadata["source_key"] == "JP-LAW"
        assert metadata["collection_profile"] == "JP-EGOV-LAWLIST-XML"
        assert metadata["region_code"] == "JP"

        chunks = store.document_chunks(int(result["document_id"]))
        assert chunks
        assert any("電気用品安全法" in item["text"] for item in chunks)
        assert all(item["metadata"].get("source_key") == "JP-LAW" for item in chunks)

        runs = service.list_runs()
        assert runs["summary"]["success"] == 1
        assert runs["items"][0]["profile_key"] == "JP-EGOV-LAWLIST-XML"

    collection_page = Path("static/collection.html").read_text(encoding="utf-8")
    sources_page = Path("static/sources.html").read_text(encoding="utf-8")
    admin_page = Path("static/admin.html").read_text(encoding="utf-8")
    runtime = Path("app/platform_server.py").read_text(encoding="utf-8")
    upload_code = Path("app/upload.py").read_text(encoding="utf-8")
    ingest_code = Path("app/ingest.py").read_text(encoding="utf-8")

    assert "来源采集执行" in collection_page
    assert "样板来源" not in collection_page
    assert "JSON API" in collection_page and "XML API" in collection_page
    assert "/api/collection/run" in collection_page
    assert 'href="/collection"' in sources_page
    assert ".xml" in admin_page
    assert '"/collection", "/collection.html"' in runtime
    assert '"/api/collection/profiles"' in runtime
    assert '"/api/collection/run"' in runtime
    assert '".xml"' in upload_code
    assert '".xml"' in ingest_code

    print("OK: stage16 executable source collection and XML ingestion passed")


if __name__ == "__main__":
    main()
