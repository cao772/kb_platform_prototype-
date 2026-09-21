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

FIRST_WAVE_PATH = Path(__file__).resolve().parent.parent / "data" / "source_collection_first50.json"


def _load_first_wave() -> list[dict[str, Any]]:
    if not FIRST_WAVE_PATH.exists():
        return []
    payload = json.loads(FIRST_WAVE_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("first-wave collection catalog must be a list")
    items = [dict(item) for item in payload if isinstance(item, dict)]
    if len(items) != 50:
        raise ValueError(f"first-wave collection catalog must contain 50 sources, got {len(items)}")
    return items


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
        self._seed_first_wave_profiles()

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

                CREATE TABLE IF NOT EXISTS source_snapshots (
                    profile_id INTEGER PRIMARY KEY,
                    profile_key TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    bytes_received INTEGER DEFAULT 0,
                    content_type TEXT DEFAULT '',
                    saved_path TEXT DEFAULT '',
                    document_id INTEGER,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    changed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS source_update_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id INTEGER NOT NULL,
                    profile_key TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    run_id INTEGER NOT NULL,
                    change_type TEXT NOT NULL,
                    previous_sha256 TEXT DEFAULT '',
                    current_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_source_update_events_profile
                    ON source_update_events(profile_id,id DESC);
                CREATE INDEX IF NOT EXISTS idx_source_update_events_status
                    ON source_update_events(status,id DESC);
                """
            )
            columns = {
                row["name"]
                for row in self.store.conn.execute("PRAGMA table_info(collection_runs)").fetchall()
            }
            if "content_status" not in columns:
                self.store.conn.execute(
                    "ALTER TABLE collection_runs ADD COLUMN content_status TEXT DEFAULT ''"
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

    def _seed_first_wave_profiles(self) -> None:
        wave = _load_first_wave()
        if not wave:
            return
        with self.store.lock:
            for entry in wave:
                source_key = str(entry.get("source_key") or "").strip()
                if not source_key:
                    continue
                existing = self.store.conn.execute(
                    "SELECT id FROM collection_profiles WHERE source_key=? LIMIT 1",
                    (source_key,),
                ).fetchone()
                if existing:
                    continue
                source_row = self.store.conn.execute(
                    "SELECT * FROM knowledge_sources WHERE source_key=?",
                    (source_key,),
                ).fetchone()
                if not source_row:
                    continue
                source = dict(source_row)
                profile_key = f"FW-{source_key}-ENTRY"
                purpose = str(entry.get("purpose") or "").strip() or "首批50站入口资料采集"
                entry_url = str(source.get("base_url") or "").strip()
                if not entry_url.startswith(("http://", "https://")):
                    continue
                notes = (
                    "首批50站入口级采集配置。用于保存官方入口页面或公开目录的原始证据；"
                    "复杂站点的列表翻页、详情页和附件发现后续按站点适配，不把首页采集等同于全站采集。"
                )
                self.store.conn.execute(
                    """INSERT INTO collection_profiles(
                           profile_key,source_key,adapter,entry_url,output_suffix,purpose,
                           enabled,timeout_seconds,max_bytes,headers_json,notes
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(profile_key) DO NOTHING""",
                    (
                        profile_key, source_key, "html", entry_url, ".html", purpose,
                        1, 30, 25 * 1024 * 1024, "{}", notes,
                    ),
                )
            self.store.conn.commit()

    def first_wave_profiles(self) -> dict[str, Any]:
        wave = _load_first_wave()
        requested_keys = [str(item.get("source_key") or "") for item in wave]
        profiles = self.list_profiles()["items"]
        grouped: dict[str, list[dict[str, Any]]] = {}
        for profile in profiles:
            grouped.setdefault(str(profile.get("source_key") or ""), []).append(profile)

        preferred_adapter_order = {"json_api": 0, "xml_api": 1, "pdf": 2, "html": 3}
        items: list[dict[str, Any]] = []
        for entry in wave:
            source_key = str(entry.get("source_key") or "")
            source = self.source_registry.by_key(source_key)
            candidates = grouped.get(source_key, [])
            candidates.sort(key=lambda item: (
                preferred_adapter_order.get(str(item.get("adapter") or ""), 9),
                str(item.get("profile_key") or ""),
            ))
            profile = candidates[0] if candidates else {}
            items.append({
                "order": int(entry.get("order") or 0),
                "source_key": source_key,
                "region_code": source.get("region_code", ""),
                "source_name": source.get("source_name", ""),
                "source_type": source.get("source_type", ""),
                "harvestability": source.get("harvestability", ""),
                "purpose": entry.get("purpose", ""),
                "profile": profile,
                "profile_ready": bool(profile),
            })

        with self.store.lock:
            run_rows = self.store.conn.execute(
                """SELECT source_key,
                          SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) success_count,
                          SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) failed_count,
                          MAX(finished_at) last_finished_at
                   FROM collection_runs
                   GROUP BY source_key"""
            ).fetchall()
        run_map = {str(row["source_key"]): dict(row) for row in run_rows}
        for item in items:
            run = run_map.get(item["source_key"], {})
            item["success_count"] = int(run.get("success_count") or 0)
            item["failed_count"] = int(run.get("failed_count") or 0)
            item["last_finished_at"] = str(run.get("last_finished_at") or "")

        return {
            "items": items,
            "summary": {
                "target_sources": len(requested_keys),
                "profile_ready": sum(1 for item in items if item["profile_ready"]),
                "regions": len({item["region_code"] for item in items if item["region_code"] and item["region_code"] != "EU"}),
                "completed_sources": sum(1 for item in items if item["success_count"] > 0),
                "failed_sources": sum(1 for item in items if item["failed_count"] > 0 and item["success_count"] == 0),
            },
        }

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

    def create_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        profile_key = str(payload.get("profile_key") or "").strip()
        source_key = str(payload.get("source_key") or "").strip()
        adapter = str(payload.get("adapter") or "").strip()
        entry_url = str(payload.get("entry_url") or "").strip()
        purpose = str(payload.get("purpose") or "").strip()
        notes = str(payload.get("notes") or "").strip()
        if not profile_key:
            raise ValueError("profile_key is required")
        if not source_key:
            raise ValueError("source_key is required")
        source = self.source_registry.by_key(source_key)
        if source.get("status") == "archived":
            raise ValueError("archived source cannot create collection profile")
        suffix_map = {"html": ".html", "pdf": ".pdf", "json_api": ".json", "xml_api": ".xml"}
        if adapter not in suffix_map:
            raise ValueError(f"unsupported adapter: {adapter}")
        if not entry_url:
            entry_url = str(source.get("base_url") or "")
        if not (entry_url.startswith("http://") or entry_url.startswith("https://")):
            raise ValueError("entry_url must start with http:// or https://")
        enabled = bool(payload.get("enabled", True))
        timeout_seconds = max(1, min(int(payload.get("timeout_seconds") or 30), 120))
        max_bytes = max(1024, min(int(payload.get("max_bytes") or 25 * 1024 * 1024), 100 * 1024 * 1024))
        headers = payload.get("headers") or {}
        if not isinstance(headers, dict):
            raise ValueError("headers must be an object")
        with self.store.lock:
            existing = self.store.conn.execute(
                "SELECT id FROM collection_profiles WHERE profile_key=?",
                (profile_key,),
            ).fetchone()
            if existing:
                raise ValueError(f"profile_key already exists: {profile_key}")
            cur = self.store.conn.execute(
                """INSERT INTO collection_profiles(
                       profile_key,source_key,adapter,entry_url,output_suffix,purpose,
                       enabled,timeout_seconds,max_bytes,headers_json,notes
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    profile_key, source_key, adapter, entry_url, suffix_map[adapter], purpose,
                    1 if enabled else 0, timeout_seconds, max_bytes,
                    json.dumps(headers, ensure_ascii=False), notes,
                ),
            )
            self.store.conn.commit()
            profile_id = int(cur.lastrowid)
        return self.profile(profile_id)

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
                "changed": sum(1 for item in items if item.get("content_status") == "changed"),
                "initial": sum(1 for item in items if item.get("content_status") == "initial"),
                "unchanged": sum(1 for item in items if item.get("content_status") == "unchanged"),
            },
        }

    def list_updates(self, *, status: str = "", limit: int = 100) -> dict[str, Any]:
        params: list[Any] = []
        where = ""
        if status:
            where = "WHERE status=?"
            params.append(status)
        params.append(min(max(int(limit), 1), 500))
        with self.store.lock:
            rows = self.store.conn.execute(
                f"SELECT * FROM source_update_events {where} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        items = [dict(row) for row in rows]
        return {
            "items": items,
            "summary": {
                "events": len(items),
                "open": sum(1 for item in items if item.get("status") == "open"),
                "initial": sum(1 for item in items if item.get("change_type") == "initial"),
                "changed": sum(1 for item in items if item.get("change_type") == "changed"),
            },
        }

    def _source_for_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        return self.source_registry.by_key(profile["source_key"])

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

            sha256 = hashlib.sha256(body).hexdigest()
            with self.store.lock:
                previous = self.store.conn.execute(
                    "SELECT * FROM source_snapshots WHERE profile_id=?",
                    (int(profile["id"]),),
                ).fetchone()

            if previous and str(previous["sha256"] or "") == sha256:
                finished_at = _now()
                with self.store.lock:
                    self.store.conn.execute(
                        """
                        UPDATE collection_runs SET
                            status='completed',finished_at=?,http_status=?,content_type=?,
                            bytes_received=?,sha256=?,content_status='unchanged'
                        WHERE id=?
                        """,
                        (finished_at, http_status, content_type, len(body), sha256, run_id),
                    )
                    self.store.conn.execute(
                        "UPDATE source_snapshots SET last_seen_at=? WHERE profile_id=?",
                        (finished_at, int(profile["id"])),
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

            content_status = "changed" if previous else "initial"
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            safe_key = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in profile["profile_key"])
            folder = self.download_dir / source["source_key"]
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f"{stamp}_{safe_key}_{sha256[:12]}{profile['output_suffix']}"
            temp = target.with_suffix(target.suffix + ".downloading")
            temp.write_bytes(body)
            temp.replace(target)

            document_id: int | None = None
            candidate_count = 0
            change_watch_count = 0
            if auto_ingest:
                document_id = ingest_file(self.store, target)
                with self.store.lock:
                    doc_row = self.store.conn.execute(
                        "SELECT metadata FROM documents WHERE id=?", (int(document_id),)
                    ).fetchone()
                    metadata = json.loads(doc_row["metadata"] or "{}") if doc_row else {}
                    metadata.update({
                        "source_registry_id": source["id"],
                        "source_key": source["source_key"],
                        "source_name": source["source_name"],
                        "source_type": source["source_type"],
                        "region_code": source["region_code"],
                        "authority": source.get("authority", ""),
                        "source_url": profile["entry_url"],
                        "collection_profile": profile["profile_key"],
                        "collection_run_id": run_id,
                        "collected_at": _now(),
                        "original_content_preserved": True,
                        "original_file_path": str(target.resolve()),
                        "translation_policy": "source_original_plus_zh-CN",
                    })
                    self.store.conn.execute(
                        "UPDATE documents SET metadata=? WHERE id=?",
                        (json.dumps(metadata, ensure_ascii=False), int(document_id)),
                    )
                    chunk_rows = self.store.conn.execute(
                        "SELECT id,metadata FROM chunks WHERE document_id=?", (int(document_id),)
                    ).fetchall()
                    for chunk_row in chunk_rows:
                        chunk_metadata = json.loads(chunk_row["metadata"] or "{}")
                        chunk_metadata.update({
                            "source_key": source["source_key"],
                            "region_code": source["region_code"],
                            "source_url": profile["entry_url"],
                            "collection_run_id": run_id,
                        })
                        self.store.conn.execute(
                            "UPDATE chunks SET metadata=? WHERE id=?",
                            (json.dumps(chunk_metadata, ensure_ascii=False), int(chunk_row["id"])),
                        )
                    self.store.conn.commit()
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
                        candidate_count=?,change_watch_count=?,content_status=?
                    WHERE id=?
                    """,
                    (
                        finished_at, http_status, content_type, len(body), sha256, str(target.resolve()),
                        document_id, candidate_count, change_watch_count, content_status, run_id,
                    ),
                )
                previous_sha = str(previous["sha256"] or "") if previous else ""
                self.store.conn.execute(
                    """
                    INSERT INTO source_snapshots(
                        profile_id,profile_key,source_key,sha256,bytes_received,content_type,
                        saved_path,document_id,first_seen_at,last_seen_at,changed_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(profile_id) DO UPDATE SET
                        sha256=excluded.sha256,
                        bytes_received=excluded.bytes_received,
                        content_type=excluded.content_type,
                        saved_path=excluded.saved_path,
                        document_id=excluded.document_id,
                        last_seen_at=excluded.last_seen_at,
                        changed_at=excluded.changed_at
                    """,
                    (
                        int(profile["id"]), profile["profile_key"], source["source_key"], sha256,
                        len(body), content_type, str(target.resolve()), document_id,
                        finished_at if not previous else str(previous["first_seen_at"]),
                        finished_at, finished_at,
                    ),
                )
                self.store.conn.execute(
                    """
                    INSERT INTO source_update_events(
                        profile_id,profile_key,source_key,run_id,change_type,
                        previous_sha256,current_sha256,status,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        int(profile["id"]), profile["profile_key"], source["source_key"], run_id,
                        content_status, previous_sha, sha256, "open", finished_at,
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
