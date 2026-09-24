from __future__ import annotations

import argparse
import json
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import robotparser
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

from app.collection_experience import CollectionExperienceService
from app.site_extraction import SiteExtractionService
from app.source_registry import SourceRegistryService
from app.store import KnowledgeStore


USER_AGENT = "KnowledgePlatformMigrationPilot/1.0"


class SafePilotFetcher:
    """Low-volume fetcher for real migration trials.

    It honours an explicit robots disallow and does not add browser automation,
    login bypasses, CAPTCHA handling, cookies, or challenge workarounds.
    """

    def __init__(self) -> None:
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}
        self.events: list[dict[str, Any]] = []

    @staticmethod
    def _origin(url: str) -> tuple[str, str, str]:
        parsed = urlparse(url)
        return parsed.scheme, parsed.netloc, urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))

    def _robot_parser(self, url: str) -> robotparser.RobotFileParser | None:
        scheme, netloc, robots_url = self._origin(url)
        key = f"{scheme}://{netloc}"
        if key in self._robots:
            return self._robots[key]
        parser = robotparser.RobotFileParser()
        parser.set_url(robots_url)
        try:
            request = Request(robots_url, headers={"User-Agent": USER_AGENT, "Accept": "text/plain,*/*;q=0.5"})
            with urlopen(request, timeout=8) as response:
                raw = response.read(512 * 1024).decode("utf-8", errors="ignore")
            parser.parse(raw.splitlines())
            self._robots[key] = parser
            self.events.append({"type": "robots", "url": robots_url, "status": "loaded"})
            return parser
        except Exception as exc:
            # Unknown robots state is not treated as permission to broaden scope.
            # The pilot still performs only the configured two-page bounded trial.
            self._robots[key] = None
            self.events.append({"type": "robots", "url": robots_url, "status": "unknown", "error": str(exc)[:200]})
            return None

    def __call__(self, url: str, headers: dict[str, str], timeout: int, max_bytes: int) -> tuple[bytes, int, str]:
        parser = self._robot_parser(url)
        if parser is not None and not parser.can_fetch(USER_AGENT, url):
            self.events.append({"type": "blocked", "url": url, "reason": "robots_disallowed"})
            raise PermissionError(f"robots disallowed: {url}")

        merged = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml,application/json,text/plain,*/*;q=0.5",
            **{str(k): str(v) for k, v in (headers or {}).items() if str(k).lower() != "user-agent"},
        }
        request = Request(url, headers=merged, method="GET")
        started = time.time()
        try:
            with urlopen(request, timeout=min(int(timeout or 12), 15)) as response:
                status = int(getattr(response, "status", 200) or 200)
                content_type = str(response.headers.get("Content-Type") or "")
                body = response.read(int(max_bytes) + 1)
            if len(body) > int(max_bytes):
                raise ValueError(f"download exceeds max_bytes: {max_bytes}")
            self.events.append({
                "type": "fetch",
                "url": url,
                "status": status,
                "content_type": content_type,
                "bytes": len(body),
                "elapsed_seconds": round(time.time() - started, 2),
            })
            return body, status, content_type
        except HTTPError as exc:
            self.events.append({"type": "http_error", "url": url, "status": int(exc.code)})
            raise
        except URLError as exc:
            self.events.append({"type": "network_error", "url": url, "error": str(exc)[:200]})
            raise


def classify_trial_error(run: dict[str, Any], events: list[dict[str, Any]]) -> str:
    text = (str(run.get("error") or "") + " " + json.dumps(events, ensure_ascii=False)).lower()
    if "robots disallowed" in text:
        return "robots_disallowed"
    if "403" in text or "access denied" in text or "request access" in text:
        return "access_restricted"
    if "429" in text or "too many requests" in text:
        return "rate_limited"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if "certificate verify failed" in text:
        return "tls_error"
    if "name or service not known" in text or "temporary failure in name resolution" in text:
        return "dns_error"
    return ""


def run_pilot(pilot_path: Path) -> dict[str, Any]:
    pilot = json.loads(pilot_path.read_text(encoding="utf-8"))
    sources = list(pilot.get("sources") or [])
    limits = dict(pilot.get("trial_limits") or {})
    max_pages = int(limits.get("max_pages") or 2)
    max_items = int(limits.get("max_items") or 20)
    timeout_seconds = int(limits.get("timeout_seconds") or 12)

    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="stage50-") as tmp:
        root = Path(tmp)
        store = KnowledgeStore(root / "stage50.db")
        registry = SourceRegistryService(store)

        for entry in sources:
            source_key = str(entry["source_key"])
            source = registry.by_key(source_key)
            fetcher = SafePilotFetcher()
            site = SiteExtractionService(store, registry, root / "raw" / source_key, fetcher=fetcher)
            experience = CollectionExperienceService(registry, site)
            started = time.time()

            recommendation = experience.recommend(source_key)
            template_id = str(recommendation["recommended_template_id"])
            top = next(
                (item for item in recommendation.get("items") or [] if item.get("template_id") == template_id),
                {},
            )
            expected = str(entry.get("expected_family") or "")
            result: dict[str, Any] = {
                "order": int(entry.get("order") or 0),
                "source_key": source_key,
                "source_name": source.get("source_name", ""),
                "region_code": source.get("region_code", ""),
                "source_type": source.get("source_type", ""),
                "access_method": source.get("access_method", ""),
                "base_url": source.get("base_url", ""),
                "expected_template_id": expected,
                "recommended_template_id": template_id,
                "template_match": not expected or expected == template_id,
                "experience_support": dict(top.get("experience_support") or {}),
            }

            try:
                preview = experience.preview_template(source_key, template_id)
                applied = experience.apply_template(
                    source_key,
                    template_id,
                    expected_base_hash=str(preview["base_hash"]),
                    operator="Stage50 migration pilot",
                    note="第51-60站真实迁移试采",
                )
                plan = site.plan(source_key)
                config = dict(plan.get("config") or {})
                config["limits"] = {
                    **dict(config.get("limits") or {}),
                    "max_pages": max_pages,
                    "max_items": max_items,
                    "max_details": min(10, max_items),
                    "max_attachments": 4,
                }
                config["request"] = {
                    **dict(config.get("request") or {}),
                    "timeout_seconds": timeout_seconds,
                }
                site.update_plan(source_key, config=config, enabled=True)

                run = site.run(
                    source_key,
                    max_pages=max_pages,
                    max_items=max_items,
                    auto_ingest=False,
                )
                outcomes = experience.reuse_outcomes(source_key=source_key, limit=10)
                outcome = next(
                    (
                        item for item in outcomes.get("items") or []
                        if int(item.get("application_id") or 0) == int(applied["application_id"])
                    ),
                    {},
                )
                result.update({
                    "application_id": int(applied["application_id"]),
                    "run_id": int(run.get("id") or outcome.get("trial_run_id") or 0),
                    "run_status": str(run.get("status") or ""),
                    "pages": int(run.get("pages_fetched") or 0),
                    "items": int(run.get("items_discovered") or 0),
                    "attachments": int(run.get("attachments_discovered") or 0),
                    "run_error": str(run.get("error") or "")[:1000],
                    "restriction_reason": classify_trial_error(run, fetcher.events),
                    "auto_outcome": str(outcome.get("auto_outcome") or "awaiting_trial"),
                    "auto_outcome_label": str(outcome.get("auto_outcome_label") or ""),
                    "auto_reason": str(outcome.get("auto_reason") or ""),
                    "relevance": dict(outcome.get("relevance") or {}),
                })
            except Exception as exc:
                result.update({
                    "application_id": 0,
                    "run_id": 0,
                    "run_status": "exception",
                    "pages": 0,
                    "items": 0,
                    "attachments": 0,
                    "run_error": str(exc)[:1000],
                    "restriction_reason": classify_trial_error({"error": str(exc)}, fetcher.events),
                    "auto_outcome": "needs_rework",
                    "auto_outcome_label": "需重新处理",
                    "auto_reason": "迁移试采在模板应用或执行阶段异常，需要检查来源入口、访问条件或规则。",
                    "relevance": {},
                })

            result["fetch_events"] = fetcher.events[-12:]
            result["elapsed_seconds"] = round(time.time() - started, 2)
            results.append(result)

    counts = Counter(str(item.get("auto_outcome") or "unknown") for item in results)
    template_counts = Counter(str(item.get("recommended_template_id") or "") for item in results)
    return {
        "pilot_id": pilot.get("pilot_id"),
        "title": pilot.get("title"),
        "started_from_experience_sources": 50,
        "candidate_orders": [int(item.get("order") or 0) for item in sources],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "trial_limits": {
            "max_pages": max_pages,
            "max_items": max_items,
            "timeout_seconds": timeout_seconds,
        },
        "summary": {
            "total": len(results),
            "template_match": sum(1 for item in results if item.get("template_match")),
            "direct_reuse": int(counts.get("direct_reuse", 0)),
            "minor_adjustment": int(counts.get("minor_adjustment", 0)),
            "needs_rework": int(counts.get("needs_rework", 0)),
            "awaiting_trial": int(counts.get("awaiting_trial", 0)),
            "with_content": sum(1 for item in results if int(item.get("items") or 0) > 0),
            "completed_runs": sum(1 for item in results if item.get("run_status") == "completed"),
            "partial_runs": sum(1 for item in results if item.get("run_status") == "partial"),
            "restricted_or_error": sum(
                1 for item in results
                if item.get("restriction_reason") or item.get("run_status") in {"failed", "exception"}
            ),
            "by_template": dict(template_counts),
        },
        "items": results,
        "governance": {
            "bounded_trial": True,
            "robots_disallow_respected": True,
            "login_or_captcha_bypass": False,
            "standard_fulltext_bypass": False,
            "formal_knowledge_auto_promotion": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pilot", default="data/stage50_migration_pilot.json")
    parser.add_argument("--output", default="stage50-migration-result.json")
    args = parser.parse_args()

    result = run_pilot(Path(args.pilot))
    output = Path(args.output)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("STAGE50_SUMMARY=" + json.dumps(result["summary"], ensure_ascii=False, sort_keys=True))
    print("STAGE50_RESULT_JSON=" + json.dumps(result, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
