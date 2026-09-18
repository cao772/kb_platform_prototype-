from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.request import Request, urlopen

from app.ingest import ingest_file
from app.source_registry import SourceRegistryService
from app.structured_extraction import extract_review_candidates_v2
from app.store import KnowledgeStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class CollectionProfileSeed:
    profile_key: str
    source_key: str
    adapter: str
    entry_url: str
    output_suffix: str
    purpose: str
    enabled: bool = True
    timeout_seconds: int = 30
    max_bytes: int = 25 * 1024 * 1024
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


PROFILE_SEEDS: tuple[CollectionProfileSeed, ...] = (
    CollectionProfileSeed(
        "EU-LVD-HTML",
        "EU-EURLEX",
        "html",
        "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32014L0035",
        ".html",
        "欧盟低电压指令样板页面，验证HTML法规正文采集。",
        notes="CELEX 32014L0035；作为欧盟法规HTML样板，不代表最终产品范围。",
    ),
    CollectionProfileSeed(
        "EU-LVD-PDF",
        "EU-EURLEX",
        "pdf",
        "https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX:32014L0035",
        ".pdf",
        "欧盟低电压指令PDF样板，验证PDF下载、解析和证据切分。",
        notes="与HTML样板对应，用于验证同一法规多格式归一。",
    ),
    CollectionProfileSeed(
        "DE-PRODSG-HTML",
        "DE-LAW",
        "html",
        "https://www.gesetze-im-internet.de/prodsg_2021/",
        ".html",
        "德国国家法规样板，验证本国HTML法规采集。",
        notes="德国本地法规与EU共享法规后续通过地区适用关系组合。",
    ),
    CollectionProfileSeed(
        "US-FEDREGISTER-API",
        "US-FEDREG",
        "json_api",
        "https://www.federalregister.gov/api/v1/documents.json?per_page=20&order=newest",
        ".json",
        "美国Federal Register API样板，验证JSON API采集。",
        notes="第一阶段仅作为增量发现入口；进入正式知识前仍需按产品和监管机构过滤。",
    ),
    CollectionProfileSeed(
        "JP-EGOV-LAWLIST-XML",
        "JP-LAW",
        "xml_api",
        "https://elaws.e-gov.go.jp/api/1/lawlists/3",
        ".xml",
        "日本e-Gov法令API样板，验证XML API采集。",
        notes="法令类型3列表样板；后续通过法令ID继续拉取正文和版本。",
    ),
)

Fetcher = Callable[[str, dict[str, str], int, int], tuple[bytes, int, str]]


def _default_fetcher(url: str, headers: dict[str, str], timeout: int, max_bytes: int) -> tuple[bytes, int, str]:
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=timeout) as response:
        status = int(getattr(response, "status", 200) or 200)
        content_type = str(response.headers.get("Content-Type") or "")
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"download exceeds max_bytes: {max_bytes}")
    return data, status, content_type


