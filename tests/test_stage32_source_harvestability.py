from __future__ import annotations

import tempfile
from pathlib import Path

import app.source_verification as verification_module
from app.source_registry import SourceRegistryService
from app.source_verification import SourceVerificationService
from app.store import KnowledgeStore


class FakeResponse:
    def __init__(self, url: str, body: bytes, *, status: int = 200, content_type: str = "text/html"):
        self.url = url
        self.body = body
        self.status = status
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size: int = -1) -> bytes:
        return self.body if size < 0 else self.body[:size]

    def geturl(self) -> str:
        return self.url


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = KnowledgeStore(Path(tmp) / "source_verification.db")
        registry = SourceRegistryService(store)
        verifier = SourceVerificationService(registry)

        listing = registry.list_sources(limit=3000)
        assert listing["summary"]["registered_sources"] == 300
        assert listing["summary"]["by_verification_status"]["preclassified"] == 300
        assert listing["summary"]["by_harvestability"]["direct"] > 0
        assert listing["summary"]["by_harvestability"]["adapter"] > 0
        assert listing["summary"]["by_harvestability"]["metadata_only"] > 0

        metadata = registry.by_key("DE-STD")
        assert metadata["harvestability"] == "metadata_only"
        assert metadata["verification_status"] == "preclassified"

        direct = registry.by_key("BE-LAW")
        assert direct["harvestability"] == "direct"
        assert direct["verification_status"] == "preclassified"

        original_urlopen = verification_module.urlopen

        def fake_urlopen(request, timeout=0):
            url = request.full_url
            if url.endswith("/robots.txt"):
                return FakeResponse(
                    url,
                    b"User-agent: *\nAllow: /\n",
                    content_type="text/plain",
                )
            return FakeResponse(
                url,
                b"<html><body>official source</body></html>",
                content_type="text/html; charset=utf-8",
            )

        verification_module.urlopen = fake_urlopen
        try:
            verified = verifier.verify_one(direct["id"])
            assert verified["verification_status"] == "verified"
            assert verified["harvestability"] == "direct"
            assert verified["last_http_status"] == 200
            assert verified["last_content_type"] == "text/html"
            assert verified["robots_allowed"] == "allowed"
            assert verified["last_verified_at"]

            verified_meta = verifier.verify_one(metadata["id"])
            assert verified_meta["verification_status"] == "verified"
            assert verified_meta["harvestability"] == "metadata_only"

            batch = verifier.verify_many([direct["id"], metadata["id"]], max_workers=2)
            assert batch["requested"] == 2
            assert batch["verified"] == 2
            assert batch["failed"] == 0
        finally:
            verification_module.urlopen = original_urlopen

        changed = registry.update(direct["id"], {"base_url": "https://example.org/new-source"})
        assert changed["verification_status"] == "preclassified"
        assert changed["last_http_status"] == 0
        assert changed["last_verified_at"] == ""
        assert "联网实测" in changed["verification_note"]

        filtered = registry.list_sources(harvestability="metadata_only", verification_status="verified")
        assert any(item["source_key"] == "DE-STD" for item in filtered["items"])

        summary = registry.verification_summary()
        assert summary["total"] == 300
        assert summary["by_verification_status"]["verified"] >= 1

    page = Path("static/sources.html").read_text(encoding="utf-8")
    runtime = Path("app/platform_server.py").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/quality.yml").read_text(encoding="utf-8")
    command = Path("scripts/verify_sources.py").read_text(encoding="utf-8")

    for label in (
        "可直接自动采集", "需要适配", "仅元数据", "需人工或授权",
        "暂不可采集", "待实测", "联网核验当前来源", "核验已选",
    ):
        assert label in page
    assert "/api/sources/verify" in runtime
    assert "/api/sources/verification-summary" in runtime
    assert "verify_many" in command
    assert "Stage32 source harvestability verification tests" in workflow

    print("OK: stage32 source harvestability preclassification and live verification passed")


if __name__ == "__main__":
    main()
