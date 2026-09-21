from __future__ import annotations

import argparse
from pathlib import Path

from app.source_registry import SourceRegistryService
from app.source_verification import SourceVerificationService
from app.store import KnowledgeStore


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify source harvestability against live websites.")
    parser.add_argument("--db", default=str(ROOT / "data" / "knowledge.db"))
    parser.add_argument("--region", default="")
    parser.add_argument("--verification-status", default="", choices=["", "preclassified", "verified", "failed"])
    parser.add_argument("--timeout", type=int, default=12)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--limit", type=int, default=0, help="0 means all matched sources")
    args = parser.parse_args()

    store = KnowledgeStore(Path(args.db))
    registry = SourceRegistryService(store)
    verifier = SourceVerificationService(registry)
    listing = registry.list_sources(
        region_code=args.region,
        verification_status=args.verification_status,
        limit=3000,
    )
    items = [item for item in listing["items"] if item.get("status") != "archived"]
    if args.limit > 0:
        items = items[: args.limit]

    print(f"Matched {len(items)} sources")
    total_verified = 0
    total_failed = 0
    for offset in range(0, len(items), 50):
        batch = items[offset : offset + 50]
        result = verifier.verify_many(
            [int(item["id"]) for item in batch],
            timeout=args.timeout,
            max_workers=args.workers,
        )
        total_verified += int(result["verified"])
        total_failed += int(result["failed"])
        print(
            f"{offset + len(batch)}/{len(items)} "
            f"verified={total_verified} failed={total_failed}"
        )

    print(registry.verification_summary())


if __name__ == "__main__":
    main()