class SourceCollectionService:
    def __init__(
        self,
        store: KnowledgeStore,
        source_registry: SourceRegistryService,
        download_dir: str | Path,
        *,
        change_monitor=None,
        fetcher: Fetcher | None = None,
    ):
        self.store = store
        self.source_registry = source_registry
        self.download_dir = Path(download_dir)
        self.change_monitor = change_monitor
        self.fetcher = fetcher or _default_fetcher
        self._ensure_schema()
        self._seed_profiles()

    def _ensure_schema(self) -> None:
        with self.store.lock:
            self.store.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS collection_profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_key TEXT NOT NULL UNIQUE,
                    source_key TEXT NOT NULL,
                    adapter TEXT NOT NULL,
                    entry_url TEXT NOT NULL,
                    output_suffix TEXT NOT NULL,
                    purpose TEXT DEFAULT '',
                    enabled INTEGER DEFAULT 1,
                    timeout_seconds INTEGER DEFAULT 30,
                    max_bytes INTEGER DEFAULT 26214400,
                    headers_json TEXT DEFAULT '{}',
                    notes TEXT DEFAULT '',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_collection_profiles_source
                    ON collection_profiles(source_key);

                CREATE TABLE IF NOT EXISTS collection_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id INTEGER NOT NULL,
                    profile_key TEXT NOT NULL,
                    source_id INTEGER,
                    source_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT DEFAULT '',
                    http_status INTEGER,
                    content_type TEXT DEFAULT '',
                    bytes_received INTEGER DEFAULT 0,
                    sha256 TEXT DEFAULT '',
                    saved_path TEXT DEFAULT '',
                    document_id INTEGER,
                    candidate_count INTEGER DEFAULT 0,
                    change_watch_count INTEGER DEFAULT 0,
                    error TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_collection_runs_profile
                    ON collection_runs(profile_id,id DESC);
                """
            )
            self.store.conn.commit()

    def _seed_profiles(self) -> None:
        with self.store.lock:
            for seed in PROFILE_SEEDS:
                item = seed.to_dict()
                self.store.conn.execute(
                    """
                    INSERT INTO collection_profiles(
                        profile_key,source_key,adapter,entry_url,output_suffix,purpose,
                        enabled,timeout_seconds,max_bytes,notes
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(profile_key) DO NOTHING
                    """,
                    (
                        item["profile_key"], item["source_key"], item["adapter"], item["entry_url"],
                        item["output_suffix"], item["purpose"], 1 if item["enabled"] else 0,
                        item["timeout_seconds"], item["max_bytes"], item["notes"],
                    ),
                )
            self.store.conn.commit()

    def _profile_row(self, row) -> dict[str, Any]:
        item = dict(row)
        item["enabled"] = bool(item.get("enabled"))
        item["headers"] = json.loads(item.pop("headers_json") or "{}")
        return item

    def list_profiles(self, *, source_key: str = "", enabled_only: bool = False) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        if source_key:
            clauses.append("source_key=?")
            params.append(source_key)
        if enabled_only:
            clauses.append("enabled=1")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self.store.lock:
            rows = self.store.conn.execute(
                f"SELECT * FROM collection_profiles {where} ORDER BY source_key,profile_key",
                params,
            ).fetchall()
        items = [self._profile_row(row) for row in rows]
        return {
            "items": items,
            "summary": {
                "profiles": len(items),
                "enabled": sum(1 for item in items if item["enabled"]),
                "adapters": sorted({item["adapter"] for item in items}),
                "sources": len({item["source_key"] for item in items}),
            },
        }

    def profile(self, profile_id: int) -> dict[str, Any]:
        with self.store.lock:
            row = self.store.conn.execute("SELECT * FROM collection_profiles WHERE id=?", (int(profile_id),)).fetchone()
        if not row:
            raise ValueError("collection profile not found")
        return self._profile_row(row)

    def list_runs(self, *, limit: int = 100) -> dict[str, Any]:
        with self.store.lock:
            rows = self.store.conn.execute(
                "SELECT * FROM collection_runs ORDER BY id DESC LIMIT ?",
                (min(max(int(limit), 1), 500),),
            ).fetchall()
        items = [dict(row) for row in rows]
        return {
            "items": items,
            "summary": {
                "runs": len(items),
                "success": sum(1 for item in items if item.get("status") == "completed"),
                "failed": sum(1 for item in items if item.get("status") == "failed"),
            },
        }

    def _source_for_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        listing = self.source_registry.list_sources(q=profile["source_key"], limit=1000)
        for item in listing["items"]:
            if item.get("source_key") == profile["source_key"]:
                return item
        raise ValueError(f"source not found for profile: {profile['source_key']}")

    def run(
        self,
        profile_id: int,
        *,
        auto_ingest: bool = True,
        auto_extract: bool = False,
        use_model: bool = False,
    ) -> dict[str, Any]:
        profile = self.profile(profile_id)
        if not profile["enabled"]:
            raise ValueError("collection profile is disabled")
        source = self._source_for_profile(profile)
        started_at = _now()
        with self.store.lock:
            cur = self.store.conn.execute(
                """
                INSERT INTO collection_runs(
                    profile_id,profile_key,source_id,source_key,status,started_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (profile["id"], profile["profile_key"], source["id"], source["source_key"], "running", started_at),
            )
            run_id = int(cur.lastrowid)
            self.store.conn.commit()

        try:
            headers = {
                "User-Agent": "KnowledgePlatformSourceCollector/1.0",
                "Accept": "*/*",
                **dict(profile.get("headers") or {}),
            }
            body, http_status, content_type = self.fetcher(
                profile["entry_url"],
                headers,
                int(profile["timeout_seconds"]),
                int(profile["max_bytes"]),
            )
            if not body:
                raise ValueError("source returned empty body")
            if http_status >= 400:
                raise ValueError(f"source returned HTTP {http_status}")

            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            safe_key = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in profile["profile_key"])
            folder = self.download_dir / source["source_key"]
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f"{stamp}_{safe_key}{profile['output_suffix']}"
            temp = target.with_suffix(target.suffix + ".downloading")
            temp.write_bytes(body)
            temp.replace(target)

            sha256 = hashlib.sha256(body).hexdigest()
            document_id: int | None = None
            candidate_count = 0
            change_watch_count = 0
            if auto_ingest:
                document_id = ingest_file(self.store, target)
                if auto_extract:
                    extraction = extract_review_candidates_v2(
                        self.store,
                        int(document_id),
                        use_llm=bool(use_model),
                    )
                    candidate_count = int(extraction.get("total_created") or 0)
                    if self.change_monitor is not None:
                        change = self.change_monitor.scan(document_id=int(document_id))
                        change_watch_count = int(change.get("created") or 0)

            finished_at = _now()
            with self.store.lock:
                self.store.conn.execute(
                    """
                    UPDATE collection_runs SET
                        status='completed',finished_at=?,http_status=?,content_type=?,
                        bytes_received=?,sha256=?,saved_path=?,document_id=?,
                        candidate_count=?,change_watch_count=?
                    WHERE id=?
                    """,
                    (
                        finished_at, http_status, content_type, len(body), sha256, str(target.resolve()),
                        document_id, candidate_count, change_watch_count, run_id,
                    ),
                )
                self.store.conn.execute(
                    """
                    UPDATE knowledge_sources SET
                        status='connected',last_checked_at=?,last_success_at=?,updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (finished_at, finished_at, source["id"]),
                )
                self.store.conn.commit()
            return self.run_detail(run_id)
        except Exception as exc:
            finished_at = _now()
            with self.store.lock:
                self.store.conn.execute(
                    "UPDATE collection_runs SET status='failed',finished_at=?,error=? WHERE id=?",
                    (finished_at, str(exc), run_id),
                )
                self.store.conn.execute(
                    "UPDATE knowledge_sources SET last_checked_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    (finished_at, source["id"]),
                )
                self.store.conn.commit()
            return self.run_detail(run_id)

    def run_detail(self, run_id: int) -> dict[str, Any]:
        with self.store.lock:
            row = self.store.conn.execute("SELECT * FROM collection_runs WHERE id=?", (int(run_id),)).fetchone()
        if not row:
            raise ValueError("collection run not found")
        return dict(row)
