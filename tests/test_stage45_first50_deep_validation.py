from __future__ import annotations

import json
from pathlib import Path


def main() -> None:
    wave = json.loads(Path("data/source_collection_first50.json").read_text(encoding="utf-8"))
    remediation = json.loads(Path("data/first50_deep_remediation.json").read_text(encoding="utf-8"))
    round1 = json.loads(Path("data/first50_round1_status.json").read_text(encoding="utf-8"))
    round2 = json.loads(Path("data/first50_round2_status.json").read_text(encoding="utf-8"))
    round3 = json.loads(Path("data/first50_round3_status.json").read_text(encoding="utf-8"))
    round4 = json.loads(Path("data/first50_round4_status.json").read_text(encoding="utf-8"))
    round5 = json.loads(Path("data/first50_round5_status.json").read_text(encoding="utf-8"))
    live_workflow = Path(".github/workflows/first50_deep_validation.yml").read_text(encoding="utf-8")
    quality = Path(".github/workflows/quality.yml").read_text(encoding="utf-8")
    runner = Path("scripts/deep_validate_first50.py").read_text(encoding="utf-8")
    aggregate = Path("scripts/aggregate_first50_validation.py").read_text(encoding="utf-8")

    assert len(wave) == 50
    assert len({item["source_key"] for item in wave}) == 50
    assert [int(item["order"]) for item in wave] == list(range(1, 51))
    assert round1["summary"] == {"total": 50, "passed": 17, "partial": 14, "restricted": 17, "failed": 2}
    assert len(round1["status"]) == 50
    assert round2["summary"] == {"total": 50, "passed": 29, "partial": 8, "restricted": 13, "failed": 0}
    assert len(round2["status"]) == 50
    assert round3["summary"] == {"total": 50, "passed": 30, "partial": 7, "restricted": 13, "failed": 0}
    assert len(round3["status"]) == 50
    assert round4["summary"] == {"total": 50, "passed": 38, "partial": 1, "restricted": 11, "failed": 0}
    assert len(round4["status"]) == 50
    assert round5["summary"] == {"total": 50, "passed": 39, "partial": 1, "restricted": 10, "failed": 0}
    assert len(round5["status"]) == 50

    for key in ("US-FEDREG", "JP-LAW", "DE-LAW", "EU-EURLEX", "US-ECFR"):
        assert key in remediation
        assert remediation[key]["alternate_start_urls"]

    assert "matrix:" in live_workflow
    assert "batch_index: [0, 1, 2, 3, 4]" in live_workflow
    assert "max-parallel: 2" in live_workflow
    assert "FIRST50_AUTH_JSON" in live_workflow
    assert "playwright install --with-deps chromium" in live_workflow
    assert "first50-deep-validation-report" in live_workflow
    assert "--retry-nonpassed-from data/first50_round5_status.json" in live_workflow

    assert "robots_status" in runner
    assert "BrowserRenderer" in runner
    assert "official_alternate" in runner
    assert "requires_registration_or_login" in runner
    assert "captcha_or_bot_challenge" in runner
    assert "不绕过 robots" in runner
    assert "受控Secret" in runner
    assert "oauth_client_credentials" in runner
    assert "controlled_broadening" in runner
    assert "relevant_business" in runner
    assert "metadata_only" in runner
    assert "direct_content" in runner
    assert "request_headers" in runner
    assert "CA-ISED" in remediation
    assert "IE-STD" in remediation
    assert "NL-STD" in remediation
    assert "DE-STD" in remediation
    assert "carried-forward" in runner
    assert "official_binary_document" in runner
    assert "binary_documents" in runner
    assert "parse_file" in runner

    assert "expected 50 source results" in aggregate
    assert "失败/受限处置原则" in aggregate
    assert "Stage45 first50 full deep validation tests" in quality

    print("OK: stage45 full first50 deep validation orchestration passed")


if __name__ == "__main__":
    main()
