from __future__ import annotations

import argparse
import base64
import json
import os
import re
import signal
import socket
import sys
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen
from urllib import robotparser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.parsers import parse_file
from app.site_extraction import SiteExtractionService
from app.source_collection import FIRST_WAVE_PATH
from app.source_registry import SourceRegistryService
from app.store import KnowledgeStore


REMEDIATION_PATH = ROOT / "data" / "first50_deep_remediation.json"
ROUND1_STATUS_PATH = ROOT / "data" / "first50_round1_status.json"
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}
CHALLENGE_MARKERS = (
    "captcha", "verify you are human", "are you a human", "request access",
    "access denied", "cloudflare", "attention required", "bot detection",
)
AUTH_MARKERS = (
    "sign in to continue", "login required", "log in to continue",
    "registration required", "register to access", "account required",
)


class SiteTimeout(Exception):
    pass


def _alarm_handler(signum, frame):
    raise SiteTimeout("site validation timeout")


class BrowserRenderer:
    def __init__(self, auth: dict[str, Any], events: list[dict[str, Any]]):
        self.auth = auth
        self.events = events
        self._playwright = None
        self._browser = None

    def _ensure(self) -> None:
        if self._browser is not None:
            return
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=True)

    def render(self, url: str, headers: dict[str, str]) -> tuple[bytes, int, str]:
        self._ensure()
        extra = {k: v for k, v in headers.items() if k.lower() != "cookie"}
        context = self._browser.new_context(
            user_agent=extra.get("User-Agent", BROWSER_HEADERS["User-Agent"]),
            extra_http_headers={k: v for k, v in extra.items() if k.lower() != "user-agent"},
        )
        cookie = str(self.auth.get("cookie") or "").strip()
        if cookie:
            parsed = urlparse(url)
            cookies = []
            for part in cookie.split(";"):
                if "=" not in part:
                    continue
                name, value = part.strip().split("=", 1)
                cookies.append({
                    "name": name.strip(),
                    "value": value.strip(),
                    "domain": parsed.hostname or "",
                    "path": "/",
                })
            if cookies:
                context.add_cookies(cookies)
        page = context.new_page()
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=35000)
            page.wait_for_timeout(1200)
            content = page.content()
            status = int(response.status if response else 200)
            lower = content.lower()
            if any(marker in lower for marker in CHALLENGE_MARKERS):
                raise RuntimeError("browser challenge/captcha detected")
            self.events.append({"strategy": "browser_render", "url": url, "status": status})
            return content.encode("utf-8"), status, "text/html; charset=utf-8"
        finally:
            context.close()

    def close(self) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            self._browser = None
            if self._playwright is not None:
                self._playwright.stop()
            self._playwright = None


def visible_text_len(data: bytes) -> int:
    text = data.decode("utf-8", errors="ignore")
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>|<noscript.*?</noscript>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return len(" ".join(text.split()))


