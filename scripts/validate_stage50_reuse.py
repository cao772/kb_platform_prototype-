from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.collection_experience import CollectionExperienceService
from app.site_extraction import SiteExtractionService
from app.source_collection import FIRST_WAVE_PATH
from app.source_registry import SourceRegistryService
from app.store import KnowledgeStore
from scripts.deep_validate_first50 import ResilientFetcher, robots_status


CANDIDATE_PATH = ROOT / "data" / "stage50_reuse_candidates.json"


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def run_candidate(entry: dict[str, Any], out_dir: Path, site_timeout: int) -> dict[str, Any]:
    source_key = str(entry["source_key"])
    started = time.time()
    store = KnowledgeStore(out_dir / f"{source_key}.db")
    registry = SourceRegistryService(store)
    source = registry.by_key(source_key)
    fetcher = ResilientFetcher(source_key, {})
    site = SiteExtractionService(
        store,
        registry,
        out_dir / "raw" / source_key,
        fetcher=fetcher,
    )
    experience = CollectionExperienceService(registry, site)

    try:
        recommendation = experience.recommend(source_key)
        template_id = str(recommendation["recommended_template_id"])
        preview = experience.preview_template(source_key, template_id)
        applied = experience.apply_template(
            source_key,
            template_id,
            expected_base_hash=str(preview["base_hash"]),
            operator="stage50-real-reuse-validation",
            note="首批50站经验迁移到真实新站；套用后立即执行受限试采。",
        )

        run = site.run(
            source_key,
            max_pages=4,
            max_items=80,
            auto_ingest=False,
        )
        outcome_payload = experience.reuse_outcomes(source_key=source_key, limit=10)
        initial_outcome = (outcome_payload.get("items") or [{}])[0]
        initial_trial = {
            "run_id": int(run.get("id") or 0),
            "status": str(run.get("status") or ""),
            "pages": int(run.get("pages_fetched") or 0),
            "items": int(run.get("items_discovered") or 0),
            "attachments": int(run.get("attachments_discovered") or 0),
            "error": str(run.get("error") or "")[:3000],
        }

        remediation_attempted = False
        remediation_note = ""
        post_adjustment_outcome: dict[str, Any] | None = None
        post_adjustment_trial: dict[str, Any] | None = None
        alternate_start_urls = [
            str(url).strip()
            for url in entry.get("alternate_start_urls") or []
            if str(url).strip()
        ]
        migration_outcome = str(initial_outcome.get("effective_outcome") or "needs_rework")
        migration_reason = str(initial_outcome.get("auto_reason") or "")

        if migration_outcome == "needs_rework" and alternate_start_urls:
            remediation_attempted = True
            remediation_note = str(entry.get("remediation_note") or "")
            plan = site.plan(source_key)
            adjusted_config = json.loads(json.dumps(plan.get("config") or {}))
            adjusted_config["start_urls"] = alternate_start_urls
            site.update_plan(source_key, config=adjusted_config, enabled=True)
            adjusted_run = site.run(
                source_key,
                max_pages=4,
                max_items=80,
                auto_ingest=False,
            )
            adjusted_payload = experience.reuse_outcomes(source_key=source_key, limit=10)
            post_adjustment_outcome = (adjusted_payload.get("items") or [{}])[0]
            post_adjustment_trial = {
                "run_id": int(adjusted_run.get("id") or 0),
                "status": str(adjusted_run.get("status") or ""),
                "pages": int(adjusted_run.get("pages_fetched") or 0),
                "items": int(adjusted_run.get("items_discovered") or 0),
                "attachments": int(adjusted_run.get("attachments_discovered") or 0),
                "error": str(adjusted_run.get("error") or "")[:3000],
            }
            recovered = str(post_adjustment_outcome.get("effective_outcome") or "")
            if recovered in {"direct_reuse", "minor_adjustment"}:
                migration_outcome = "minor_adjustment"
                migration_reason = "初始模板试采失败；切换同一官方站替代入口后试采成功，因此记为需小幅调整。"
            else:
                migration_outcome = "needs_rework"
                migration_reason = "初始模板试采失败，官方替代入口补救后仍未形成可用试采结果。"

        outcome = post_adjustment_outcome or initial_outcome
        effective_run = post_adjustment_trial or initial_trial
        expected_template = str(entry.get("expected_template") or "")
        result = {
            **entry,
            "source_name": source.get("source_name", ""),
            "base_url": source.get("base_url", ""),
            "source_type": source.get("source_type", ""),
            "access_method": source.get("access_method", ""),
            "recommended_template": template_id,
            "expected_template": expected_template,
            "template_match": not expected_template or template_id == expected_template,
            "experience_support": next(
                (
                    item.get("experience_support") or {}
                    for item in recommendation.get("items") or []
                    if item.get("template_id") == template_id
                ),
                {},
            ),
            "application_id": int(applied["application_id"]),
            "changed_paths": list(applied.get("changed_paths") or []),
            "robots": robots_status(str(source.get("base_url") or "")),
            "initial_trial": initial_trial,
            "initial_reuse_outcome": {
                "auto_outcome": initial_outcome.get("auto_outcome", ""),
                "auto_outcome_label": initial_outcome.get("auto_outcome_label", ""),
                "auto_reason": initial_outcome.get("auto_reason", ""),
                "effective_outcome": initial_outcome.get("effective_outcome", ""),
                "relevance": initial_outcome.get("relevance") or {},
            },
            "remediation": {
                "attempted": remediation_attempted,
                "note": remediation_note,
                "alternate_start_urls": alternate_start_urls if remediation_attempted else [],
                "changed_paths": ["start_urls"] if remediation_attempted else [],
            },
            "post_adjustment_trial": post_adjustment_trial,
            "post_adjustment_reuse_outcome": ({
                "auto_outcome": outcome.get("auto_outcome", ""),
                "auto_outcome_label": outcome.get("auto_outcome_label", ""),
                "auto_reason": outcome.get("auto_reason", ""),
                "effective_outcome": outcome.get("effective_outcome", ""),
                "relevance": outcome.get("relevance") or {},
            } if remediation_attempted else None),
            "trial": effective_run,
            "reuse_outcome": {
                "auto_outcome": outcome.get("auto_outcome", ""),
                "auto_outcome_label": outcome.get("auto_outcome_label", ""),
                "auto_reason": migration_reason,
                "effective_outcome": migration_outcome,
                "relevance": outcome.get("relevance") or {},
            },
            "fetch_events": fetcher.events[-80:],
            "elapsed_seconds": round(time.time() - started, 1),
        }
        return result
    except Exception as exc:
        return {
            **entry,
            "source_name": source.get("source_name", ""),
            "base_url": source.get("base_url", ""),
            "source_type": source.get("source_type", ""),
            "access_method": source.get("access_method", ""),
            "recommended_template": "",
            "expected_template": str(entry.get("expected_template") or ""),
            "template_match": False,
            "trial": {
                "run_id": 0,
                "status": "exception",
                "pages": 0,
                "items": 0,
                "attachments": 0,
                "error": str(exc)[:3000],
            },
            "reuse_outcome": {
                "auto_outcome": "needs_rework",
                "auto_outcome_label": "需重新处理",
                "auto_reason": "真实试采执行异常，需要检查访问或站点规则。",
                "effective_outcome": "needs_rework",
                "relevance": {},
            },
            "robots": robots_status(str(source.get("base_url") or "")),
            "fetch_events": fetcher.events[-80:],
            "exception": str(exc)[:3000],
            "elapsed_seconds": round(time.time() - started, 1),
        }
    finally:
        fetcher.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="stage50-validation")
    parser.add_argument("--source-keys", default="")
    parser.add_argument("--site-timeout", type=int, default=180)
    args = parser.parse_args()

    payload = load_json(CANDIDATE_PATH, {})
    entries = list(payload.get("items") or [])
    first50 = {
        str(item.get("source_key") or "")
        for item in load_json(FIRST_WAVE_PATH, [])
    }
    if not entries:
        raise SystemExit("Stage50 candidate list is empty")
    if any(str(item.get("source_key") or "") in first50 for item in entries):
        raise SystemExit("Stage50 candidates must be outside first50")

    requested = {
        item.strip()
        for item in str(args.source_keys or "").split(",")
        if item.strip()
    }
    if requested:
        known = {str(item["source_key"]) for item in entries}
        unknown = requested - known
        if unknown:
            raise SystemExit("unknown Stage50 source keys: " + ", ".join(sorted(unknown)))
        entries = [item for item in entries if str(item["source_key"]) in requested]

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for entry in entries:
        source_key = str(entry["source_key"])
        print(f"[stage50] {entry['order']} {source_key}", flush=True)
        result = run_candidate(entry, out_dir, args.site_timeout)
        results.append(result)
        print(
            f"[stage50] {source_key} => "
            f"{result['reuse_outcome']['effective_outcome']} "
            f"({result['trial']['status']}, {result['trial']['items']} items)",
            flush=True,
        )

    counts: dict[str, int] = {}
    for result in results:
        outcome = str(result.get("reuse_outcome", {}).get("effective_outcome") or "unknown")
        counts[outcome] = counts.get(outcome, 0) + 1

    output = {
        "stage": 50,
        "candidate_count": len(results),
        "sources": results,
        "summary": {
            "processed": len(results),
            "template_matches": sum(1 for item in results if item.get("template_match")),
            "direct_reuse": int(counts.get("direct_reuse", 0)),
            "minor_adjustment": int(counts.get("minor_adjustment", 0)),
            "needs_rework": int(counts.get("needs_rework", 0)),
            "awaiting_trial": int(counts.get("awaiting_trial", 0)),
            "by_outcome": counts,
        },
        "governance": {
            "real_network_trial": True,
            "outside_first50": True,
            "bounded_trial": True,
            "formal_knowledge_auto_promotion": False,
        },
    }
    target = out_dir / "results.json"
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(output["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
