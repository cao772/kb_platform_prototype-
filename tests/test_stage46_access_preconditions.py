from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from app.collection_experience import CollectionExperienceService
from app.site_extraction import SiteExtractionService
from app.source_registry import SourceRegistryService
from app.store import KnowledgeStore


def main() -> None:
    original = os.environ.get("FIRST50_AUTH_JSON")
    fake_secret = "must-never-be-exposed-stage46"
    os.environ["FIRST50_AUTH_JSON"] = json.dumps({
        "EU-EPREL": {"headers": {"X-Api-Key": fake_secret}},
        "CH-STD": {"cookie": f"session={fake_secret}"},
    })
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = KnowledgeStore(root / "stage46.db")
            registry = SourceRegistryService(store)
            site = SiteExtractionService(store, registry, root / "downloads")
            service = CollectionExperienceService(registry, site)

            pre = service.access_preconditions()
            assert pre["summary"]["remaining"] == 6
            assert pre["summary"]["credentials_required"] == 3
            assert pre["summary"]["credentials_configured"] == 2
            assert pre["summary"]["selfhosted_required"] == 3
            assert pre["execution"]["secret_values_exposed"] is False
            assert pre["execution"]["auth_env"] == "FIRST50_AUTH_JSON"
            assert fake_secret not in json.dumps(pre, ensure_ascii=False)

            by_key = {item["source_key"]: item for item in pre["items"]}
            assert by_key["EU-EPREL"]["credential_required"] is True
            assert by_key["EU-EPREL"]["credential_configured"] is True
            assert by_key["NZ-LAW"]["credential_required"] is True
            assert by_key["NZ-LAW"]["credential_configured"] is False
            assert by_key["DE-STD"]["selfhosted_required"] is True
            assert by_key["NL-STD"]["selfhosted_required"] is True
            assert by_key["SG-LAW"]["time_window_required"] is True
            assert by_key["SG-LAW"]["time_window"] == "03:00-07:00 Asia/Singapore"
            assert isinstance(by_key["SG-LAW"]["time_window_open"], bool)

            overview = service.overview()
            access_summary = overview["validation"]["access_summary"]
            assert sum(int(v) for v in access_summary.values()) == 6
            assert access_summary["registration_or_api_key"] == 2
            assert access_summary["registration_or_authorized_api"] == 1
            assert overview["summary"]["by_access_method"] != access_summary
    finally:
        if original is None:
            os.environ.pop("FIRST50_AUTH_JSON", None)
        else:
            os.environ["FIRST50_AUTH_JSON"] = original

    runtime = Path("app/platform_server.py").read_text(encoding="utf-8")
    page = Path("static/collection_experience.html").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/quality.yml").read_text(encoding="utf-8")

    assert '"/api/collection-experience/access-preconditions"' in runtime
    assert "剩余站点执行准备度" in page
    assert "Secret值不会返回页面" in page
    assert "Stage46 access precondition readiness tests" in workflow

    print("OK: stage46 access preconditions are visible and secrets remain private")


if __name__ == "__main__":
    main()
