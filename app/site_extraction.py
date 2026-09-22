from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from app.ingest import ingest_file
from app.source_collection import FIRST_WAVE_PATH
from app.source_registry import SourceRegistryService
from app.store import KnowledgeStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


FetchResult = tuple[bytes, int, str]
Fetcher = Callable[[str, dict[str, str], int, int], FetchResult]


def _default_fetcher(url: str, headers: dict[str, str], timeout: int, max_bytes: int) -> FetchResult:
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=timeout) as response:
        status = int(getattr(response, "status", 200) or 200)
        content_type = str(response.headers.get("Content-Type") or "")
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"download exceeds max_bytes: {max_bytes}")
    return data, status, content_type


TRACKING_QUERY_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "gclid", "mc_cid", "mc_eid",
}


def canonical_url(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return str(url or "").strip()
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in TRACKING_QUERY_KEYS
    ]
    return urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        parsed.path or "/",
        "",
        urlencode(query, doseq=True),
        "",
    ))


class _HTMLCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self._link_href = ""
        self._link_text: list[str] = []
        self.title = ""
        self._in_title = False
        self._title_parts: list[str] = []
        self._heading_level = 0
        self._heading_parts: list[str] = []
        self.headings: list[dict[str, str]] = []
        self.text_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {str(k).lower(): str(v or "") for k, v in attrs}
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "a":
            self._link_href = values.get("href", "")
            self._link_text = []
        elif tag == "title":
            self._in_title = True
            self._title_parts = []
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading_level = int(tag[1])
            self._heading_parts = []
        elif tag == "meta":
            name = values.get("name") or values.get("property")
            content = values.get("content")
            if name and content:
                self.meta[name.lower()] = content

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag == "a" and self._link_href:
            text = " ".join(" ".join(self._link_text).split())
            self.links.append({"href": self._link_href, "text": text})
            self._link_href = ""
            self._link_text = []
        elif tag == "title":
            self._in_title = False
            self.title = " ".join(" ".join(self._title_parts).split())
        elif self._heading_level and tag == f"h{self._heading_level}":
            text = " ".join(" ".join(self._heading_parts).split())
            if text:
                self.headings.append({"level": str(self._heading_level), "text": text})
            self._heading_level = 0
            self._heading_parts = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = " ".join(str(data or "").split())
        if not text:
            return
        self.text_parts.append(text)
        if self._link_href:
            self._link_text.append(text)
        if self._in_title:
            self._title_parts.append(text)
        if self._heading_level:
            self._heading_parts.append(text)


def _default_plan(source: dict[str, Any]) -> dict[str, Any]:
    source_type = str(source.get("source_type") or "")
    access = str(source.get("access_method") or "mixed")
    metadata_only = access == "metadata_only" or source_type == "standard"
    if source_type == "regulation":
        detail_keywords = [
            "law", "laws", "act", "regulation", "regulations", "decree", "ordinance",
            "legislation", "statute", "directive", "decision", "rule", "rules",
            "法", "法规", "法令", "政令", "省令",
        ]
        field_patterns = {
            "code": [r"\b(?:Regulation|Directive|Decision|Act|Law|Decree|Order|Ordinance)\s*(?:\([A-Z]{2,4}\))?\s*[A-Z0-9()/.\-]+", r"\b\d{4}/\d{2,5}(?:/[A-Z]{2,5})?\b"],
            "date": [r"\b(?:19|20)\d{2}[-/.](?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])\b"],
            "status": [r"\b(?:in force|current|repealed|revoked|superseded|amended|effective)\b"],
        }
    elif source_type == "certification":
        detail_keywords = ["certification", "certificate", "notified", "body", "scope", "accreditation", "认证", "机构", "公告机构"]
        field_patterns = {
            "code": [r"\b[A-Z]{1,8}[-/]?\d{2,8}\b"],
            "date": [r"\b(?:19|20)\d{2}[-/.](?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])\b"],
            "status": [r"\b(?:active|suspended|withdrawn|valid|expired)\b"],
        }
    elif source_type == "standard":
        detail_keywords = ["standard", "standards", "catalogue", "catalog", "norm", "norme", "standardization", "标准"]
        field_patterns = {
            "code": [r"\b(?:ISO|IEC|EN|DIN|BS|NF|UNI|UNE|NEN|SFS|SIS|AS/NZS|AS|CSA|ANSI)[\s-]*[A-Z0-9][A-Z0-9:/.\-]{1,30}\b"],
            "date": [r"\b(?:19|20)\d{2}\b"],
            "status": [r"\b(?:current|published|withdrawn|superseded|draft)\b"],
        }
    else:
        detail_keywords = [
            "product", "safety", "recall", "energy", "label", "market", "access",
            "compliance", "registration", "guidance", "requirement", "产品", "召回", "能效", "准入",
        ]
        field_patterns = {
            "code": [r"\b[A-Z]{2,10}[-/]?[A-Z0-9]{2,20}\b"],
            "date": [r"\b(?:19|20)\d{2}[-/.](?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])\b"],
            "status": [r"\b(?:active|current|recalled|withdrawn|closed|open|effective)\b"],
        }

    return {
        "version": "1",
        "enabled": True,
        "start_urls": [str(source.get("base_url") or "")],
        "crawl_scope": str(source.get("crawl_scope") or ""),
        "document_policy": "metadata_only" if metadata_only else "public_fulltext",
        "request": {
            "timeout_seconds": 25,
            "max_bytes": 12 * 1024 * 1024,
            "headers": {},
        },
        "limits": {
            "max_pages": 6,
            "max_items": 120,
            "max_details": 50,
            "max_attachments": 20,
        },
        "discovery": {
            "same_host": True,
            "follow_details": True,
            "fetch_attachments": not metadata_only,
            "include_url_patterns": [],
            "exclude_url_patterns": [
                r"/login", r"/signin", r"/account", r"/privacy", r"/cookie",
                r"/contact", r"/careers", r"/newsroom(?:/)?$",
            ],
            "detail_text_keywords": detail_keywords,
            "attachment_extensions": [".pdf", ".doc", ".docx", ".xls", ".xlsx", ".xml", ".json", ".zip"],
            "attachment_text_keywords": [
                "pdf", "download", "attachment", "annex", "appendix", "document",
                "full text", "official journal", "下载", "附件", "正文",
            ],
        },
        "pagination": {
            "mode": "links",
            "next_text_keywords": ["next", "next page", "older", "more", ">", "»", "下一页", "次へ"],
            "url_patterns": [r"[?&](?:page|p|start|offset|from)=\d+", r"/page/\d+"],
            "max_pages": 6,
        },
        "fields": field_patterns,
        "content": {
            "min_text_chars": 80,
            "max_excerpt_chars": 4000,
            "keep_headings": True,
        },
        "relevance": {
            "enabled": True,
            "exclude_text_keywords": [
                "privacy policy", "cookie policy", "accessibility", "contact us",
                "careers", "sitemap", "help center",
                "隐私政策", "联系我们", "无障碍", "招聘", "网站地图",
            ],
            "min_text_chars": 80,
            "allow_field_match": True,
            "inherit_parent_for_attachments": True,
        },
        "notes": "可在来源采集页面逐站调整分页、详情、附件、字段正则、业务相关性和采集上限。",
    }


