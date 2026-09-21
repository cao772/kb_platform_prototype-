from __future__ import annotations

import argparse
from pathlib import Path

from app.source_collection import SourceCollectionService
from app.source_registry import SourceRegistryService
from app.store import KnowledgeStore


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect the first-wave 50 official sources.")
    parser.add_argument("--db", default=str(ROOT / "data" / "knowledge.db"))
    parser.add_argument("--download-dir", default=str(ROOT / "data" / "source_downloads"))
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--auto-extract", action="store_true")
    parser.add_argument("--use-model", action="store_true")
    args = parser.parse_args()

    store = KnowledgeStore(Path(args.db))
    registry = SourceRegistryService(store)
    collection = SourceCollectionService(store, registry, Path(args.download_dir))

    wave = collection.first_wave_profiles()["items"]
    start = max(0, int(args.offset))
    end = min(len(wave), start + max(1, int(args.limit)))
    selected = wave[start:end]

    success = 0
    failed = 0
    for index, item in enumerate(selected, start=start + 1):
        profile = item.get("profile") or {}
        profile_id = int(profile.get("id") or 0)
        label = f"[{index}/{len(wave)}] {item['source_key']} {item['source_name']}"
        if not profile_id:
            failed += 1
            print(label + " SKIP no profile")
            continue
        try:
            result = collection.run(
                profile_id,
                auto_ingest=True,
                auto_extract=bool(args.auto_extract),
                use_model=bool(args.use_model),
            )
            success += 1
            print(
                label
                + f" OK status={result.get('content_status') or result.get('status')}"
                + f" bytes={result.get('bytes_received') or 0}"
                + f" document_id={result.get('document_id') or ''}"
            )
        except Exception as exc:
            failed += 1
            print(label + f" FAILED {exc}")

    summary = collection.first_wave_profiles()["summary"]
    print(
        f"done selected={len(selected)} success={success} failed={failed} "
        f"completed_sources={summary['completed_sources']}/{summary['target_sources']}"
    )


if __name__ == "__main__":
    main()
