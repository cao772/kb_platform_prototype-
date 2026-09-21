from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

from app.source_registry import SourceRegistryService


class SourceVerificationService:
    def __init__(self, source_registry: SourceRegistryService):
        self.source_registry = source_registry

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _robots_allowed(url: str, *, timeout: int = 8) -> str:
        try:
            parsed = urlparse(url)
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
            rp = RobotFileParser()
            rp.set_url(robots_url)
            request = Request(
                robots_url,
                headers={"User-Agent": "KnowledgePlatformSourceVerifier/1.0"},
                method="GET",
            )
            with urlopen(request, timeout=timeout) as response:
                body = response.read(256 * 1024).decode("utf-8", errors="ignore")
            rp.parse(body.splitlines())
            return "allowed" if rp.can_fetch("KnowledgePlatformSourceVerifier/1.0", url) else "disallowed"
        except Exception:
            return "unknown"

    @staticmethod
    def _classify_success(source: dict[str, Any], *, content_type: str, robots_allowed: str) -> tuple[str, str]:
        method = str(source.get("access_method") or "html")
        source_type = str(source.get("source_type") or "")
        notes = (str(source.get("notes") or "") + " " + str(source.get("verification_note") or "")).lower()

        if robots_allowed == "disallowed":
            return "manual_authorized", "robots.txt不允许当前自动抓取路径，保留人工/授权处理。"
        if method == "metadata_only":
            return "metadata_only", "网站可访问；按公开元数据采集，标准全文仍受许可边界约束。"
        if source_type == "standard" and any(token in notes for token in ("licence", "license", "版权", "授权")):
            return "manual_authorized", "网站可访问，但标准全文存在版权或许可边界，自动采集仅限公开元数据。"
        if method == "mixed":
            return "adapter", f"网站可访问（{content_type or '未知内容类型'}），但存在多种资料形态，建议配置站点级适配。"
        if any(token in (content_type or "").lower() for token in ("html", "pdf", "json", "xml", "text", "rss", "atom")):
            return "direct", f"网站可访问，返回{content_type or '可识别内容'}，具备直接采集条件。"
        return "adapter", f"网站可访问，但内容类型为{content_type or '未知'}，需进一步确认解析适配。"

    def verify_one(self, source_id: int, *, timeout: int = 12) -> dict[str, Any]:
        source = self.source_registry.detail(source_id)
        url = str(source.get("base_url") or "").strip()
        checked_at = self._now()
        robots_allowed = self._robots_allowed(url, timeout=min(timeout, 8))
        started = time.perf_counter()

        try:
            request = Request(
                url,
                headers={
                    "User-Agent": "KnowledgePlatformSourceVerifier/1.0",
                    "Accept": "text/html,application/xhtml+xml,application/pdf,application/json,application/xml,text/xml,*/*;q=0.8",
                    "Range": "bytes=0-262143",
                },
                method="GET",
            )
            with urlopen(request, timeout=timeout) as response:
                status = int(getattr(response, "status", 200) or 200)
                content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip()
                final_url = str(response.geturl() or url)
                response.read(64 * 1024)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            harvestability, note = self._classify_success(
                source,
                content_type=content_type,
                robots_allowed=robots_allowed,
            )
            return self.source_registry.record_verification(source_id, {
                "harvestability": harvestability,
                "verification_status": "verified",
                "last_verified_at": checked_at,
                "last_http_status": status,
                "last_content_type": content_type,
                "last_final_url": final_url,
                "robots_allowed": robots_allowed,
                "verification_note": f"{note} 响应耗时约{elapsed_ms}ms。",
            })
        except HTTPError as exc:
            status = int(exc.code or 0)
            content_type = str(exc.headers.get("Content-Type") or "") if exc.headers else ""
            if status in {401, 403}:
                harvestability = "manual_authorized" if source.get("access_method") == "metadata_only" else "adapter"
                note = f"HTTP {status}，可能存在登录、授权、地区限制或反自动化策略，需要人工确认访问方式。"
            elif status == 429:
                harvestability = "adapter"
                note = "HTTP 429，站点存在频率限制，需要降低访问频率或配置专用适配。"
            elif status in {404, 410}:
                harvestability = "unavailable"
                note = f"HTTP {status}，当前登记地址不可用，需要更新来源入口。"
            else:
                harvestability = "adapter"
                note = f"HTTP {status}，当前访问异常，建议复核入口或配置适配。"
            return self.source_registry.record_verification(source_id, {
                "harvestability": harvestability,
                "verification_status": "failed",
                "last_verified_at": checked_at,
                "last_http_status": status,
                "last_content_type": content_type,
                "last_final_url": url,
                "robots_allowed": robots_allowed,
                "verification_note": note,
            })
        except (URLError, TimeoutError, OSError) as exc:
            return self.source_registry.record_verification(source_id, {
                "harvestability": "unavailable",
                "verification_status": "failed",
                "last_verified_at": checked_at,
                "last_http_status": 0,
                "last_content_type": "",
                "last_final_url": url,
                "robots_allowed": robots_allowed,
                "verification_note": f"联网核验失败：{exc}",
            })

    def verify_many(self, source_ids: list[int], *, timeout: int = 12, max_workers: int = 6) -> dict[str, Any]:
        ids = sorted({int(item) for item in source_ids if int(item) > 0})
        if not ids:
            raise ValueError("source_ids is required")
        if len(ids) > 50:
            raise ValueError("batch verification supports at most 50 sources per request")

        results: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=max(1, min(int(max_workers), 8))) as executor:
            future_map = {executor.submit(self.verify_one, source_id, timeout=timeout): source_id for source_id in ids}
            for future in as_completed(future_map):
                source_id = future_map[future]
                try:
                    item = future.result()
                    results.append({
                        "id": source_id,
                        "source_key": item.get("source_key"),
                        "harvestability": item.get("harvestability"),
                        "verification_status": item.get("verification_status"),
                        "http_status": item.get("last_http_status"),
                        "note": item.get("verification_note"),
                    })
                except Exception as exc:
                    results.append({
                        "id": source_id,
                        "verification_status": "failed",
                        "error": str(exc),
                    })

        order = {source_id: index for index, source_id in enumerate(ids)}
        results.sort(key=lambda item: order.get(int(item.get("id") or 0), 99999))
        return {
            "requested": len(ids),
            "verified": sum(1 for item in results if item.get("verification_status") == "verified"),
            "failed": sum(1 for item in results if item.get("verification_status") == "failed"),
            "results": results,
            "summary": self.source_registry.verification_summary(),
        }
