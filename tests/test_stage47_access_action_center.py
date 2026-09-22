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
    fake_secret = "stage47-secret-must-not-leak"
    os.environ["FIRST50_AUTH_JSON"] = json.dumps({
        "EU-EPREL": {"headers": {"X-Api-Key": fake_secret}},
    })
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = KnowledgeStore(root / "stage47.db")
            registry = SourceRegistryService(store)
            site = SiteExtractionService(store, registry, root / "downloads")
            service = CollectionExperienceService(registry, site)

            actions = service.access_actions()
            assert actions["summary"]["remaining"] == 6
            assert actions["safety"]["secrets_returned"] is False
            assert actions["safety"]["bypass_access_controls"] is False
            assert actions["safety"]["formal_knowledge_auto_promotion"] is False
            assert fake_secret not in json.dumps(actions, ensure_ascii=False)

            by_key = {item["source_key"]: item for item in actions["items"]}
            eprel = by_key["EU-EPREL"]
            assert eprel["credential_required"] is True
            assert eprel["credential_configured"] is True
            assert eprel["credential_contract"]["env"] == "FIRST50_AUTH_JSON"
            assert "headers" in eprel["credential_contract"]["supported_shapes"]
            assert eprel["credential_contract"]["secret_values_exposed"] is False

            de_std = by_key["DE-STD"]
            assert de_std["selfhosted_required"] is True
            assert "self_hosted_runner" in de_std["blockers"]
            assert '--source-keys "DE-STD"' in de_std["local_command"]
            assert de_std["workflow"]["file"] == ".github/workflows/first50_selfhosted_access.yml"

            sg = by_key["SG-LAW"]
            assert sg["time_window_required"] is True
            assert sg["workflow"]["lane"] == "site_terms_window_and_cloud_egress"
            assert any("03:00-07:00" in step for step in sg["steps"])

            nz = by_key["NZ-LAW"]
            assert nz["registration_url"].startswith("https://")
            assert '--source-keys "NZ-LAW"' in nz["local_command"]
    finally:
        if original is None:
            os.environ.pop("FIRST50_AUTH_JSON", None)
        else:
            os.environ["FIRST50_AUTH_JSON"] = original

    runtime = Path("app/platform_server.py").read_text(encoding="utf-8")
    page = Path("static/collection_experience.html").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/quality.yml").read_text(encoding="utf-8")

    assert '"/api/collection-experience/access-actions"' in runtime
    assert "剩余站点执行动作" in page
    assert "复制本地复跑命令" in page
    assert "Stage47 access action center tests" in workflow

    print("OK: stage47 access action center is actionable and keeps secrets private")


if __name__ == "__main__":
    main()