class ResilientFetcher:
    def __init__(self, source_key: str, auth: dict[str, Any]):
        self.source_key = source_key
        self.auth = auth
        self.events: list[dict[str, Any]] = []
        self.renderer = BrowserRenderer(auth, self.events)
        self._robots_cache: dict[str, robotparser.RobotFileParser | None] = {}
        self.auth_headers = self._resolve_auth_headers()

    def _resolve_auth_headers(self) -> dict[str, str]:
        headers = {str(k): str(v) for k, v in (self.auth.get("headers") or {}).items()}
        oauth = dict(self.auth.get("oauth") or {})
        if not oauth:
            return headers
        token_url = str(oauth.get("token_url") or "").strip()
        client_id = str(oauth.get("client_id") or "").strip()
        client_secret = str(oauth.get("client_secret") or "").strip()
        if not token_url or not client_id or not client_secret:
            self.events.append({"strategy": "oauth_skipped", "error": "missing token_url/client_id/client_secret"})
            return headers
        form = {
            "grant_type": str(oauth.get("grant_type") or "client_credentials"),
            "client_id": client_id,
            "client_secret": client_secret,
        }
        scope = str(oauth.get("scope") or "").strip()
        if scope:
            form["scope"] = scope
        try:
            request = Request(
                token_url,
                data=urlencode(form).encode("utf-8"),
                headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with urlopen(request, timeout=25) as response:
                payload = json.loads(response.read(1024 * 1024).decode("utf-8"))
            token = str(payload.get("access_token") or "").strip()
            if not token:
                raise ValueError("OAuth response missing access_token")
            headers["Authorization"] = f"Bearer {token}"
            self.events.append({"strategy": "oauth_client_credentials", "status": "token_acquired"})
        except Exception as exc:
            self.events.append({"strategy": "oauth_failed", "error": str(exc)[:500]})
        return headers

    def _robots_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        host_key = f"{parsed.scheme}://{parsed.netloc}"
        if host_key not in self._robots_cache:
            robots_url = urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
            try:
                req = Request(robots_url, headers=BROWSER_HEADERS, method="GET")
                with urlopen(req, timeout=12) as response:
                    raw = response.read(512 * 1024).decode("utf-8", errors="ignore")
                rp = robotparser.RobotFileParser()
                rp.set_url(robots_url)
                rp.parse(raw.splitlines())
                self._robots_cache[host_key] = rp
            except Exception:
                self._robots_cache[host_key] = None
        parser = self._robots_cache[host_key]
        return True if parser is None else bool(parser.can_fetch("KnowledgePlatformDeepCollector/1.0", url))

    def __call__(self, url: str, headers: dict[str, str], timeout: int, max_bytes: int):
        if not self._robots_allowed(url):
            self.events.append({"strategy": "robots_disallowed", "url": url})
            raise PermissionError(f"robots disallowed: {url}")

        merged = dict(BROWSER_HEADERS)
        merged.update(headers or {})
        merged.update(self.auth_headers)
        cookie = str(self.auth.get("cookie") or "").strip()
        if cookie:
            merged["Cookie"] = cookie

        req = Request(url, headers=merged, method="GET")
        try:
            with urlopen(req, timeout=timeout) as response:
                status = int(getattr(response, "status", 200) or 200)
                content_type = str(response.headers.get("Content-Type") or "")
                data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError(f"download exceeds max_bytes: {max_bytes}")
            lower = data[:300000].decode("utf-8", errors="ignore").lower()
            if any(marker in lower for marker in CHALLENGE_MARKERS):
                self.events.append({"strategy": "challenge_detected", "url": url, "status": status})
                return self.renderer.render(url, merged)
            if "html" in content_type.lower() and len(data) > 800 and visible_text_len(data) < 120:
                self.events.append({"strategy": "dynamic_shell_detected", "url": url, "status": status})
                try:
                    return self.renderer.render(url, merged)
                except Exception as exc:
                    self.events.append({"strategy": "browser_render_failed", "url": url, "error": str(exc)[:300]})
            self.events.append({"strategy": "browser_headers", "url": url, "status": status})
            return data, status, content_type
        except HTTPError as exc:
            self.events.append({"strategy": "http_error", "url": url, "status": int(exc.code)})
            if int(exc.code) in {401, 403, 429}:
                try:
                    return self.renderer.render(url, merged)
                except Exception as browser_exc:
                    self.events.append({
                        "strategy": "browser_render_failed",
                        "url": url,
                        "error": str(browser_exc)[:300],
                    })
            raise
        except (URLError, socket.timeout) as exc:
            self.events.append({"strategy": "network_error", "url": url, "error": str(exc)[:300]})
            try:
                return self.renderer.render(url, merged)
            except Exception as browser_exc:
                self.events.append({
                    "strategy": "browser_network_fallback_failed",
                    "url": url,
                    "error": str(browser_exc)[:300],
                })
            raise

    def close(self) -> None:
        self.renderer.close()


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def robots_status(url: str) -> dict[str, Any]:
    parsed = urlparse(url)
    robots_url = urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
    try:
        req = Request(robots_url, headers=BROWSER_HEADERS, method="GET")
        with urlopen(req, timeout=12) as response:
            raw = response.read(512 * 1024).decode("utf-8", errors="ignore")
        rp = robotparser.RobotFileParser()
        rp.set_url(robots_url)
        rp.parse(raw.splitlines())
        allowed = bool(rp.can_fetch("KnowledgePlatformDeepCollector/1.0", url))
        return {"status": "allowed" if allowed else "disallowed", "robots_url": robots_url}
    except Exception as exc:
        return {"status": "unknown", "robots_url": robots_url, "error": str(exc)[:300]}


def assessment(
    service: SiteExtractionService,
    source_key: str,
    run: dict[str, Any],
    *,
    structured_api: bool = False,
    metadata_only: bool = False,
    direct_content: bool = False,
) -> dict[str, Any]:
    listing = service.list_items(source_key=source_key, limit=5000)
    summary = dict(listing.get("summary") or {})
    items = listing.get("items") or []
    details = sum(1 for item in items if item.get("item_type") == "detail")
    attachments = sum(1 for item in items if item.get("item_type") == "attachment")
    listing_items = sum(1 for item in items if item.get("item_type") == "listing")
    meaningful = sum(
        1 for item in items
        if len(str(item.get("text_excerpt") or "").strip()) >= 120 or bool(item.get("fields"))
    )
    pages = int(run.get("pages_fetched") or 0)
    relevant = int(summary.get("relevant") or 0)
    relevant_business = sum(
        1 for item in items
        if item.get("item_type") in {"detail", "attachment"}
        and (item.get("effective_relevance") or {}).get("status") == "relevant"
    )
    relevant_listing = sum(
        1 for item in items
        if item.get("item_type") == "listing"
        and (item.get("effective_relevance") or {}).get("status") == "relevant"
        and (len(str(item.get("text_excerpt") or "").strip()) >= 120 or bool(item.get("fields")))
    )
    errors = str(run.get("error") or "")
    evidence_mode = "fulltext"
    if metadata_only:
        evidence_mode = "metadata_only"
    elif structured_api:
        evidence_mode = "structured_api"
    elif direct_content:
        evidence_mode = "direct_content"
    if (structured_api or metadata_only or direct_content) and pages >= 1 and meaningful >= 1 and relevant >= 1:
        verdict = "passed"
    elif pages >= 2 and (details + attachments >= 1) and meaningful >= 1 and relevant_business >= 1:
        verdict = "passed"
    elif pages >= 1 and int(run.get("items_discovered") or 0) >= 1:
        verdict = "partial"
    else:
        verdict = "failed"
    return {
        "verdict": verdict,
        "evidence_mode": evidence_mode,
        "pages": pages,
        "items": int(run.get("items_discovered") or 0),
        "listing_items": listing_items,
        "details": details,
        "attachments": attachments,
        "relevant": relevant,
        "relevant_business": relevant_business,
        "relevant_listing": relevant_listing,
        "needs_review": int(summary.get("needs_review") or 0),
        "irrelevant": int(summary.get("irrelevant") or 0),
        "reviewed": int(summary.get("reviewed") or 0),
        "meaningful_items": meaningful,
        "error": errors[:3000],
    }


def classify_restriction(text: str, events: list[dict[str, Any]]) -> str:
    hay = (text + " " + json.dumps(events, ensure_ascii=False)).lower()
    if "robots" in hay and "disallow" in hay:
        return "robots_disallowed"
    if any(marker in hay for marker in ("captcha", "verify you are human", "challenge")):
        return "captcha_or_bot_challenge"
    if any(marker in hay for marker in ("401", "login required", "sign in", "registration required", "account required")):
        return "requires_registration_or_login"
    if any(marker in hay for marker in ("403", "request access", "access denied")):
        return "access_restricted"
    if "429" in hay or "too many requests" in hay:
        return "rate_limited"
    if "timed out" in hay or "timeout" in hay:
        return "timeout"
    if "name or service not known" in hay or "nodename nor servname" in hay:
        return "dns_or_endpoint_failure"
    return "unresolved_failure"


def recommendation(reason: str, has_auth: bool, remediation: dict[str, Any]) -> str:
    registration_url = str(remediation.get("registration_url") or "").strip()
    auth_notes = str(remediation.get("auth_notes") or "").strip()
    if reason == "robots_disallowed":
        return "不绕过 robots 禁止；改用该机构公开API、数据下载、RSS或人工授权渠道。"
    if reason == "captcha_or_bot_challenge":
        return "使用人工浏览器完成验证码/登录并保存合规会话；若网站禁止自动访问则改用官方API/导出。"
    if reason == "requires_registration_or_login":
        if has_auth:
            return "已配置登录会话仍失败；检查账号权限、会话有效期和站点许可。"
        suffix = f" 注册入口：{registration_url}" if registration_url else ""
        detail = f" {auth_notes}" if auth_notes else ""
        return "尝试官方免费注册/登录；完成后将授权Cookie、请求头或OAuth客户端凭证作为受控Secret注入，再复跑。" + suffix + detail
    if reason == "access_restricted":
        if remediation.get("alternate_start_urls"):
            return "继续优先使用已配置的官方API/备用网址；必要时申请官方数据服务权限。"
        return "查找该机构官方API/XML/RSS/下载入口；如需注册则走官方注册/授权，不绕过访问控制。"
    if reason == "rate_limited":
        return "降低并发和频率、增加退避；优先使用官方批量/API渠道。"
    if reason == "timeout":
        return "缩小单次范围并重试；必要时更换稳定的官方详情/API入口。"
    return "检查最终URL、页面结构和可用官方替代入口；必要时增加站点适配器后复跑。"


def run_source(
    entry: dict[str, Any],
    *,
    out_dir: Path,
    auth_map: dict[str, Any],
    remediation_catalog: dict[str, Any],
    site_timeout: int,
) -> dict[str, Any]:
    source_key = str(entry["source_key"])
    store = KnowledgeStore(out_dir / f"{source_key}.db")
    registry = SourceRegistryService(store)
    source = registry.by_key(source_key)
    remediation = dict(remediation_catalog.get(source_key) or {})
    auth = dict(auth_map.get(source_key) or {})
    fetcher = ResilientFetcher(source_key, auth)
    service = SiteExtractionService(store, registry, out_dir / "raw" / source_key, fetcher=fetcher)
    robot = robots_status(str(source.get("base_url") or ""))
    attempts: list[dict[str, Any]] = []
    started = time.time()
    base_plan = service.plan(source_key)
    base_config = json.loads(json.dumps(base_plan.get("config") or {}))

    previous_handler = signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(max(site_timeout, 60))
    try:
        def allowed_start_urls(urls: list[str]) -> list[str]:
            accepted: list[str] = []
            for candidate in urls:
                status = robots_status(candidate)
                if status["status"] == "disallowed":
                    attempts.append({
                        "label": "robots_skip",
                        "start_urls": [candidate],
                        "exception": "robots disallowed",
                        "robots": status,
                    })
                    continue
                accepted.append(candidate)
            return accepted

        def do_attempt(
            label: str,
            start_urls: list[str] | None = None,
            *,
            controlled_broadening: bool = False,
            structured_api: bool = False,
            direct_content: bool = False,
        ) -> dict[str, Any]:
            config = json.loads(json.dumps(base_config))
            candidate_urls = list(start_urls or config.get("start_urls") or [])
            candidate_urls = allowed_start_urls(candidate_urls)
            if not candidate_urls:
                raise PermissionError("all candidate start URLs are disallowed by robots")
            config["start_urls"] = candidate_urls
            request = dict(config.get("request") or {})
            request["headers"] = {
                **dict(request.get("headers") or {}),
                **{str(k): str(v) for k, v in (remediation.get("request_headers") or {}).items()},
                **fetcher.auth_headers,
            }
            config["request"] = request
            if controlled_broadening:
                discovery = dict(config.get("discovery") or {})
                source_type = str(source.get("source_type") or "")
                generic_patterns = {
                    "regulation": [
                        r"/(?:law|laws|act|acts|legislation|regulation|regulations|statute|statutes|legal|eli|document|documents|norm|wetten|lov|lag|laki|retsinformation)/",
                    ],
                    "standard": [
                        r"/(?:standard|standards|norm|norme|normen|catalog|catalogue|search|shop|product)/",
                    ],
                    "certification": [
                        r"/(?:certification|certificate|notified|body|bodies|accreditation|scope)/",
                    ],
                    "gma": [
                        r"/(?:product|safety|recall|energy|label|compliance|requirement|guidance|regulation|market)/",
                    ],
                }
                merged_patterns = list(discovery.get("include_url_patterns") or [])
                for pattern in generic_patterns.get(source_type, generic_patterns["gma"]):
                    if pattern not in merged_patterns:
                        merged_patterns.append(pattern)
                discovery["include_url_patterns"] = merged_patterns
                discovery["follow_details"] = True
                if config.get("document_policy") != "metadata_only":
                    discovery["fetch_attachments"] = True
                config["discovery"] = discovery
                limits = dict(config.get("limits") or {})
                limits["max_pages"] = min(max(int(limits.get("max_pages") or 6), 8), 12)
                limits["max_details"] = min(max(int(limits.get("max_details") or 0), 16), 30)
                limits["max_attachments"] = min(max(int(limits.get("max_attachments") or 0), 8), 20)
                limits["max_items"] = min(max(int(limits.get("max_items") or 0), 160), 300)
                config["limits"] = limits
                pagination = dict(config.get("pagination") or {})
                pagination["max_pages"] = min(max(int(pagination.get("max_pages") or 6), 8), 12)
                config["pagination"] = pagination
            service.update_plan(source_key, config=config, enabled=True)
            run = service.run(source_key, auto_ingest=False)
            result = {
                "label": label,
                "start_urls": list(config.get("start_urls") or []),
                "run": run,
                "assessment": assessment(
                    service,
                    source_key,
                    run,
                    structured_api=structured_api,
                    metadata_only=config.get("document_policy") == "metadata_only",
                    direct_content=direct_content,
                ),
            }
            attempts.append(result)
            return result

        def do_binary_attempt(urls: list[str]) -> dict[str, Any]:
            keywords = [
                str(item).lower()
                for item in remediation.get("binary_keywords") or []
                if str(item).strip()
            ]
            errors: list[str] = []
            best_text_len = 0
            for index, candidate in enumerate(urls, 1):
                status = robots_status(candidate)
                if status["status"] == "disallowed":
                    errors.append(f"{candidate}: robots disallowed")
                    continue
                try:
                    body, http_status, content_type = fetcher(
                        candidate,
                        fetcher.auth_headers,
                        int((base_config.get("request") or {}).get("timeout_seconds") or 25),
                        int((base_config.get("request") or {}).get("max_bytes") or 12 * 1024 * 1024),
                    )
                    path_suffix = Path(urlparse(candidate).path).suffix.lower()
                    suffix = path_suffix if path_suffix in {".pdf", ".xml", ".json", ".xlsx", ".xls"} else ".bin"
                    target = out_dir / "binary" / source_key / f"official-{index}{suffix}"
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(body)
                    parsed = parse_file(target)
                    text_value = str(parsed.full_text or "")
                    best_text_len = max(best_text_len, len(text_value.strip()))
                    low = text_value.lower()
                    matched = [item for item in keywords if item in low]
                    if len(text_value.strip()) >= 120 and (not keywords or matched):
                        result = {
                            "label": "official_binary_document",
                            "start_urls": [candidate],
                            "run": {
                                "status": "completed",
                                "pages_fetched": 1,
                                "items_discovered": 1,
                                "attachments_discovered": 1,
                                "error": "",
                            },
                            "assessment": {
                                "verdict": "passed",
                                "evidence_mode": "official_binary_document",
                                "pages": 1,
                                "items": 1,
                                "listing_items": 0,
                                "details": 0,
                                "attachments": 1,
                                "relevant": 1,
                                "relevant_business": 1,
                                "relevant_listing": 0,
                                "needs_review": 0,
                                "irrelevant": 0,
                                "reviewed": 0,
                                "meaningful_items": 1,
                                "matched_keywords": matched,
                                "parsed_chars": len(text_value.strip()),
                                "content_type": content_type,
                                "http_status": http_status,
                                "raw_path": str(target),
                                "error": "",
                            },
                        }
                        attempts.append(result)
                        return result
                    errors.append(
                        f"{candidate}: parsed_chars={len(text_value.strip())}, matched={matched}"
                    )
                except Exception as exc:
                    errors.append(f"{candidate}: {exc}")
            result = {
                "label": "official_binary_document",
                "start_urls": list(urls),
                "exception": "; ".join(errors)[:3000],
                "assessment": {
                    "verdict": "failed",
                    "evidence_mode": "official_binary_document",
                    "pages": 0,
                    "items": 0,
                    "details": 0,
                    "attachments": 0,
                    "relevant": 0,
                    "meaningful_items": 0,
                    "parsed_chars": best_text_len,
                    "error": "; ".join(errors)[:3000],
                },
            }
            attempts.append(result)
            return result

        rank = {"passed": 3, "partial": 2, "failed": 1}
        best: dict[str, Any] | None = None

        primary_urls = list(base_config.get("start_urls") or [])
        if robot["status"] != "disallowed":
            try:
                first = do_attempt("primary", primary_urls)
                best = first
            except Exception as exc:
                attempts.append({"label": "primary", "exception": str(exc)[:1000]})
        else:
            attempts.append({
                "label": "primary",
                "start_urls": primary_urls,
                "exception": "base source robots disallowed; trying official alternatives instead",
                "robots": robot,
            })

        alternates = list(remediation.get("alternate_start_urls") or [])
        final_url = str(source.get("last_final_url") or "").strip()
        if final_url and final_url not in alternates and final_url != source.get("base_url"):
            alternates.append(final_url)
        if alternates and (best is None or best["assessment"]["verdict"] != "passed"):
            try:
                second = do_attempt(
                    "official_alternate",
                    alternates,
                    structured_api=bool(remediation.get("structured_api")),
                    direct_content=bool(remediation.get("direct_content")),
                )
                if best is None or rank[second["assessment"]["verdict"]] > rank[best["assessment"]["verdict"]]:
                    best = second
            except Exception as exc:
                attempts.append({"label": "official_alternate", "exception": str(exc)[:1000]})

        binary_documents = list(remediation.get("binary_documents") or [])
        if binary_documents and (best is None or best["assessment"]["verdict"] != "passed"):
            binary_result = do_binary_attempt(binary_documents)
            if best is None or rank[binary_result["assessment"]["verdict"]] > rank[best["assessment"]["verdict"]]:
                best = binary_result

        if best is None or best["assessment"]["verdict"] != "passed":
            broad_urls = alternates or primary_urls
            try:
                third = do_attempt(
                    "controlled_broadening",
                    broad_urls,
                    controlled_broadening=True,
                    structured_api=bool(remediation.get("structured_api")),
                    direct_content=bool(remediation.get("direct_content")),
                )
                if best is None or rank[third["assessment"]["verdict"]] > rank[best["assessment"]["verdict"]]:
                    best = third
            except Exception as exc:
                attempts.append({"label": "controlled_broadening", "exception": str(exc)[:1000]})

        if best is None:
            verdict = "failed"
            best_assessment: dict[str, Any] = {}
        else:
            verdict = best["assessment"]["verdict"]
            best_assessment = dict(best["assessment"])

        all_error = " ".join(
            str((attempt.get("assessment") or {}).get("error") or attempt.get("exception") or "")
            for attempt in attempts
        )
        if verdict == "failed":
            reason = classify_restriction(all_error, fetcher.events)
            if remediation.get("requires_registration") and not auth:
                reason = "requires_registration_or_login"
            final_verdict = "restricted" if reason in {
                "robots_disallowed", "captcha_or_bot_challenge",
                "requires_registration_or_login", "access_restricted",
            } else "failed"
        else:
            reason = ""
            final_verdict = verdict

        if final_verdict == "passed" and remediation.get("coverage_limited"):
            final_verdict = "partial"
            reason = "coverage_limited_official_fallback"

        return {
            **entry,
            "source_name": source.get("source_name", ""),
            "base_url": source.get("base_url", ""),
            "source_type": source.get("source_type", ""),
            "access_method": source.get("access_method", ""),
            "final_verdict": final_verdict,
            "restriction_reason": reason,
            "recommendation": "" if final_verdict == "passed" else (
                str(remediation.get("coverage_note") or "").strip()
                if reason == "coverage_limited_official_fallback"
                else recommendation(reason, bool(auth), remediation)
            ),
            "robots": robot,
            "used_auth": bool(auth),
            "requires_registration": bool(remediation.get("requires_registration")),
            "registration_url": str(remediation.get("registration_url") or ""),
            "remediation_notes": str(remediation.get("notes") or ""),
            "best_assessment": best_assessment,
            "fetch_events": fetcher.events[-100:],
            "attempts": attempts,
            "elapsed_seconds": round(time.time() - started, 1),
        }
    except SiteTimeout as exc:
        reason = "timeout"
        return {
            **entry,
            "source_name": source.get("source_name", ""),
            "base_url": source.get("base_url", ""),
            "source_type": source.get("source_type", ""),
            "access_method": source.get("access_method", ""),
            "final_verdict": "failed",
            "restriction_reason": reason,
            "recommendation": recommendation(reason, bool(auth), remediation),
            "robots": robot,
            "used_auth": bool(auth),
            "remediation_notes": str(remediation.get("notes") or ""),
            "fetch_events": fetcher.events[-60:],
            "attempts": attempts,
            "exception": str(exc),
            "elapsed_seconds": round(time.time() - started, 1),
        }
    except Exception as exc:
        reason = classify_restriction(str(exc), fetcher.events)
        final_verdict = "restricted" if reason in {
            "captcha_or_bot_challenge", "requires_registration_or_login", "access_restricted",
        } else "failed"
        return {
            **entry,
            "source_name": source.get("source_name", ""),
            "base_url": source.get("base_url", ""),
            "source_type": source.get("source_type", ""),
            "access_method": source.get("access_method", ""),
            "final_verdict": final_verdict,
            "restriction_reason": reason,
            "recommendation": recommendation(reason, bool(auth), remediation),
            "robots": robot,
            "used_auth": bool(auth),
            "remediation_notes": str(remediation.get("notes") or ""),
            "fetch_events": fetcher.events[-60:],
            "attempts": attempts,
            "exception": str(exc)[:2000],
            "elapsed_seconds": round(time.time() - started, 1),
        }
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)
        fetcher.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-index", type=int, required=True)
    parser.add_argument("--batch-count", type=int, default=5)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--site-timeout", type=int, default=int(os.getenv("SITE_DEEP_TIMEOUT_SECONDS", "180")))
    parser.add_argument("--retry-nonpassed-from", default="")
    args = parser.parse_args()

    wave = load_json(FIRST_WAVE_PATH, [])
    if len(wave) != 50:
        raise SystemExit(f"expected exactly 50 first-wave sources, got {len(wave)}")
    remediation = load_json(REMEDIATION_PATH, {})
    auth_raw = os.getenv("FIRST50_AUTH_JSON", "").strip()
    try:
        auth_map = json.loads(auth_raw) if auth_raw else {}
    except ValueError:
        raise SystemExit("FIRST50_AUTH_JSON is not valid JSON")

    selected = [item for idx, item in enumerate(wave) if idx % args.batch_count == args.batch_index]
    prior_status: dict[str, str] = {}
    if args.retry_nonpassed_from:
        prior_payload = load_json(Path(args.retry_nonpassed_from), {})
        prior_status = dict(prior_payload.get("status") or {})
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for item in selected:
        source_key = str(item["source_key"])
        if prior_status.get(source_key) == "passed":
            result = {
                **item,
                "source_name": "",
                "base_url": "",
                "source_type": "",
                "access_method": "",
                "final_verdict": "passed",
                "restriction_reason": "",
                "recommendation": "",
                "robots": {"status": "carried_forward"},
                "used_auth": False,
                "remediation_notes": "Round1 passed; no new network request in retry round.",
                "fetch_events": [],
                "attempts": [],
                "carried_forward_from_round1": True,
                "elapsed_seconds": 0.0,
            }
            results.append(result)
            print(f"[deep-validate] {item['order']:02d}/50 {source_key} => passed (carried-forward)", flush=True)
            continue
        print(f"[deep-validate] {item['order']:02d}/50 {source_key}", flush=True)
        result = run_source(
            item,
            out_dir=out_dir,
            auth_map=auth_map,
            remediation_catalog=remediation,
            site_timeout=args.site_timeout,
        )
        results.append(result)
        print(
            f"[deep-validate] {item['source_key']} => {result['final_verdict']} "
            f"({result.get('restriction_reason') or 'ok'})",
            flush=True,
        )

    payload = {
        "batch_index": args.batch_index,
        "batch_count": args.batch_count,
        "sources": results,
        "summary": {
            "processed": len(results),
            "passed": sum(1 for x in results if x["final_verdict"] == "passed"),
            "partial": sum(1 for x in results if x["final_verdict"] == "partial"),
            "restricted": sum(1 for x in results if x["final_verdict"] == "restricted"),
            "failed": sum(1 for x in results if x["final_verdict"] == "failed"),
        },
    }
    output = out_dir / f"batch-{args.batch_index}.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
