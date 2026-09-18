from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.model_gateway import call_translation_model
from app.store import KnowledgeStore


REVIEW_STATUSES = {"draft", "confirmed"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _looks_chinese(text: str) -> bool:
    value = str(text or "")
    chinese = len(re.findall(r"[\u4e00-\u9fff]", value))
    visible = max(1, len(re.sub(r"\s+", "", value)))
    return chinese >= 2 and chinese / visible >= 0.15


class DocumentTranslationService:
    """Bilingual derivative layer for collected source documents.

    Original files, parsed document text and source chunks are never rewritten.
    Translations are stored separately, can be manually edited and must be
    confirmed independently from formal knowledge review.
    """

    def __init__(self, store: KnowledgeStore):
        self.store = store
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.store.lock:
            self.store.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS document_translation_segments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL,
                    chunk_id INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    target_language TEXT NOT NULL DEFAULT 'zh-CN',
                    source_sha256 TEXT NOT NULL,
                    source_text TEXT NOT NULL,
                    translated_text TEXT NOT NULL DEFAULT '',
                    translation_mode TEXT DEFAULT '',
                    model_trace_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(chunk_id,target_language)
                );
                CREATE INDEX IF NOT EXISTS idx_document_translation_document
                    ON document_translation_segments(document_id,target_language,chunk_index);

                CREATE TABLE IF NOT EXISTS document_translation_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    document_id INTEGER NOT NULL,
                    target_language TEXT NOT NULL DEFAULT 'zh-CN',
                    review_status TEXT NOT NULL DEFAULT 'draft',
                    operator TEXT DEFAULT '',
                    note TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    confirmed_at TEXT DEFAULT '',
                    UNIQUE(document_id,target_language)
                );
                CREATE INDEX IF NOT EXISTS idx_document_translation_review_status
                    ON document_translation_reviews(review_status,id DESC);
                """
            )
            self.store.conn.commit()

    def _document(self, document_id: int) -> dict[str, Any]:
        with self.store.lock:
            row = self.store.conn.execute(
                """
                SELECT id,filename,title,knowledge_type,mime_type,parser,source_path,
                       full_text,metadata,created_at
                FROM documents WHERE id=?
                """,
                (int(document_id),),
            ).fetchone()
        if not row:
            raise ValueError("document not found")
        item = dict(row)
        item["metadata"] = json.loads(item.get("metadata") or "{}")
        return item

    def _chunks(self, document_id: int) -> list[dict[str, Any]]:
        with self.store.lock:
            rows = self.store.conn.execute(
                """
                SELECT id,document_id,chunk_index,text,metadata
                FROM chunks WHERE document_id=? ORDER BY chunk_index,id
                """,
                (int(document_id),),
            ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.get("metadata") or "{}")
            output.append(item)
        return output

    def raw_path(self, document_id: int) -> Path:
        document = self._document(document_id)
        metadata = document.get("metadata") or {}
        candidate = str(metadata.get("original_file_path") or document.get("source_path") or "").strip()
        if not candidate:
            raise ValueError("original source file path is unavailable")
        path = Path(candidate)
        if not path.exists() or not path.is_file():
            raise ValueError("original source file is unavailable")
        return path

    def detail(self, document_id: int, *, target_language: str = "zh-CN") -> dict[str, Any]:
        document = self._document(document_id)
        chunks = self._chunks(document_id)
        with self.store.lock:
            rows = self.store.conn.execute(
                """
                SELECT * FROM document_translation_segments
                WHERE document_id=? AND target_language=?
                ORDER BY chunk_index,id
                """,
                (int(document_id), target_language),
            ).fetchall()
            review = self.store.conn.execute(
                """
                SELECT * FROM document_translation_reviews
                WHERE document_id=? AND target_language=?
                """,
                (int(document_id), target_language),
            ).fetchone()
        translated_by_chunk = {int(row["chunk_id"]): dict(row) for row in rows}
        segments: list[dict[str, Any]] = []
        for chunk in chunks:
            saved = translated_by_chunk.get(int(chunk["id"]))
            if saved:
                item = saved
                item["model_trace"] = json.loads(item.pop("model_trace_json") or "{}")
                item["source_changed"] = item["source_sha256"] != hashlib.sha256(
                    chunk["text"].encode("utf-8")
                ).hexdigest()
                segments.append(item)
            else:
                segments.append({
                    "id": None,
                    "document_id": int(document_id),
                    "chunk_id": int(chunk["id"]),
                    "chunk_index": int(chunk["chunk_index"]),
                    "target_language": target_language,
                    "source_sha256": hashlib.sha256(chunk["text"].encode("utf-8")).hexdigest(),
                    "source_text": chunk["text"],
                    "translated_text": chunk["text"] if _looks_chinese(chunk["text"]) else "",
                    "translation_mode": "source_is_chinese" if _looks_chinese(chunk["text"]) else "not_translated",
                    "model_trace": {},
                    "source_changed": False,
                })
        review_item = dict(review) if review else {
            "review_status": "draft",
            "operator": "",
            "note": "",
            "confirmed_at": "",
        }
        return {
            "document": {
                "id": document["id"],
                "filename": document["filename"],
                "title": document["title"],
                "knowledge_type": document["knowledge_type"],
                "mime_type": document["mime_type"],
                "parser": document["parser"],
                "created_at": document["created_at"],
                "metadata": document["metadata"],
            },
            "target_language": target_language,
            "source_preserved": True,
            "segments": segments,
            "review": review_item,
            "summary": {
                "segments": len(segments),
                "translated": sum(1 for item in segments if item.get("translated_text")),
                "pending": sum(1 for item in segments if not item.get("translated_text")),
                "source_changed": sum(1 for item in segments if item.get("source_changed")),
            },
            "notice": "原始文件、原始解析文本和原始分片保持不变；中文翻译独立保存，可人工修订和确认。",
        }

    def translate(
        self,
        document_id: int,
        *,
        target_language: str = "zh-CN",
        force: bool = False,
        max_segments: int = 500,
        translator=None,
    ) -> dict[str, Any]:
        document = self._document(document_id)
        chunks = self._chunks(document_id)
        chunks = chunks[: min(max(int(max_segments), 1), 2000)]
        now = _now()

        with self.store.lock:
            existing_rows = self.store.conn.execute(
                """
                SELECT * FROM document_translation_segments
                WHERE document_id=? AND target_language=?
                """,
                (int(document_id), target_language),
            ).fetchall()
        existing = {int(row["chunk_id"]): dict(row) for row in existing_rows}

        pending: list[dict[str, Any]] = []
        for chunk in chunks:
            source_text = str(chunk["text"] or "")
            source_sha = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
            saved = existing.get(int(chunk["id"]))
            if (
                not force
                and saved
                and saved.get("source_sha256") == source_sha
                and str(saved.get("translated_text") or "").strip()
            ):
                continue
            if _looks_chinese(source_text):
                with self.store.lock:
                    self.store.conn.execute(
                        """
                        INSERT INTO document_translation_segments(
                            document_id,chunk_id,chunk_index,target_language,source_sha256,
                            source_text,translated_text,translation_mode,model_trace_json,
                            created_at,updated_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(chunk_id,target_language) DO UPDATE SET
                            source_sha256=excluded.source_sha256,
                            source_text=excluded.source_text,
                            translated_text=excluded.translated_text,
                            translation_mode=excluded.translation_mode,
                            model_trace_json=excluded.model_trace_json,
                            updated_at=excluded.updated_at
                        """,
                        (
                            int(document_id), int(chunk["id"]), int(chunk["chunk_index"]),
                            target_language, source_sha, source_text, source_text,
                            "source_is_chinese", "{}", now, now,
                        ),
                    )
                    self.store.conn.commit()
                continue
            pending.append({
                "chunk": chunk,
                "source_text": source_text,
                "source_sha": source_sha,
            })

        batch_size = 8
        translated_count = 0
        last_trace: dict[str, Any] = {"mode": "translation_not_needed"}
        for start in range(0, len(pending), batch_size):
            batch = pending[start:start + batch_size]
            texts = [item["source_text"] for item in batch]
            if translator is not None:
                values = translator(texts, target_language)
                trace = {"mode": "translation_test_adapter", "target_language": target_language}
            else:
                values, trace = call_translation_model(texts, target_language=target_language)
            last_trace = trace
            if values is None:
                break
            if len(values) != len(batch):
                raise ValueError("translation result count mismatch")
            with self.store.lock:
                for pending_item, translated in zip(batch, values):
                    chunk = pending_item["chunk"]
                    self.store.conn.execute(
                        """
                        INSERT INTO document_translation_segments(
                            document_id,chunk_id,chunk_index,target_language,source_sha256,
                            source_text,translated_text,translation_mode,model_trace_json,
                            created_at,updated_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(chunk_id,target_language) DO UPDATE SET
                            source_sha256=excluded.source_sha256,
                            source_text=excluded.source_text,
                            translated_text=excluded.translated_text,
                            translation_mode=excluded.translation_mode,
                            model_trace_json=excluded.model_trace_json,
                            updated_at=excluded.updated_at
                        """,
                        (
                            int(document_id), int(chunk["id"]), int(chunk["chunk_index"]),
                            target_language, pending_item["source_sha"], pending_item["source_text"],
                            str(translated or "").strip(), trace.get("mode", "translation_model"),
                            json.dumps(trace, ensure_ascii=False), now, now,
                        ),
                    )
                    translated_count += 1
                self.store.conn.commit()

        result = self.detail(document_id, target_language=target_language)
        result["translation"] = {
            **last_trace,
            "requested": len(pending),
            "translated_now": translated_count,
        }
        return result

    def save_review(
        self,
        document_id: int,
        *,
        target_language: str = "zh-CN",
        segments: list[dict[str, Any]],
        action: str = "save",
        operator: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        if action not in {"save", "confirm"}:
            raise ValueError("action must be save or confirm")
        chunk_map = {int(item["id"]): item for item in self._chunks(document_id)}
        now = _now()
        with self.store.lock:
            for raw in segments or []:
                chunk_id = int(raw.get("chunk_id") or 0)
                chunk = chunk_map.get(chunk_id)
                if not chunk:
                    raise ValueError(f"chunk does not belong to document: {chunk_id}")
                source_text = str(chunk["text"] or "")
                source_sha = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
                translated_text = str(raw.get("translated_text") or "").strip()
                self.store.conn.execute(
                    """
                    INSERT INTO document_translation_segments(
                        document_id,chunk_id,chunk_index,target_language,source_sha256,
                        source_text,translated_text,translation_mode,model_trace_json,
                        created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(chunk_id,target_language) DO UPDATE SET
                        source_sha256=excluded.source_sha256,
                        source_text=excluded.source_text,
                        translated_text=excluded.translated_text,
                        translation_mode='human_edited',
                        updated_at=excluded.updated_at
                    """,
                    (
                        int(document_id), chunk_id, int(chunk["chunk_index"]), target_language,
                        source_sha, source_text, translated_text, "human_edited", "{}", now, now,
                    ),
                )
            status = "confirmed" if action == "confirm" else "draft"
            confirmed_at = now if action == "confirm" else ""
            self.store.conn.execute(
                """
                INSERT INTO document_translation_reviews(
                    document_id,target_language,review_status,operator,note,
                    created_at,updated_at,confirmed_at
                ) VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(document_id,target_language) DO UPDATE SET
                    review_status=excluded.review_status,
                    operator=excluded.operator,
                    note=excluded.note,
                    updated_at=excluded.updated_at,
                    confirmed_at=excluded.confirmed_at
                """,
                (
                    int(document_id), target_language, status, str(operator or "").strip(),
                    str(note or "").strip(), now, now, confirmed_at,
                ),
            )
            self.store.conn.commit()
        return self.detail(document_id, target_language=target_language)
