from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from app.model_gateway import call_translation_model
from app.store import KnowledgeStore


TRANSLATABLE_FIELDS = (
    "name",
    "authority",
    "applicability_scope",
    "exceptions",
    "transition_note",
    "legal_effect",
    "review_basis",
    "evidence_excerpt",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _looks_chinese(text: str) -> bool:
    value = str(text or "")
    chinese = len(re.findall(r"[\u4e00-\u9fff]", value))
    visible = max(1, len(re.sub(r"\s+", "", value)))
    return chinese >= 2 and chinese / visible >= 0.15


def _sha(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


class FormalKnowledgeTranslationService:
    """Bilingual derivative layer for approved formal knowledge.

    Formal records and evidence remain unchanged. Translation fields are stored
    separately and can be model-generated, manually edited and human-confirmed.
    """

    def __init__(self, store: KnowledgeStore):
        self.store = store
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.store.lock:
            self.store.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS formal_knowledge_translations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_id INTEGER NOT NULL,
                    target_language TEXT NOT NULL DEFAULT 'zh-CN',
                    translations_json TEXT NOT NULL DEFAULT '{}',
                    source_hashes_json TEXT NOT NULL DEFAULT '{}',
                    translation_mode TEXT DEFAULT '',
                    model_trace_json TEXT NOT NULL DEFAULT '{}',
                    review_status TEXT NOT NULL DEFAULT 'draft',
                    operator TEXT DEFAULT '',
                    note TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    confirmed_at TEXT DEFAULT '',
                    UNIQUE(record_id,target_language)
                );
                CREATE INDEX IF NOT EXISTS idx_formal_translation_status
                    ON formal_knowledge_translations(review_status,id DESC);
                """
            )
            self.store.conn.commit()

    def _record(self, record_id: int) -> dict[str, Any]:
        with self.store.lock:
            row = self.store.conn.execute(
                """
                SELECT * FROM compliance_records
                WHERE id=? AND review_status='approved'
                """,
                (int(record_id),),
            ).fetchone()
        if not row:
            raise ValueError("approved formal knowledge record not found")
        item = dict(row)
        item["attributes"] = json.loads(item.get("attributes") or "{}")
        if item.get("source_chunk_id"):
            with self.store.lock:
                chunk = self.store.conn.execute(
                    "SELECT text FROM chunks WHERE id=?",
                    (int(item["source_chunk_id"]),),
                ).fetchone()
            item["evidence_excerpt"] = str(chunk["text"] if chunk else "")[:4000]
        else:
            item["evidence_excerpt"] = ""
        return item

    def _source_fields(self, record: dict[str, Any]) -> dict[str, str]:
        attrs = dict(record.get("attributes") or {})
        return {
            "name": str(record.get("name") or ""),
            "authority": str(attrs.get("authority") or ""),
            "applicability_scope": str(attrs.get("applicability_scope") or ""),
            "exceptions": str(attrs.get("exceptions") or ""),
            "transition_note": str(attrs.get("transition_note") or ""),
            "legal_effect": str(attrs.get("legal_effect") or ""),
            "review_basis": str(attrs.get("review_basis") or ""),
            "evidence_excerpt": str(record.get("evidence_excerpt") or ""),
        }

    def detail(self, record_id: int, *, target_language: str = "zh-CN") -> dict[str, Any]:
        record = self._record(record_id)
        source_fields = self._source_fields(record)
        current_hashes = {key: _sha(value) for key, value in source_fields.items()}
        with self.store.lock:
            row = self.store.conn.execute(
                """
                SELECT * FROM formal_knowledge_translations
                WHERE record_id=? AND target_language=?
                """,
                (int(record_id), target_language),
            ).fetchone()
        if row:
            saved = dict(row)
            translations = json.loads(saved.pop("translations_json") or "{}")
            saved_hashes = json.loads(saved.pop("source_hashes_json") or "{}")
            trace = json.loads(saved.pop("model_trace_json") or "{}")
            review_status = saved.get("review_status") or "draft"
            operator = saved.get("operator") or ""
            note = saved.get("note") or ""
            confirmed_at = saved.get("confirmed_at") or ""
        else:
            translations = {}
            saved_hashes = {}
            trace = {}
            review_status = "draft"
            operator = ""
            note = ""
            confirmed_at = ""

        fields: dict[str, Any] = {}
        for key in TRANSLATABLE_FIELDS:
            source = source_fields.get(key, "")
            translated = str(translations.get(key) or "")
            if not translated and _looks_chinese(source):
                translated = source
            fields[key] = {
                "source": source,
                "translated": translated,
                "source_changed": bool(saved_hashes and saved_hashes.get(key) != current_hashes[key]),
            }

        return {
            "record_id": int(record_id),
            "target_language": target_language,
            "source_preserved": True,
            "record": {
                "record_type": record.get("record_type", ""),
                "code": record.get("code", ""),
                "name": record.get("name", ""),
                "version": record.get("version", ""),
                "region_code": record.get("region_code", ""),
                "region_name": record.get("region_name", ""),
                "product_class": record.get("product_class", ""),
                "source_document_id": record.get("source_document_id"),
                "source_chunk_id": record.get("source_chunk_id"),
            },
            "fields": fields,
            "review": {
                "review_status": review_status,
                "operator": operator,
                "note": note,
                "confirmed_at": confirmed_at,
            },
            "translation": trace,
            "summary": {
                "fields": len(TRANSLATABLE_FIELDS),
                "translated": sum(1 for value in fields.values() if value["translated"]),
                "pending": sum(1 for value in fields.values() if value["source"] and not value["translated"]),
                "source_changed": sum(1 for value in fields.values() if value["source_changed"]),
            },
            "notice": "正式知识原字段和原始依据保持不变；中文翻译独立保存，可人工修订和确认。",
        }

    def translate(
        self,
        record_id: int,
        *,
        target_language: str = "zh-CN",
        force: bool = False,
        translator=None,
    ) -> dict[str, Any]:
        detail = self.detail(record_id, target_language=target_language)
        fields = detail["fields"]
        source_fields = {key: value["source"] for key, value in fields.items()}
        existing = {key: value["translated"] for key, value in fields.items()}
        requests: list[str] = []
        keys: list[str] = []

        for key in TRANSLATABLE_FIELDS:
            source = source_fields.get(key, "")
            if not source:
                continue
            if _looks_chinese(source):
                existing[key] = source
                continue
            if not force and existing.get(key) and not fields[key]["source_changed"]:
                continue
            keys.append(key)
            requests.append(source)

        trace: dict[str, Any] = {"mode": "translation_not_needed", "target_language": target_language}
        if requests:
            if translator is not None:
                translated = translator(requests, target_language)
                trace = {"mode": "translation_test_adapter", "target_language": target_language}
            else:
                translated, trace = call_translation_model(requests, target_language=target_language)
            if translated is None:
                result = self.detail(record_id, target_language=target_language)
                result["translation"] = trace
                return result
            if len(translated) != len(keys):
                raise ValueError("translation result count mismatch")
            for key, value in zip(keys, translated):
                existing[key] = str(value or "").strip()

        hashes = {key: _sha(value) for key, value in source_fields.items()}
        now = _now()
        with self.store.lock:
            self.store.conn.execute(
                """
                INSERT INTO formal_knowledge_translations(
                    record_id,target_language,translations_json,source_hashes_json,
                    translation_mode,model_trace_json,review_status,operator,note,
                    created_at,updated_at,confirmed_at
                ) VALUES(?,?,?,?,?,?,'draft','','',?,?, '')
                ON CONFLICT(record_id,target_language) DO UPDATE SET
                    translations_json=excluded.translations_json,
                    source_hashes_json=excluded.source_hashes_json,
                    translation_mode=excluded.translation_mode,
                    model_trace_json=excluded.model_trace_json,
                    review_status='draft',
                    updated_at=excluded.updated_at,
                    confirmed_at=''
                """,
                (
                    int(record_id), target_language,
                    json.dumps(existing, ensure_ascii=False),
                    json.dumps(hashes, ensure_ascii=False),
                    trace.get("mode", "translation_model"),
                    json.dumps(trace, ensure_ascii=False),
                    now, now,
                ),
            )
            self.store.conn.commit()
        result = self.detail(record_id, target_language=target_language)
        result["translation"] = trace
        return result

    def save_review(
        self,
        record_id: int,
        *,
        target_language: str = "zh-CN",
        translations: dict[str, Any],
        action: str = "save",
        operator: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        if action not in {"save", "confirm"}:
            raise ValueError("action must be save or confirm")
        record = self._record(record_id)
        source_fields = self._source_fields(record)
        clean = {
            key: str((translations or {}).get(key) or "").strip()
            for key in TRANSLATABLE_FIELDS
        }
        hashes = {key: _sha(value) for key, value in source_fields.items()}
        now = _now()
        status = "confirmed" if action == "confirm" else "draft"
        confirmed_at = now if action == "confirm" else ""
        with self.store.lock:
            self.store.conn.execute(
                """
                INSERT INTO formal_knowledge_translations(
                    record_id,target_language,translations_json,source_hashes_json,
                    translation_mode,model_trace_json,review_status,operator,note,
                    created_at,updated_at,confirmed_at
                ) VALUES(?,?,?,?,?,'{}',?,?,?,?,?,?)
                ON CONFLICT(record_id,target_language) DO UPDATE SET
                    translations_json=excluded.translations_json,
                    source_hashes_json=excluded.source_hashes_json,
                    translation_mode='human_edited',
                    review_status=excluded.review_status,
                    operator=excluded.operator,
                    note=excluded.note,
                    updated_at=excluded.updated_at,
                    confirmed_at=excluded.confirmed_at
                """,
                (
                    int(record_id), target_language,
                    json.dumps(clean, ensure_ascii=False),
                    json.dumps(hashes, ensure_ascii=False),
                    "human_edited", status,
                    str(operator or "").strip(), str(note or "").strip(),
                    now, now, confirmed_at,
                ),
            )
            self.store.conn.commit()
        return self.detail(record_id, target_language=target_language)
