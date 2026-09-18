from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.ontology import ontology_schema
from app.store import KnowledgeStore


TERM_TYPES = {"domain", "node_type", "relation", "record_type", "product_class", "authority", "custom"}
TERM_STATUSES = {"active", "draft", "retired"}
VERSION_STATUSES = {"draft", "active", "retired"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


class OntologyRegistryService:
    """Version and terminology governance for the business ontology.

    The runtime Python schema remains the implementation contract. This registry
    keeps reviewable snapshots and terminology mappings so ontology changes can be
    discussed, audited, activated and later exported without silently rewriting
    formal knowledge.
    """

    def __init__(self, store: KnowledgeStore):
        self.store = store
        self._ensure_schema()
        self._seed_current_schema()
        self._seed_terms()

    def _ensure_schema(self) -> None:
        with self.store.lock:
            self.store.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS ontology_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    version_code TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'draft',
                    change_note TEXT DEFAULT '',
                    schema_json TEXT NOT NULL,
                    created_by TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    activated_at TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_ontology_versions_status
                    ON ontology_versions(status,id DESC);

                CREATE TABLE IF NOT EXISTS ontology_terms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    term_type TEXT NOT NULL,
                    canonical_name TEXT NOT NULL,
                    code TEXT DEFAULT '',
                    language TEXT NOT NULL DEFAULT 'zh-CN',
                    aliases_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'active',
                    source_note TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(term_type,canonical_name,language)
                );
                CREATE INDEX IF NOT EXISTS idx_ontology_terms_type
                    ON ontology_terms(term_type,status,id);
                """
            )
            self.store.conn.commit()

    def _seed_current_schema(self) -> None:
        schema = ontology_schema()
        code = str(schema["version"])
        now = _now()
        with self.store.lock:
            active = self.store.conn.execute(
                "SELECT id FROM ontology_versions WHERE status='active' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            existing = self.store.conn.execute(
                "SELECT id,status FROM ontology_versions WHERE version_code=?", (code,)
            ).fetchone()
            if not existing:
                self.store.conn.execute(
                    """
                    INSERT INTO ontology_versions(
                        version_code,status,change_note,schema_json,created_by,created_at,activated_at
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        code,
                        "active" if not active else "draft",
                        "当前代码本体结构初始化",
                        json.dumps(schema, ensure_ascii=False),
                        "system",
                        now,
                        now if not active else "",
                    ),
                )
            self.store.conn.commit()

    def _seed_terms(self) -> None:
        schema = ontology_schema()
        seeds: list[tuple[str, str, str, str, list[str]]] = []
        for item in schema.get("domains", []):
            seeds.append(("domain", item["label"], item["key"], "zh-CN", [item["key"]]))
        for item in schema.get("node_types", []):
            seeds.append(("node_type", item["label"], item["key"], "zh-CN", [item["key"]]))
        for item in schema.get("relations", []):
            seeds.append(("relation", item["label"], item["key"], "zh-CN", [item["key"]]))
        record_labels = {
            "regulation": "法规",
            "standard": "标准",
            "certification": "认证",
            "requirement": "技术要求",
            "test_item": "检测项目",
        }
        for code, label in record_labels.items():
            seeds.append(("record_type", label, code, "zh-CN", [code]))

        now = _now()
        with self.store.lock:
            for term_type, canonical_name, code, language, aliases in seeds:
                self.store.conn.execute(
                    """
                    INSERT INTO ontology_terms(
                        term_type,canonical_name,code,language,aliases_json,status,
                        source_note,created_at,updated_at
                    ) VALUES(?,?,?,?,?,'active',?,?,?)
                    ON CONFLICT(term_type,canonical_name,language) DO NOTHING
                    """,
                    (
                        term_type,
                        canonical_name,
                        code,
                        language,
                        json.dumps(aliases, ensure_ascii=False),
                        "由当前业务本体初始化",
                        now,
                        now,
                    ),
                )
            self.store.conn.commit()

    def _version_row(self, row) -> dict[str, Any]:
        item = dict(row)
        item["schema"] = json.loads(item.pop("schema_json") or "{}")
        return item

    def _term_row(self, row) -> dict[str, Any]:
        item = dict(row)
        item["aliases"] = json.loads(item.pop("aliases_json") or "[]")
        return item

    def list_versions(self) -> dict[str, Any]:
        with self.store.lock:
            rows = self.store.conn.execute(
                "SELECT * FROM ontology_versions ORDER BY id DESC"
            ).fetchall()
        items = [self._version_row(row) for row in rows]
        active = next((item for item in items if item["status"] == "active"), None)
        return {
            "items": items,
            "summary": {
                "versions": len(items),
                "active_version": active["version_code"] if active else "",
                "drafts": sum(1 for item in items if item["status"] == "draft"),
            },
        }

    def create_version(
        self,
        *,
        version_code: str,
        change_note: str = "",
        created_by: str = "",
        source_version_id: int | None = None,
    ) -> dict[str, Any]:
        code = str(version_code or "").strip()
        if not code:
            raise ValueError("version_code is required")

        with self.store.lock:
            existing = self.store.conn.execute(
                "SELECT id FROM ontology_versions WHERE version_code=?", (code,)
            ).fetchone()
            if existing:
                raise ValueError("ontology version already exists")
            if source_version_id:
                source = self.store.conn.execute(
                    "SELECT schema_json FROM ontology_versions WHERE id=?",
                    (int(source_version_id),),
                ).fetchone()
            else:
                source = self.store.conn.execute(
                    "SELECT schema_json FROM ontology_versions WHERE status='active' ORDER BY id DESC LIMIT 1"
                ).fetchone()
            schema = json.loads(source["schema_json"]) if source else ontology_schema()
            schema["version"] = code
            now = _now()
            cur = self.store.conn.execute(
                """
                INSERT INTO ontology_versions(
                    version_code,status,change_note,schema_json,created_by,created_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    code,
                    "draft",
                    change_note,
                    json.dumps(schema, ensure_ascii=False),
                    created_by,
                    now,
                ),
            )
            self.store.conn.commit()
            version_id = int(cur.lastrowid)
            row = self.store.conn.execute(
                "SELECT * FROM ontology_versions WHERE id=?", (version_id,)
            ).fetchone()
        return self._version_row(row)

    def activate_version(self, version_id: int, *, operator: str = "") -> dict[str, Any]:
        now = _now()
        with self.store.lock:
            row = self.store.conn.execute(
                "SELECT * FROM ontology_versions WHERE id=?", (int(version_id),)
            ).fetchone()
            if not row:
                raise ValueError("ontology version not found")
            if row["status"] == "retired":
                raise ValueError("retired ontology version cannot be activated")
            self.store.conn.execute(
                "UPDATE ontology_versions SET status='retired' WHERE status='active' AND id<>?",
                (int(version_id),),
            )
            self.store.conn.execute(
                """
                UPDATE ontology_versions SET
                    status='active',activated_at=?,
                    change_note=CASE WHEN ?<>'' THEN change_note || CASE WHEN change_note<>'' THEN '；' ELSE '' END || ? ELSE change_note END
                WHERE id=?
                """,
                (now, operator, f"启用人：{operator}" if operator else "", int(version_id)),
            )
            self.store.conn.commit()
            current = self.store.conn.execute(
                "SELECT * FROM ontology_versions WHERE id=?", (int(version_id),)
            ).fetchone()
        return self._version_row(current)

    def list_terms(
        self,
        *,
        term_type: str = "",
        status: str = "",
        q: str = "",
        limit: int = 500,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        if term_type:
            clauses.append("term_type=?")
            params.append(term_type)
        if status:
            clauses.append("status=?")
            params.append(status)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(min(max(int(limit), 1), 2000))
        with self.store.lock:
            rows = self.store.conn.execute(
                f"SELECT * FROM ontology_terms {where} ORDER BY term_type,canonical_name LIMIT ?",
                params,
            ).fetchall()
        items = [self._term_row(row) for row in rows]
        if q:
            needle = _norm(q)
            items = [
                item for item in items
                if needle in _norm(item["canonical_name"])
                or needle in _norm(item.get("code"))
                or any(needle in _norm(alias) for alias in item["aliases"])
            ]
        return {
            "items": items,
            "summary": {
                "terms": len(items),
                "types": sorted({item["term_type"] for item in items}),
                "active": sum(1 for item in items if item["status"] == "active"),
            },
        }

    def upsert_term(self, payload: dict[str, Any]) -> dict[str, Any]:
        term_type = str(payload.get("term_type") or "custom").strip()
        canonical_name = str(payload.get("canonical_name") or "").strip()
        language = str(payload.get("language") or "zh-CN").strip()
        code = str(payload.get("code") or "").strip()
        status = str(payload.get("status") or "active").strip()
        source_note = str(payload.get("source_note") or "").strip()
        aliases = payload.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [part.strip() for part in re_split_aliases(aliases) if part.strip()]
        aliases = sorted({str(alias).strip() for alias in aliases if str(alias).strip()})
        if term_type not in TERM_TYPES:
            raise ValueError(f"unsupported term_type: {term_type}")
        if not canonical_name:
            raise ValueError("canonical_name is required")
        if status not in TERM_STATUSES:
            raise ValueError(f"unsupported term status: {status}")
        aliases = [alias for alias in aliases if _norm(alias) != _norm(canonical_name)]
        now = _now()
        with self.store.lock:
            self.store.conn.execute(
                """
                INSERT INTO ontology_terms(
                    term_type,canonical_name,code,language,aliases_json,status,
                    source_note,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(term_type,canonical_name,language) DO UPDATE SET
                    code=excluded.code,
                    aliases_json=excluded.aliases_json,
                    status=excluded.status,
                    source_note=excluded.source_note,
                    updated_at=excluded.updated_at
                """,
                (
                    term_type,
                    canonical_name,
                    code,
                    language,
                    json.dumps(aliases, ensure_ascii=False),
                    status,
                    source_note,
                    now,
                    now,
                ),
            )
            self.store.conn.commit()
            row = self.store.conn.execute(
                """
                SELECT * FROM ontology_terms
                WHERE term_type=? AND canonical_name=? AND language=?
                """,
                (term_type, canonical_name, language),
            ).fetchone()
        return self._term_row(row)

    def normalize(self, value: str, *, term_type: str = "") -> dict[str, Any]:
        needle = _norm(value)
        if not needle:
            return {"query": value, "matches": []}
        listing = self.list_terms(term_type=term_type, status="active", limit=2000)
        matches: list[dict[str, Any]] = []
        for item in listing["items"]:
            canonical = _norm(item["canonical_name"])
            aliases = {_norm(alias) for alias in item["aliases"]}
            code = _norm(item.get("code"))
            if needle == canonical or needle == code or needle in aliases:
                score = 1.0
                match_type = "exact"
            elif needle in canonical or canonical in needle:
                score = 0.8
                match_type = "contains"
            else:
                alias_hits = [alias for alias in aliases if needle in alias or alias in needle]
                if not alias_hits:
                    continue
                score = 0.75
                match_type = "alias_contains"
            matches.append({
                "id": item["id"],
                "term_type": item["term_type"],
                "canonical_name": item["canonical_name"],
                "code": item.get("code", ""),
                "language": item["language"],
                "matched_as": match_type,
                "score": score,
            })
        matches.sort(key=lambda item: (-item["score"], item["canonical_name"]))
        return {"query": value, "term_type": term_type, "matches": matches[:20]}


def re_split_aliases(value: str) -> list[str]:
    import re
    return re.split(r"[,，;；\n]+", value)
