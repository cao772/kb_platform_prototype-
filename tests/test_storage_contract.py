from __future__ import annotations

from scripts.storage_contract import load_manifest, resolve_folder, validate_manifest


def main() -> None:
    payload = load_manifest()
    errors = validate_manifest(payload)
    assert errors == [], errors

    assert payload["storage_policy"] == {
        "code": "github",
        "artifacts": "google_drive",
        "logs_and_long_term_context": "notion",
    }
    assert payload["github"]["repository"] == "cao772/kb_platform_prototype-"
    assert payload["github"]["stable_branch"] == "通用知识库平台"

    root = payload["google_drive"]["root"]
    assert root["name"] == "法规知识库平台"
    assert root["id"] == "1q7q3TUE25H_t0nukHvX5_jBgKrZm0hVd"

    folders = payload["google_drive"]["folders"]
    assert len(folders) == 10
    assert resolve_folder(payload, "project_index")["name"] == "00_项目索引"
    assert resolve_folder(payload, "reports_and_outputs")["name"] == "08_报告与输出"
    assert resolve_folder(payload, "archive")["name"] == "99_历史归档"

    ids = [item["id"] for item in folders.values()]
    assert len(ids) == len(set(ids))

    required = set(payload["artifact_record_contract"]["required_fields"])
    assert {"logical_key", "drive_file_id", "sha256", "source_commit"} <= required

    print("OK: Drive/Notion/GitHub storage contract is valid")


if __name__ == "__main__":
    main()
