from __future__ import annotations

import difflib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app.store import KnowledgeStore


DATE_PATTERNS = (
    re.compile(r"\b(?:19|20)\d{2}[-/.年](?:0?[1-9]|1[0-2])[-/.月](?:0?[1-9]|[12]\d|3[01])日?\b"),
    re.compile(r"\b(?:0?[1-9]|[12]\d|3[01])[./-](?:0?[1-9]|1[0-2])[./-](?:19|20)\d{2}\b"),
)
VERSION_PATTERN = re.compile(
    r"(?i)(?:version|revision|rev\.?|版本|版次|第\s*\d+\s*版)\s*[:：#-]?\s*[A-Za-z0-9._-]+|\bV\d+(?:\.\d+)*\b"
)
IDENTIFIER_PATTERN = re.compile(
    r"(?i)\b(?:EU\s*)?(?:REG|DIRECTIVE|DECISION|EN|IEC|ISO|DIN|BS|NF|UL|CFR|CELEX)[\s:/-]*[A-Z0-9./()-]{2,}\b"
)
REQUIREMENT_TERMS = (
    "shall", "must", "required", "requirement", "prohibited", "shall not", "must not",
    "应当", "必须", "不得", "禁止", "要求", "需要", "应",
)
STATUS_TERMS = (
    "effective", "in force", "repealed", "withdrawn", "superseded", "transitional",
    "生效", "废止", "撤回", "被替代", "过渡期", "失效",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_line(value: str) -> str:
    return " ".join(str(value or "").replace("\u00a0", " ").split())


def _lines(text: str, *, limit: int = 12000) -> list[str]:
    output: list[str] = []
    seen_blank = False
    for raw in str(text or "").splitlines():
        line = _clean_line(raw)
        if not line:
            if output and not seen_blank:
                output.append("")
            seen_blank = True
            continue
        seen_blank = False
        if output and line == output[-1]:
            continue
        output.append(line)
        if len(output) >= limit:
            output.append("[内容过长，差异分析已截断]")
            break
    while output and output[-1] == "":
        output.pop()
    return output


def _extract_signals(lines: list[str]) -> dict[str, list[str]]:
    joined = "\n".join(lines)
    dates: set[str] = set()
    for pattern in DATE_PATTERNS:
        dates.update(match.group(0) for match in pattern.finditer(joined))
    versions = {match.group(0) for match in VERSION_PATTERN.finditer(joined)}
    identifiers = {match.group(0) for match in IDENTIFIER_PATTERN.finditer(joined)}
    requirement_lines = [
        line for line in lines
        if line and any(term.lower() in line.lower() for term in REQUIREMENT_TERMS)
    ][:80]
    status_lines = [
        line for line in lines
        if line and any(term.lower() in line.lower() for term in STATUS_TERMS)
    ][:80]
    return {
        "dates": sorted(dates),
        "versions": sorted(versions),
        "identifiers": sorted(identifiers),
        "requirement_lines": requirement_lines,
        "status_lines": status_lines,
    }


def _set_delta(before: list[str], after: list[str]) -> dict[str, list[str]]:
    left, right = set(before), set(after)
    return {"removed": sorted(left - right), "added": sorted(right - left)}


def _similarity(a: str, b: str) -> float:
    return round(difflib.SequenceMatcher(None, a, b, autojunk=False).ratio(), 4)


class SourceDifferenceService:
    """Produces reviewable text/field differences for changed source snapshots.

    A report is evidence for human review only. It never edits the formal catalog,
    approves candidates, or decides legal applicability.
    """

    def __init__(self, store: KnowledgeStore):
        self.store = store
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.store.lock:
            self.store.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS source_diff_reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    update_event_id INTEGER NOT NULL UNIQUE,
                    profile_id INTEGER NOT NULL,
                    profile_key TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    previous_run_id INTEGER,
                    current_run_id INTEGER NOT NULL,
                    previous_document_id INTEGER,
                    current_document_id INTEGER,
                    report_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_source_diff_profile
                    ON source_diff_reports(profile_id,id DESC);

                CREATE TABLE IF NOT EXISTS source_diff_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    update_event_id INTEGER NOT NULL UNIQUE,
                    review_status TEXT NOT NULL DEFAULT 'draft',
                    reviewed_items_json TEXT NOT NULL DEFAULT '[]',
                    operator TEXT DEFAULT '',
                    note TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    confirmed_at TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_source_diff_reviews_status
                    ON source_diff_reviews(review_status,id DESC);
                """
            )
            self.store.conn.commit()

    def _event(self, event_id: int) -> dict[str, Any]:
        with self.store.lock:
            row = self.store.conn.execute(
                "SELECT * FROM source_update_events WHERE id=?", (int(event_id),)
            ).fetchone()
        if not row:
            raise ValueError("source update event not found")
        return dict(row)

    def _run_for_sha(self, profile_id: int, sha256: str, *, before_run_id: int) -> dict[str, Any] | None:
        if not sha256:
            return None
        with self.store.lock:
            row = self.store.conn.execute(
                """
                SELECT * FROM collection_runs
                WHERE profile_id=? AND sha256=? AND id<? AND status='completed'
                ORDER BY id DESC LIMIT 1
                """,
                (int(profile_id), sha256, int(before_run_id)),
            ).fetchone()
        return dict(row) if row else None

    def _run(self, run_id: int) -> dict[str, Any]:
        with self.store.lock:
            row = self.store.conn.execute(
                "SELECT * FROM collection_runs WHERE id=?", (int(run_id),)
            ).fetchone()
        if not row:
            raise ValueError("collection run not found")
        return dict(row)

    def _document(self, document_id: int | None) -> dict[str, Any] | None:
        if not document_id:
            return None
        with self.store.lock:
            row = self.store.conn.execute(
                "SELECT id,filename,title,full_text,metadata,created_at FROM documents WHERE id=?",
                (int(document_id),),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["metadata"] = json.loads(item.get("metadata") or "{}")
        return item

    def _change_blocks(self, before: list[str], after: list[str]) -> list[dict[str, Any]]:
        matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
        blocks: list[dict[str, Any]] = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            old_lines = [line for line in before[i1:i2] if line]
            new_lines = [line for line in after[j1:j2] if line]
            block: dict[str, Any] = {
                "change_type": {
                    "replace": "modified",
                    "delete": "removed",
                    "insert": "added",
                }.get(tag, tag),
                "old_start": i1 + 1,
                "old_end": i2,
                "new_start": j1 + 1,
                "new_end": j2,
                "before": old_lines[:40],
                "after": new_lines[:40],
            }
            if old_lines and new_lines:
                block["similarity"] = _similarity("\n".join(old_lines), "\n".join(new_lines))
            blocks.append(block)
            if len(blocks) >= 300:
                blocks.append({
                    "change_type": "truncated",
                    "before": [],
                    "after": ["[差异块过多，已截断]"],
                })
                break
        return blocks

    def _structured_change_items(self, signals: dict[str, dict[str, list[str]]]) -> list[dict[str, Any]]:
        field_map = {
            "dates": ("effective_date", "生效/日期"),
            "versions": ("version", "版本"),
            "identifiers": ("identifier", "法规/标准编号"),
            "requirement_lines": ("requirement", "技术/准入要求"),
            "status_lines": ("status", "状态/效力"),
        }
        items: list[dict[str, Any]] = []
        seq = 1
        for key, (field, label) in field_map.items():
            group = signals.get(key) or {"removed": [], "added": []}
            removed = list(group.get("removed") or [])
            added = list(group.get("added") or [])
            if len(removed) == 1 and len(added) == 1 and key in {"dates", "versions"}:
                items.append({
                    "item_id": f"chg-{seq}",
                    "change_type": "modified",
                    "field": field,
                    "label": label,
                    "before": removed[0],
                    "after": added[0],
                    "impact_note": "",
                    "selected": True,
                })
                seq += 1
                continue
            for value in removed:
                items.append({
                    "item_id": f"chg-{seq}",
                    "change_type": "removed",
                    "field": field,
                    "label": label,
                    "before": value,
                    "after": "",
                    "impact_note": "",
                    "selected": True,
                })
                seq += 1
            for value in added:
                items.append({
                    "item_id": f"chg-{seq}",
                    "change_type": "added",
                    "field": field,
                    "label": label,
                    "before": "",
                    "after": value,
                    "impact_note": "",
                    "selected": True,
                })
                seq += 1
        return items

    def analyze(self, event_id: int, *, refresh: bool = False) -> dict[str, Any]:
        event = self._event(event_id)
        if event.get("change_type") != "changed":
            raise ValueError("only changed source events can be compared")

        if not refresh:
            with self.store.lock:
                existing = self.store.conn.execute(
                    "SELECT report_json FROM source_diff_reports WHERE update_event_id=?",
                    (int(event_id),),
                ).fetchone()
            if existing:
                return json.loads(existing["report_json"])

        current_run = self._run(int(event["run_id"]))
        previous_run = self._run_for_sha(
            int(event["profile_id"]),
            str(event.get("previous_sha256") or ""),
            before_run_id=int(event["run_id"]),
        )
        if not previous_run:
            raise ValueError("previous successful source version not found")
        current_doc = self._document(current_run.get("document_id"))
        previous_doc = self._document(previous_run.get("document_id"))
        if not current_doc or not previous_doc:
            raise ValueError("both source versions must be parsed before difference analysis")

        before = _lines(previous_doc["full_text"])
        after = _lines(current_doc["full_text"])
        blocks = self._change_blocks(before, after)
        block_counts = Counter(block["change_type"] for block in blocks)
        before_signals = _extract_signals(before)
        after_signals = _extract_signals(after)

        signals = {
            "dates": _set_delta(before_signals["dates"], after_signals["dates"]),
            "versions": _set_delta(before_signals["versions"], after_signals["versions"]),
            "identifiers": _set_delta(before_signals["identifiers"], after_signals["identifiers"]),
            "requirement_lines": _set_delta(
                before_signals["requirement_lines"], after_signals["requirement_lines"]
            ),
            "status_lines": _set_delta(before_signals["status_lines"], after_signals["status_lines"]),
        }
        signal_count = sum(
            len(group["added"]) + len(group["removed"]) for group in signals.values()
        )
        changed_old = sum(
            len(block.get("before") or []) for block in blocks
            if block.get("change_type") in {"modified", "removed"}
        )
        changed_new = sum(
            len(block.get("after") or []) for block in blocks
            if block.get("change_type") in {"modified", "added"}
        )
        report: dict[str, Any] = {
            "update_event_id": int(event_id),
            "profile_id": int(event["profile_id"]),
            "profile_key": event["profile_key"],
            "source_key": event["source_key"],
            "created_at": _now(),
            "review_status": "needs_human_review",
            "previous": {
                "run_id": previous_run["id"],
                "document_id": previous_doc["id"],
                "filename": previous_doc["filename"],
                "sha256": previous_run.get("sha256", ""),
                "collected_at": previous_run.get("finished_at", ""),
                "source_url": previous_doc["metadata"].get("source_url", ""),
            },
            "current": {
                "run_id": current_run["id"],
                "document_id": current_doc["id"],
                "filename": current_doc["filename"],
                "sha256": current_run.get("sha256", ""),
                "collected_at": current_run.get("finished_at", ""),
                "source_url": current_doc["metadata"].get("source_url", ""),
            },
            "summary": {
                "old_lines": len([line for line in before if line]),
                "new_lines": len([line for line in after if line]),
                "change_blocks": len(blocks),
                "modified_blocks": block_counts.get("modified", 0),
                "added_blocks": block_counts.get("added", 0),
                "removed_blocks": block_counts.get("removed", 0),
                "changed_old_lines": changed_old,
                "changed_new_lines": changed_new,
                "signal_changes": signal_count,
            },
            "signals": signals,
            "change_items": self._structured_change_items(signals),
            "blocks": blocks,
            "notice": "差异结果先进入人工复核；复核人员可以修改、补充或取消变化项。确认复核结果后不自动修改正式知识，后续再进入影响分析或正式知识维护。",
        }

        payload = json.dumps(report, ensure_ascii=False)
        now = _now()
        with self.store.lock:
            self.store.conn.execute(
                """
                INSERT INTO source_diff_reports(
                    update_event_id,profile_id,profile_key,source_key,
                    previous_run_id,current_run_id,previous_document_id,current_document_id,
                    report_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(update_event_id) DO UPDATE SET
                    previous_run_id=excluded.previous_run_id,
                    current_run_id=excluded.current_run_id,
                    previous_document_id=excluded.previous_document_id,
                    current_document_id=excluded.current_document_id,
                    report_json=excluded.report_json,
                    updated_at=excluded.updated_at
                """,
                (
                    int(event_id), int(event["profile_id"]), event["profile_key"], event["source_key"],
                    int(previous_run["id"]), int(current_run["id"]), int(previous_doc["id"]),
                    int(current_doc["id"]), payload, now, now,
                ),
            )
            self.store.conn.commit()
        return report

    def review_state(self, event_id: int) -> dict[str, Any]:
        report = self.analyze(event_id)
        with self.store.lock:
            row = self.store.conn.execute(
                "SELECT * FROM source_diff_reviews WHERE update_event_id=?",
                (int(event_id),),
            ).fetchone()
        if not row:
            return {
                "update_event_id": int(event_id),
                "review_status": "draft",
                "operator": "",
                "note": "",
                "confirmed_at": "",
                "items": report.get("change_items", []),
            }
        item = dict(row)
        item["items"] = json.loads(item.pop("reviewed_items_json") or "[]")
        return item

    def save_review(
        self,
        event_id: int,
        *,
        items: list[dict[str, Any]],
        action: str = "save",
        operator: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        if action not in {"save", "confirm", "dismiss"}:
            raise ValueError("action must be save, confirm or dismiss")
        report = self.analyze(event_id)
        source_items = {str(item.get("item_id")): item for item in report.get("change_items", [])}
        reviewed: list[dict[str, Any]] = []
        for index, raw in enumerate(items or [], start=1):
            item_id = str(raw.get("item_id") or f"manual-{index}")
            base = dict(source_items.get(item_id) or {})
            change_type = str(raw.get("change_type") or base.get("change_type") or "modified").strip()
            if change_type not in {"added", "removed", "modified"}:
                raise ValueError(f"unsupported change_type: {change_type}")
            label = str(raw.get("label") or base.get("label") or "其他变化").strip()
            if not label:
                raise ValueError("change item label is required")
            reviewed.append({
                "item_id": item_id,
                "change_type": change_type,
                "field": str(raw.get("field") or base.get("field") or "custom").strip(),
                "label": label,
                "before": str(raw.get("before") if raw.get("before") is not None else base.get("before") or "").strip(),
                "after": str(raw.get("after") if raw.get("after") is not None else base.get("after") or "").strip(),
                "impact_note": str(raw.get("impact_note") or "").strip(),
                "selected": bool(raw.get("selected", True)),
            })

        now = _now()
        status = {"save": "draft", "confirm": "confirmed", "dismiss": "dismissed"}[action]
        confirmed_at = now if action in {"confirm", "dismiss"} else ""
        with self.store.lock:
            self.store.conn.execute(
                """
                INSERT INTO source_diff_reviews(
                    update_event_id,review_status,reviewed_items_json,operator,note,
                    created_at,updated_at,confirmed_at
                ) VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(update_event_id) DO UPDATE SET
                    review_status=excluded.review_status,
                    reviewed_items_json=excluded.reviewed_items_json,
                    operator=excluded.operator,
                    note=excluded.note,
                    updated_at=excluded.updated_at,
                    confirmed_at=excluded.confirmed_at
                """,
                (
                    int(event_id), status, json.dumps(reviewed, ensure_ascii=False),
                    str(operator or "").strip(), str(note or "").strip(),
                    now, now, confirmed_at,
                ),
            )
            self.store.conn.commit()
        return self.review_state(event_id)

    def list_reports(self, *, limit: int = 100) -> dict[str, Any]:
        with self.store.lock:
            rows = self.store.conn.execute(
                """
                SELECT id,update_event_id,profile_id,profile_key,source_key,
                       previous_run_id,current_run_id,previous_document_id,current_document_id,
                       created_at,updated_at
                FROM source_diff_reports ORDER BY id DESC LIMIT ?
                """,
                (min(max(int(limit), 1), 500),),
            ).fetchall()
        items = [dict(row) for row in rows]
        return {"items": items, "summary": {"reports": len(items)}}