SPECIAL_PLAN_OVERRIDES: dict[str, dict[str, Any]] = {
    "EU-EURLEX": {
        "discovery": {
            "include_url_patterns": [r"/legal-content/", r"/eli/", r"CELEX"],
            "detail_text_keywords": ["regulation", "directive", "decision", "consolidated", "official journal"],
        },
        "fields": {
            "code": [r"\b(?:Regulation|Directive|Decision)\s*\(?(?:EU|EC|EEC)?\)?\s*(?:No\s*)?\d{2,4}/\d{2,5}\b", r"\bCELEX[:\s]*[0-9A-Z]+\b"],
            "date": [r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b"],
            "status": [r"\b(?:in force|no longer in force|repealed|consolidated)\b"],
        },
    },
    "US-FEDREG": {
        "discovery": {
            "include_url_patterns": [r"/documents/", r"/api/v1/documents"],
            "detail_text_keywords": ["rule", "proposed rule", "notice", "presidential document"],
        },
        "pagination": {
            "mode": "links",
            "next_text_keywords": ["next"],
            "url_patterns": [r"[?&]page=\d+"],
            "max_pages": 8,
        },
        "fields": {
            "code": [r"\b(?:RIN|Document Number)[:\s]+[A-Z0-9\-]+", r"\b\d{4}-\d{4,6}\b"],
            "date": [r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b"],
            "status": [r"\b(?:final rule|proposed rule|notice|correction)\b"],
        },
    },
    "JP-LAW": {
        "discovery": {
            "include_url_patterns": [r"/law/", r"/api/"],
            "detail_text_keywords": ["法令", "法律", "政令", "省令", "law"],
        },
        "fields": {
            "code": [r"(?:法律|政令|省令)第?\s*[一二三四五六七八九十百千0-9]+号"],
            "date": [r"(?:令和|平成|昭和)\s*[一二三四五六七八九十0-9]+年"],
            "status": [r"(?:施行|廃止|改正|現行)"],
        },
    },
    "GB-LAW": {
        "discovery": {
            "include_url_patterns": [r"/ukpga/", r"/uksi/", r"/eur/", r"/contents", r"/made", r"/data\.xml"],
            "detail_text_keywords": ["act", "regulations", "order", "rules", "contents", "original", "latest available"],
        },
    },
    "EU-SAFETY-GATE": {
        "limits": {"max_pages": 10, "max_items": 200, "max_details": 100, "max_attachments": 20},
        "discovery": {
            "detail_text_keywords": ["alert", "notification", "recall", "product", "risk"],
        },
        "fields": {
            "code": [r"\b[A-Z][0-9]{2}/[0-9]{4}\b", r"\bA12/[0-9]{4}/[0-9]{2}\b"],
            "date": [r"\b(?:19|20)\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\b"],
            "status": [r"\b(?:recall|withdrawal|warning|ban|rejected at border)\b"],
        },
    },
    "EU-NANDO": {
        "document_policy": "metadata_only",
        "discovery": {
            "fetch_attachments": False,
            "detail_text_keywords": ["notified body", "body", "scope", "directive", "regulation"],
        },
        "fields": {
            "code": [r"\bNB\s*\d{4}\b", r"\b\d{4}\b"],
            "date": [r"\b(?:19|20)\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\b"],
            "status": [r"\b(?:active|withdrawn|suspended|notification)\b"],
        },
    },
    "US-CPSC": {
        "discovery": {
            "detail_text_keywords": ["recall", "regulation", "mandatory standard", "safety standard", "guidance"],
        },
        "fields": {
            "code": [r"\bRecall No\.\s*\d{2}-\d{3,4}\b", r"\b16\s+CFR\s+Part\s+\d+\b"],
            "date": [r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+(?:19|20)\d{2}\b"],
            "status": [r"\b(?:recall|mandatory standard|ban|rule)\b"],
        },
    },
    "US-DOE-EFFICIENCY": {
        "discovery": {
            "detail_text_keywords": ["current standard", "test procedure", "compliance", "waiver", "final rule", "proposed rule"],
        },
        "fields": {
            "code": [r"\b10\s+CFR\s+(?:Part\s+)?(?:429|430|431)(?:\.[0-9A-Za-z()]+)?\b", r"\bEERE-\d{4}-BT-[A-Z]+-\d{4}\b"],
            "date": [r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b"],
            "status": [r"\b(?:current standard|current test procedure|final rule|proposed rule)\b"],
        },
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(dict(result[key]), value)
        else:
            result[key] = value
    return result


@dataclass
class ParsedPage:
    title: str
    text: str
    headings: list[dict[str, str]]
    links: list[dict[str, str]]
    meta: dict[str, str]


class SiteExtractionService:
    def __init__(
        self,
        store: KnowledgeStore,
        source_registry: SourceRegistryService,
        download_dir: str | Path,
        *,
        fetcher: Fetcher | None = None,
    ):
        self.store = store
        self.source_registry = source_registry
        self.download_dir = Path(download_dir)
        self.fetcher = fetcher or _default_fetcher
        self._ensure_schema()
        self._seed_first_wave_plans()

    def _ensure_schema(self) -> None:
        with self.store.lock:
            self.store.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS source_extraction_plans (
                    source_key TEXT PRIMARY KEY,
                    enabled INTEGER DEFAULT 1,
                    config_json TEXT NOT NULL,
                    origin TEXT DEFAULT 'seed',
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS source_deep_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT DEFAULT '',
                    pages_fetched INTEGER DEFAULT 0,
                    items_discovered INTEGER DEFAULT 0,
                    attachments_discovered INTEGER DEFAULT 0,
                    documents_ingested INTEGER DEFAULT 0,
                    changed_items INTEGER DEFAULT 0,
                    error TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_source_deep_runs_source
                    ON source_deep_runs(source_key,id DESC);
                CREATE TABLE IF NOT EXISTS source_extracted_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_key TEXT NOT NULL,
                    canonical_url TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    item_type TEXT NOT NULL,
                    title TEXT DEFAULT '',
                    content_type TEXT DEFAULT '',
                    sha256 TEXT DEFAULT '',
                    fields_json TEXT DEFAULT '{}',
                    metadata_json TEXT DEFAULT '{}',
                    text_excerpt TEXT DEFAULT '',
                    raw_path TEXT DEFAULT '',
                    document_id INTEGER,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    changed_at TEXT NOT NULL,
                    UNIQUE(source_key,canonical_url)
                );
                CREATE INDEX IF NOT EXISTS idx_source_extracted_items_source
                    ON source_extracted_items(source_key,id DESC);
                """
            )
            self.store.conn.commit()

    def _seed_first_wave_plans(self) -> None:
        if not FIRST_WAVE_PATH.exists():
            return
        wave = json.loads(FIRST_WAVE_PATH.read_text(encoding="utf-8"))
        with self.store.lock:
            for entry in wave:
                source_key = str(entry.get("source_key") or "").strip()
                if not source_key:
                    continue
                existing = self.store.conn.execute(
                    "SELECT source_key FROM source_extraction_plans WHERE source_key=?",
                    (source_key,),
                ).fetchone()
                if existing:
                    continue
                try:
                    source = self.source_registry.by_key(source_key)
                except Exception:
                    continue
                config = _default_plan(source)
                config = _deep_merge(config, SPECIAL_PLAN_OVERRIDES.get(source_key, {}))
                self.store.conn.execute(
                    """INSERT INTO source_extraction_plans(source_key,enabled,config_json,origin)
                       VALUES(?,?,?,?)""",
                    (source_key, 1, json.dumps(config, ensure_ascii=False), "first50_seed"),
                )
            self.store.conn.commit()

    def plan(self, source_key: str) -> dict[str, Any]:
        with self.store.lock:
            row = self.store.conn.execute(
                "SELECT * FROM source_extraction_plans WHERE source_key=?",
                (str(source_key),),
            ).fetchone()
        if not row:
            source = self.source_registry.by_key(source_key)
            config = _deep_merge(_default_plan(source), SPECIAL_PLAN_OVERRIDES.get(source_key, {}))
            return {
                "source_key": source_key,
                "enabled": True,
                "origin": "generated",
                "config": config,
            }
        item = dict(row)
        item["enabled"] = bool(item.get("enabled"))
        item["config"] = json.loads(item.pop("config_json") or "{}")
        return item

    def list_plans(self, *, source_key: str = "") -> dict[str, Any]:
        params: list[Any] = []
        where = ""
        if source_key:
            where = "WHERE source_key=?"
            params.append(source_key)
        with self.store.lock:
            rows = self.store.conn.execute(
                f"SELECT * FROM source_extraction_plans {where} ORDER BY source_key",
                params,
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["enabled"] = bool(item.get("enabled"))
            item["config"] = json.loads(item.pop("config_json") or "{}")
            items.append(item)
        return {
            "items": items,
            "summary": {
                "plans": len(items),
                "enabled": sum(1 for item in items if item["enabled"]),
                "metadata_only": sum(1 for item in items if item["config"].get("document_policy") == "metadata_only"),
            },
        }

    @staticmethod
    def _validate_config(config: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(config, dict):
            raise ValueError("config must be an object")
        limits = dict(config.get("limits") or {})
        limits["max_pages"] = max(1, min(int(limits.get("max_pages") or 6), 100))
        limits["max_items"] = max(1, min(int(limits.get("max_items") or 120), 5000))
        limits["max_details"] = max(0, min(int(limits.get("max_details") or 50), 1000))
        limits["max_attachments"] = max(0, min(int(limits.get("max_attachments") or 20), 500))
        config["limits"] = limits
        request = dict(config.get("request") or {})
        request["timeout_seconds"] = max(1, min(int(request.get("timeout_seconds") or 25), 120))
        request["max_bytes"] = max(1024, min(int(request.get("max_bytes") or 12 * 1024 * 1024), 100 * 1024 * 1024))
        request["headers"] = dict(request.get("headers") or {})
        config["request"] = request
        discovery = dict(config.get("discovery") or {})
        for key in ("include_url_patterns", "exclude_url_patterns"):
            patterns = list(discovery.get(key) or [])
            for pattern in patterns:
                re.compile(str(pattern))
            discovery[key] = [str(item) for item in patterns]
        discovery["detail_text_keywords"] = [str(item) for item in discovery.get("detail_text_keywords") or []]
        discovery["attachment_extensions"] = [str(item).lower() for item in discovery.get("attachment_extensions") or []]
        discovery["attachment_text_keywords"] = [str(item) for item in discovery.get("attachment_text_keywords") or []]
        config["discovery"] = discovery
        pagination = dict(config.get("pagination") or {})
        pagination["max_pages"] = max(1, min(int(pagination.get("max_pages") or limits["max_pages"]), 100))
        pagination["next_text_keywords"] = [str(item) for item in pagination.get("next_text_keywords") or []]
        patterns = [str(item) for item in pagination.get("url_patterns") or []]
        for pattern in patterns:
            re.compile(pattern)
        pagination["url_patterns"] = patterns
        config["pagination"] = pagination
        fields = dict(config.get("fields") or {})
        for name, patterns in fields.items():
            values = [str(item) for item in (patterns or [])]
            for pattern in values:
                re.compile(pattern, re.I)
            fields[str(name)] = values
        config["fields"] = fields
        relevance = dict(config.get("relevance") or {})
        relevance["enabled"] = bool(relevance.get("enabled", True))
        relevance["exclude_text_keywords"] = [
            str(item).strip().lower()
            for item in relevance.get("exclude_text_keywords") or []
            if str(item).strip()
        ]
        relevance["min_text_chars"] = max(0, min(int(relevance.get("min_text_chars") or 80), 20000))
        relevance["allow_field_match"] = bool(relevance.get("allow_field_match", True))
        relevance["inherit_parent_for_attachments"] = bool(
            relevance.get("inherit_parent_for_attachments", True)
        )
        config["relevance"] = relevance
        start_urls = [str(item).strip() for item in config.get("start_urls") or [] if str(item).strip()]
        if not start_urls:
            raise ValueError("at least one start_url is required")
        if not all(url.startswith(("http://", "https://")) for url in start_urls):
            raise ValueError("start_urls must use http/https")
        config["start_urls"] = start_urls
        return config

    def update_plan(self, source_key: str, *, config: dict[str, Any], enabled: bool = True) -> dict[str, Any]:
        self.source_registry.by_key(source_key)
        clean = self._validate_config(dict(config))
        with self.store.lock:
            self.store.conn.execute(
                """INSERT INTO source_extraction_plans(source_key,enabled,config_json,origin,updated_at)
                   VALUES(?,?,?,?,CURRENT_TIMESTAMP)
                   ON CONFLICT(source_key) DO UPDATE SET
                     enabled=excluded.enabled,
                     config_json=excluded.config_json,
                     origin='custom',
                     updated_at=CURRENT_TIMESTAMP""",
                (source_key, 1 if enabled else 0, json.dumps(clean, ensure_ascii=False), "custom"),
            )
            self.store.conn.commit()
        return self.plan(source_key)

    @staticmethod
    def _decode(body: bytes, content_type: str) -> str:
        match = re.search(r"charset=([^;\s]+)", content_type or "", re.I)
        encodings = [match.group(1).strip("\"'") if match else "", "utf-8", "utf-8-sig", "latin-1"]
        for encoding in encodings:
            if not encoding:
                continue
            try:
                return body.decode(encoding)
            except Exception:
                continue
        return body.decode("utf-8", errors="replace")

    def _parse_html(self, body: bytes, content_type: str) -> ParsedPage:
        parser = _HTMLCollector()
        parser.feed(self._decode(body, content_type))
        return ParsedPage(
            title=parser.title,
            text="\n".join(parser.text_parts),
            headings=parser.headings,
            links=parser.links,
            meta=parser.meta,
        )

    @staticmethod
    def _json_walk(value: Any, links: list[dict[str, str]], text: list[str], prefix: str = "") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                label = f"{prefix}.{key}" if prefix else str(key)
                SiteExtractionService._json_walk(item, links, text, label)
        elif isinstance(value, list):
            for idx, item in enumerate(value):
                SiteExtractionService._json_walk(item, links, text, f"{prefix}[{idx}]")
        else:
            scalar = str(value or "")
            if scalar.startswith(("http://", "https://")):
                links.append({"href": scalar, "text": prefix})
            elif scalar:
                text.append(f"{prefix}: {scalar}" if prefix else scalar)

    def _parse_json(self, body: bytes, content_type: str) -> ParsedPage:
        payload = json.loads(self._decode(body, content_type))
        links: list[dict[str, str]] = []
        text: list[str] = []
        self._json_walk(payload, links, text)
        title = ""
        if isinstance(payload, dict):
            title = str(payload.get("title") or payload.get("name") or "")
        return ParsedPage(title=title, text="\n".join(text), headings=[], links=links, meta={})

    def _parse_xml(self, body: bytes) -> ParsedPage:
        root = ElementTree.fromstring(body)
        text: list[str] = []
        links: list[dict[str, str]] = []
        for elem in root.iter():
            if elem.text and elem.text.strip():
                text.append(elem.text.strip())
            for value in elem.attrib.values():
                if str(value).startswith(("http://", "https://")):
                    links.append({"href": str(value), "text": elem.tag})
        return ParsedPage(title="", text="\n".join(text), headings=[], links=links, meta={})

    def _parse_page(self, body: bytes, content_type: str, url: str) -> ParsedPage:
        lowered = (content_type or "").lower()
        path = urlparse(url).path.lower()
        if "json" in lowered or path.endswith(".json"):
            return self._parse_json(body, content_type)
        if "xml" in lowered or path.endswith(".xml"):
            return self._parse_xml(body)
        return self._parse_html(body, content_type)

    @staticmethod
    def _pattern_match(patterns: list[str], value: str) -> bool:
        return any(re.search(pattern, value, re.I) for pattern in patterns)

    def _extract_fields(self, text: str, fields: dict[str, Any]) -> dict[str, list[str]]:
        output: dict[str, list[str]] = {}
        for name, patterns in fields.items():
            values: list[str] = []
            for pattern in patterns or []:
                for match in re.finditer(str(pattern), text, re.I):
                    value = match.group(0).strip()
                    if value and value not in values:
                        values.append(value)
                    if len(values) >= 12:
                        break
                if len(values) >= 12:
                    break
            if values:
                output[str(name)] = values
        return output

    def _evaluate_relevance(
        self,
        *,
        url: str,
        item_type: str,
        title: str,
        text: str,
        fields: dict[str, Any],
        config: dict[str, Any],
        parent_relevant: bool,
        link_text: str,
    ) -> dict[str, Any]:
        relevance = dict(config.get("relevance") or {})
        if not relevance.get("enabled", True) or item_type == "listing":
            return {"status": "relevant", "reasons": ["listing_or_gate_disabled"]}

        combined = "\n".join([url, title, link_text, text[:6000]]).lower()
        excluded = [
            token
            for token in relevance.get("exclude_text_keywords") or []
            if token and token in combined
        ]
        if excluded:
            return {
                "status": "irrelevant",
                "reasons": [f"excluded_keyword:{excluded[0]}"],
            }

        discovery = dict(config.get("discovery") or {})
        signals: list[str] = []
        include_patterns = list(discovery.get("include_url_patterns") or [])
        if include_patterns and self._pattern_match(include_patterns, url):
            signals.append("include_url_pattern")

        detail_keywords = [
            str(item).lower()
            for item in discovery.get("detail_text_keywords") or []
            if str(item).strip()
        ]
        if any(token in combined for token in detail_keywords):
            signals.append("business_keyword")

        if fields and relevance.get("allow_field_match", True):
            signals.append("structured_field_match")

        if (
            item_type == "attachment"
            and parent_relevant
            and relevance.get("inherit_parent_for_attachments", True)
        ):
            signals.append("relevant_parent")

        if signals:
            return {"status": "relevant", "reasons": signals}

        min_chars = int(relevance.get("min_text_chars") or 0)
        if item_type == "detail" and len(text.strip()) < min_chars:
            return {
                "status": "needs_review",
                "reasons": [f"thin_content:{len(text.strip())}<{min_chars}"],
            }
        return {"status": "needs_review", "reasons": ["no_business_signal"]}

    def _classify_links(
        self,
        *,
        current_url: str,
        links: list[dict[str, str]],
        config: dict[str, Any],
    ) -> dict[str, list[dict[str, str]]]:
        discovery = dict(config.get("discovery") or {})
        pagination = dict(config.get("pagination") or {})
        same_host = bool(discovery.get("same_host", True))
        current_host = urlparse(current_url).netloc.lower()
        include_patterns = list(discovery.get("include_url_patterns") or [])
        exclude_patterns = list(discovery.get("exclude_url_patterns") or [])
        detail_keywords = [item.lower() for item in discovery.get("detail_text_keywords") or []]
        attachment_extensions = tuple(discovery.get("attachment_extensions") or [])
        attachment_keywords = [item.lower() for item in discovery.get("attachment_text_keywords") or []]
        next_keywords = [item.lower() for item in pagination.get("next_text_keywords") or []]
        page_patterns = list(pagination.get("url_patterns") or [])

        out = {"detail": [], "pagination": [], "attachment": []}
        seen: set[str] = set()
        for link in links:
            href = str(link.get("href") or "").strip()
            if not href or href.startswith(("#", "mailto:", "javascript:", "tel:")):
                continue
            absolute = canonical_url(urljoin(current_url, href))
            if not absolute or absolute in seen:
                continue
            seen.add(absolute)
            parsed = urlparse(absolute)
            if parsed.scheme not in {"http", "https"}:
                continue
            if same_host and parsed.netloc.lower() != current_host:
                continue
            if exclude_patterns and self._pattern_match(exclude_patterns, absolute):
                continue
            text = str(link.get("text") or "").strip()
            low_text = text.lower()
            low_path = parsed.path.lower()

            if attachment_extensions and low_path.endswith(attachment_extensions):
                out["attachment"].append({"url": absolute, "text": text})
                continue
            if attachment_keywords and any(token in low_text for token in attachment_keywords):
                if any(ext in low_path for ext in attachment_extensions):
                    out["attachment"].append({"url": absolute, "text": text})
                    continue

            if any(token and token in low_text for token in next_keywords) or (page_patterns and self._pattern_match(page_patterns, absolute)):
                out["pagination"].append({"url": absolute, "text": text})
                continue

            included = bool(include_patterns and self._pattern_match(include_patterns, absolute))
            keyword_match = any(token and token in low_text for token in detail_keywords)
            if included or keyword_match:
                out["detail"].append({"url": absolute, "text": text})
        return out

    def _save_raw(self, source_key: str, url: str, body: bytes, content_type: str, item_type: str) -> Path:
        parsed = urlparse(url)
        suffix = Path(parsed.path).suffix.lower()
        if not suffix or len(suffix) > 8:
            lowered = (content_type or "").lower()
            suffix = ".json" if "json" in lowered else ".xml" if "xml" in lowered else ".pdf" if "pdf" in lowered else ".html"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
        folder = self.download_dir / source_key / "deep"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{stamp}_{item_type}_{digest}{suffix}"
        target.write_bytes(body)
        return target

    def _upsert_item(
        self,
        *,
        source_key: str,
        url: str,
        item_type: str,
        title: str,
        content_type: str,
        sha256: str,
        fields: dict[str, Any],
        metadata: dict[str, Any],
        text_excerpt: str,
        raw_path: str,
        document_id: int | None,
    ) -> bool:
        canonical = canonical_url(url)
        now = _now()
        changed = True
        with self.store.lock:
            previous = self.store.conn.execute(
                "SELECT sha256,metadata_json FROM source_extracted_items WHERE source_key=? AND canonical_url=?",
                (source_key, canonical),
            ).fetchone()
            if previous and str(previous["sha256"] or "") == sha256:
                changed = False
            if previous:
                try:
                    previous_metadata = json.loads(previous["metadata_json"] or "{}")
                except (TypeError, ValueError):
                    previous_metadata = {}
                if previous_metadata.get("relevance_review"):
                    metadata["relevance_review"] = previous_metadata["relevance_review"]
            self.store.conn.execute(
                """INSERT INTO source_extracted_items(
                       source_key,canonical_url,source_url,item_type,title,content_type,sha256,
                       fields_json,metadata_json,text_excerpt,raw_path,document_id,
                       first_seen_at,last_seen_at,changed_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(source_key,canonical_url) DO UPDATE SET
                       source_url=excluded.source_url,
                       item_type=excluded.item_type,
                       title=excluded.title,
                       content_type=excluded.content_type,
                       sha256=excluded.sha256,
                       fields_json=excluded.fields_json,
                       metadata_json=excluded.metadata_json,
                       text_excerpt=excluded.text_excerpt,
                       raw_path=excluded.raw_path,
                       document_id=COALESCE(excluded.document_id,source_extracted_items.document_id),
                       last_seen_at=excluded.last_seen_at,
                       changed_at=CASE
                         WHEN source_extracted_items.sha256<>excluded.sha256 THEN excluded.changed_at
                         ELSE source_extracted_items.changed_at
                       END""",
                (
                    source_key, canonical, url, item_type, title, content_type, sha256,
                    json.dumps(fields, ensure_ascii=False),
                    json.dumps(metadata, ensure_ascii=False),
                    text_excerpt, raw_path, document_id,
                    now, now, now,
                ),
            )
            self.store.conn.commit()
        return changed

    @staticmethod
    def _effective_relevance(metadata: dict[str, Any]) -> dict[str, Any]:
        review = dict(metadata.get("relevance_review") or {})
        automatic = dict(metadata.get("relevance") or {})
        if review.get("status") in {"relevant", "needs_review", "irrelevant"}:
            return {
                "status": review["status"],
                "origin": "human",
                "reasons": [review.get("note") or "人工复核"],
                "operator": review.get("operator", ""),
                "reviewed_at": review.get("reviewed_at", ""),
            }
        return {
            "status": automatic.get("status") or "needs_review",
            "origin": "rule",
            "reasons": automatic.get("reasons") or [],
        }

    def item_detail(self, item_id: int) -> dict[str, Any]:
        with self.store.lock:
            row = self.store.conn.execute(
                "SELECT * FROM source_extracted_items WHERE id=?",
                (int(item_id),),
            ).fetchone()
        if not row:
            raise ValueError("collection item not found")
        item = dict(row)
        item["fields"] = json.loads(item.pop("fields_json") or "{}")
        item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        item["effective_relevance"] = self._effective_relevance(item["metadata"])
        return item

    def list_items(self, *, source_key: str = "", limit: int = 200) -> dict[str, Any]:
        params: list[Any] = []
        where = ""
        if source_key:
            where = "WHERE source_key=?"
            params.append(source_key)
        params.append(max(1, min(int(limit), 2000)))
        with self.store.lock:
            rows = self.store.conn.execute(
                f"SELECT * FROM source_extracted_items {where} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item["fields"] = json.loads(item.pop("fields_json") or "{}")
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            item["effective_relevance"] = self._effective_relevance(item["metadata"])
            items.append(item)
        return {
            "items": items,
            "summary": {
                "items": len(items),
                "details": sum(1 for item in items if item["item_type"] == "detail"),
                "attachments": sum(1 for item in items if item["item_type"] == "attachment"),
                "documents": sum(1 for item in items if item.get("document_id")),
                "relevant": sum(1 for item in items if item["effective_relevance"]["status"] == "relevant"),
                "irrelevant": sum(1 for item in items if item["effective_relevance"]["status"] == "irrelevant"),
                "needs_review": sum(1 for item in items if item["effective_relevance"]["status"] == "needs_review"),
                "reviewed": sum(1 for item in items if item["effective_relevance"]["origin"] == "human"),
            },
        }

    def review_item(
        self,
        item_id: int,
        *,
        status: str,
        operator: str = "",
        note: str = "",
        auto_ingest: bool = False,
    ) -> dict[str, Any]:
        status = str(status or "").strip()
        if status not in {"relevant", "needs_review", "irrelevant"}:
            raise ValueError("unsupported relevance status")
        item = self.item_detail(item_id)
        metadata = dict(item.get("metadata") or {})
        metadata["relevance_review"] = {
            "status": status,
            "operator": str(operator or "").strip(),
            "note": str(note or "").strip(),
            "reviewed_at": _now(),
        }

        document_id = item.get("document_id")
        if (
            status == "relevant"
            and auto_ingest
            and not document_id
            and item.get("item_type") in {"detail", "attachment"}
        ):
            raw_path = Path(str(item.get("raw_path") or ""))
            if not raw_path.is_file():
                raise ValueError("raw collection file not found")
            document_id = ingest_file(self.store, raw_path)

        with self.store.lock:
            self.store.conn.execute(
                "UPDATE source_extracted_items SET metadata_json=?,document_id=COALESCE(?,document_id) WHERE id=?",
                (json.dumps(metadata, ensure_ascii=False), document_id, int(item_id)),
            )
            self.store.conn.commit()
        return self.item_detail(item_id)

    def relevance_overview(self, *, source_key: str = "") -> dict[str, Any]:
        params: list[Any] = []
        where = "WHERE item_type IN ('detail','attachment')"
        if source_key:
            where += " AND source_key=?"
            params.append(source_key)
        with self.store.lock:
            rows = self.store.conn.execute(
                f"SELECT source_key,metadata_json,document_id FROM source_extracted_items {where} ORDER BY source_key,id",
                params,
            ).fetchall()

        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = str(row["source_key"] or "")
            bucket = grouped.setdefault(key, {
                "source_key": key,
                "items": 0,
                "relevant": 0,
                "needs_review": 0,
                "irrelevant": 0,
                "reviewed": 0,
                "documents": 0,
            })
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except (TypeError, ValueError):
                metadata = {}
            effective = self._effective_relevance(metadata)
            bucket["items"] += 1
            bucket[effective["status"]] += 1
            bucket["reviewed"] += 1 if effective["origin"] == "human" else 0
            bucket["documents"] += 1 if row["document_id"] else 0

        items = []
        for bucket in grouped.values():
            total = int(bucket["items"] or 0)
            bucket["relevant_rate"] = round((bucket["relevant"] / total * 100), 1) if total else 0.0
            bucket["review_rate"] = round((bucket["reviewed"] / total * 100), 1) if total else 0.0
            items.append(bucket)
        items.sort(key=lambda item: (-item["needs_review"], -item["irrelevant"], item["source_key"]))
        return {
            "items": items,
            "summary": {
                "sources": len(items),
                "items": sum(item["items"] for item in items),
                "relevant": sum(item["relevant"] for item in items),
                "needs_review": sum(item["needs_review"] for item in items),
                "irrelevant": sum(item["irrelevant"] for item in items),
                "reviewed": sum(item["reviewed"] for item in items),
            },
        }

    def run(
        self,
        source_key: str,
        *,
        max_pages: int | None = None,
        max_items: int | None = None,
        auto_ingest: bool = False,
    ) -> dict[str, Any]:
        source = self.source_registry.by_key(source_key)
        plan_record = self.plan(source_key)
        if not plan_record.get("enabled"):
            raise ValueError("site extraction plan is disabled")
        config = self._validate_config(dict(plan_record.get("config") or {}))
        limits = dict(config.get("limits") or {})
        page_limit = min(int(max_pages or limits["max_pages"]), limits["max_pages"], 100)
        item_limit = min(int(max_items or limits["max_items"]), limits["max_items"], 5000)
        detail_limit = int(limits.get("max_details") or 0)
        attachment_limit = int(limits.get("max_attachments") or 0)
        request_cfg = dict(config.get("request") or {})
        headers = {
            "User-Agent": "KnowledgePlatformDeepCollector/1.0",
            "Accept": "*/*",
            **dict(request_cfg.get("headers") or {}),
        }
        started = _now()
        with self.store.lock:
            cur = self.store.conn.execute(
                """INSERT INTO source_deep_runs(source_key,status,started_at)
                   VALUES(?,?,?)""",
                (source_key, "running", started),
            )
            run_id = int(cur.lastrowid)
            self.store.conn.commit()

        queue: list[tuple[str, str, int, bool, str]] = [
            (canonical_url(url), "listing", 0, True, "")
            for url in config.get("start_urls") or []
        ]
        visited: set[str] = set()
        pages_fetched = 0
        items_discovered = 0
        attachments_discovered = 0
        documents_ingested = 0
        changed_items = 0
        details_queued = 0
        attachments_queued = 0
        pagination_queued = 0
        errors: list[str] = []

        try:
            while queue and pages_fetched < page_limit and items_discovered < item_limit:
                url, item_type, depth, parent_relevant, link_text = queue.pop(0)
                url = canonical_url(url)
                if not url or url in visited:
                    continue
                visited.add(url)
                try:
                    body, status, content_type = self.fetcher(
                        url,
                        headers,
                        int(request_cfg["timeout_seconds"]),
                        int(request_cfg["max_bytes"]),
                    )
                    if status >= 400:
                        raise ValueError(f"HTTP {status}")
                    if not body:
                        raise ValueError("empty response")
                except Exception as exc:
                    errors.append(f"{url}: {exc}")
                    continue

                pages_fetched += 1
                digest = hashlib.sha256(body).hexdigest()
                raw_path = self._save_raw(source_key, url, body, content_type, item_type)
                parsed: ParsedPage | None = None
                fields: dict[str, Any] = {}
                excerpt = ""
                metadata: dict[str, Any] = {"depth": depth, "http_status": status}

                is_binary_attachment = (
                    item_type == "attachment"
                    and (
                        "pdf" in (content_type or "").lower()
                        or Path(urlparse(url).path).suffix.lower() in {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip"}
                    )
                )
                if not is_binary_attachment:
                    try:
                        parsed = self._parse_page(body, content_type, url)
                        fields = self._extract_fields(parsed.text, dict(config.get("fields") or {}))
                        max_excerpt = int((config.get("content") or {}).get("max_excerpt_chars") or 4000)
                        excerpt = parsed.text[:max_excerpt]
                        metadata.update({
                            "headings": parsed.headings[:60],
                            "meta": parsed.meta,
                            "link_count": len(parsed.links),
                        })
                    except Exception as exc:
                        errors.append(f"{url}: parse {exc}")

                title = ""
                if parsed:
                    title = parsed.title or (parsed.headings[0]["text"] if parsed.headings else "")
                if not title:
                    title = Path(urlparse(url).path).name or source.get("source_name") or source_key

                relevance = self._evaluate_relevance(
                    url=url,
                    item_type=item_type,
                    title=title,
                    text=excerpt,
                    fields=fields,
                    config=config,
                    parent_relevant=parent_relevant,
                    link_text=link_text,
                )
                metadata["relevance"] = relevance
                current_relevant = relevance.get("status") == "relevant"

                document_id: int | None = None
                if auto_ingest and (item_type in {"detail", "attachment"}) and current_relevant:
                    try:
                        document_id = ingest_file(self.store, raw_path)
                        documents_ingested += 1
                    except Exception as exc:
                        errors.append(f"{url}: ingest {exc}")

                if self._upsert_item(
                    source_key=source_key,
                    url=url,
                    item_type=item_type,
                    title=title,
                    content_type=content_type,
                    sha256=digest,
                    fields=fields,
                    metadata=metadata,
                    text_excerpt=excerpt,
                    raw_path=str(raw_path),
                    document_id=document_id,
                ):
                    changed_items += 1
                items_discovered += 1

                if not parsed:
                    continue
                links = self._classify_links(current_url=url, links=parsed.links, config=config)
                discovery = dict(config.get("discovery") or {})
                pagination_cfg = dict(config.get("pagination") or {})

                if item_type == "listing":
                    for link in links["pagination"]:
                        if pagination_queued >= int(pagination_cfg.get("max_pages") or page_limit) - 1:
                            break
                        if link["url"] not in visited:
                            queue.append((link["url"], "listing", depth, current_relevant, link.get("text", "")))
                            pagination_queued += 1

                if bool(discovery.get("follow_details", True)):
                    for link in links["detail"]:
                        if details_queued >= detail_limit:
                            break
                        if link["url"] not in visited:
                            queue.append((link["url"], "detail", depth + 1, current_relevant, link.get("text", "")))
                            details_queued += 1

                if bool(discovery.get("fetch_attachments", False)) and config.get("document_policy") != "metadata_only":
                    for link in links["attachment"]:
                        if attachments_queued >= attachment_limit:
                            break
                        if link["url"] not in visited:
                            queue.append((link["url"], "attachment", depth + 1, current_relevant, link.get("text", "")))
                            attachments_queued += 1
                            attachments_discovered += 1

            finished = _now()
            with self.store.lock:
                self.store.conn.execute(
                    """UPDATE source_deep_runs SET
                         status=?,finished_at=?,pages_fetched=?,items_discovered=?,
                         attachments_discovered=?,documents_ingested=?,changed_items=?,error=?
                       WHERE id=?""",
                    (
                        'failed' if not pages_fetched else 'partial' if errors else 'completed',
                        finished, pages_fetched, items_discovered, attachments_discovered,
                        documents_ingested, changed_items, "\n".join(errors[:20]), run_id,
                    ),
                )
                self.store.conn.commit()
            return self.run_detail(run_id)
        except Exception as exc:
            finished = _now()
            with self.store.lock:
                self.store.conn.execute(
                    "UPDATE source_deep_runs SET status='failed',finished_at=?,error=? WHERE id=?",
                    (finished, str(exc), run_id),
                )
                self.store.conn.commit()
            raise

    def run_detail(self, run_id: int) -> dict[str, Any]:
        with self.store.lock:
            row = self.store.conn.execute(
                "SELECT * FROM source_deep_runs WHERE id=?",
                (int(run_id),),
            ).fetchone()
        if not row:
            raise ValueError("deep collection run not found")
        return dict(row)

    def list_runs(self, *, source_key: str = "", limit: int = 100) -> dict[str, Any]:
        params: list[Any] = []
        where = ""
        if source_key:
            where = "WHERE source_key=?"
            params.append(source_key)
        params.append(max(1, min(int(limit), 500)))
        with self.store.lock:
            rows = self.store.conn.execute(
                f"SELECT * FROM source_deep_runs {where} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        items = [dict(row) for row in rows]
        return {
            "items": items,
            "summary": {
                "runs": len(items),
                "success": sum(1 for item in items if item["status"] == "completed"),
                "failed": sum(1 for item in items if item["status"] == "failed"),
                "partial": sum(1 for item in items if item["status"] == "partial"),
                "pages": sum(int(item.get("pages_fetched") or 0) for item in items),
                "items": sum(int(item.get("items_discovered") or 0) for item in items),
                "attachments": sum(int(item.get("attachments_discovered") or 0) for item in items),
            },
        }
