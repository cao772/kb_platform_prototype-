from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "docs" / "storage" / "drive_manifest.json"


def load_manifest() -> dict[str, Any]:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if int(payload.get("schema_version") or 0) != 1:
        raise ValueError("unsupported storage manifest schema_version")
    return payload


def validate_manifest(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    drive = dict(payload.get("google_drive") or {})
    root = dict(drive.get("root") or {})
    folders = dict(drive.get("folders") or {})

    if not root.get("id"):
        errors.append("google_drive.root.id is required")
    if not root.get("url"):
        errors.append("google_drive.root.url is required")

    required_folder_keys = {
        "project_index",
        "formal_standards",
        "raw_business_data",
        "evaluation_data",
        "algorithms_and_rules",
        "project_process",
        "acceptance_and_delivery",
        "screenshots_and_evidence",
        "reports_and_outputs",
        "archive",
    }
    missing = sorted(required_folder_keys - set(folders))
    if missing:
        errors.append("missing folder keys: " + ", ".join(missing))

    ids: list[str] = []
    for key, item in folders.items():
        if not isinstance(item, dict):
            errors.append(f"folder {key} must be an object")
            continue
        folder_id = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        if not folder_id:
            errors.append(f"folder {key}.id is required")
        if not name:
            errors.append(f"folder {key}.name is required")
        if folder_id:
            ids.append(folder_id)
    if len(ids) != len(set(ids)):
        errors.append("Drive folder ids must be unique")

    policy = dict(payload.get("storage_policy") or {})
    if policy.get("code") != "github":
        errors.append("storage_policy.code must be github")
    if policy.get("artifacts") != "google_drive":
        errors.append("storage_policy.artifacts must be google_drive")
    if policy.get("logs_and_long_term_context") != "notion":
        errors.append("storage_policy.logs_and_long_term_context must be notion")

    contract = dict(payload.get("artifact_record_contract") or {})
    required_fields = set(contract.get("required_fields") or [])
    for field in {
        "logical_key",
        "file_name",
        "drive_file_id",
        "drive_url",
        "folder_key",
        "sha256",
        "size_bytes",
        "created_at",
        "producer",
        "source_commit",
    }:
        if field not in required_fields:
            errors.append(f"artifact record contract missing required field: {field}")

    return errors


def resolve_folder(payload: dict[str, Any], folder_key: str) -> dict[str, Any]:
    folders = dict((payload.get("google_drive") or {}).get("folders") or {})
    if folder_key not in folders:
        raise KeyError(f"unknown folder_key: {folder_key}")
    item = dict(folders[folder_key])
    item["folder_key"] = folder_key
    item["url"] = f"https://drive.google.com/drive/folders/{item['id']}"
    return item


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect the project Drive/Notion/GitHub storage contract")
    parser.add_argument("--check", action="store_true", help="Validate the manifest and exit non-zero on errors")
    parser.add_argument("--folder", default="", help="Resolve one logical Drive folder key")
    parser.add_argument("--list-folders", action="store_true", help="List all logical Drive folders")
    args = parser.parse_args()

    payload = load_manifest()
    errors = validate_manifest(payload)
    if args.check:
        if errors:
            for error in errors:
                print("ERROR:", error)
            raise SystemExit(1)
        print("OK: storage manifest is valid")

    if args.folder:
        print(json.dumps(resolve_folder(payload, args.folder), ensure_ascii=False, indent=2))

    if args.list_folders:
        folders = (payload.get("google_drive") or {}).get("folders") or {}
        for key in folders:
            item = resolve_folder(payload, key)
            print(f"{key}\t{item['name']}\t{item['id']}\t{item['url']}")

    if not (args.check or args.folder or args.list_folders):
        print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
