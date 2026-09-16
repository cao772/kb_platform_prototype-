from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.ingest import ingest_file
from app.runtime_settings import current_parser_settings
from app.structured_extraction import extract_review_candidates_v2
from app.upload import save_browser_upload


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


STAGES = [
    ("received", "等待处理", 5),
    ("saved", "文件接收完成", 15),
    ("parsing", "正在解析文件", 35),
    ("indexed", "内容解析完成", 60),
    ("extracting", "正在识别法规认证信息", 75),
    ("review_ready", "待人工校核", 95),
    ("completed", "处理完成", 100),
]
STAGE_MAP = {key: {"label": label, "progress": progress} for key, label, progress in STAGES}


class ProcessingTaskStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _save(self, items: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(items[-300:], ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def create(self, filename: str, options: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            items = self._load()
            task = {
                "id": uuid.uuid4().hex[:16],
                "filename": filename,
                "status": "queued",
                "stage": "received",
                "stage_label": STAGE_MAP["received"]["label"],
                "progress": STAGE_MAP["received"]["progress"],
                "message": "已进入处理队列",
                "document_id": None,
                "candidate_count": 0,
                "parser": "",
                "error": "",
                "options": options,
                "created_at": _now(),
                "updated_at": _now(),
            }
            items.append(task)
            self._save(items)
            return dict(task)

    def update(self, task_id: str, *, stage: str | None = None, status: str | None = None, message: str | None = None, **extra: Any) -> dict[str, Any]:
        with self.lock:
            items = self._load()
            for item in items:
                if item.get("id") != task_id:
                    continue
                if stage:
                    item["stage"] = stage
                    info = STAGE_MAP.get(stage, {})
                    item["stage_label"] = info.get("label", stage)
                    item["progress"] = info.get("progress", item.get("progress", 0))
                if status:
                    item["status"] = status
                if message is not None:
                    item["message"] = message
                item.update(extra)
                item["updated_at"] = _now()
                self._save(items)
                return dict(item)
            raise ValueError("processing task not found")

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self.lock:
            for item in reversed(self._load()):
                if item.get("id") == task_id:
                    return dict(item)
        return None

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.lock:
            items = self._load()
        return [dict(item) for item in reversed(items[-limit:])]


class DocumentProcessingService:
    def __init__(self, store, upload_dir: str | Path, task_path: str | Path):
        self.store = store
        self.upload_dir = Path(upload_dir)
        self.tasks = ProcessingTaskStore(task_path)
        self._threads: dict[str, threading.Thread] = {}

    def start_browser_upload(self, payload: dict[str, Any]) -> dict[str, Any]:
        filename = str(payload.get("filename") or "").strip()
        if not filename:
            raise ValueError("filename is required")
        parser_settings = current_parser_settings()
        options = {
            "auto_extract": bool(payload.get("auto_extract", parser_settings.get("auto_extract", True))),
            "use_model": bool(payload.get("use_model", parser_settings.get("use_model_extraction", True))),
        }
        task = self.tasks.create(filename, options)
        thread = threading.Thread(target=self._run, args=(task["id"], payload, options), daemon=True, name=f"kb-processing-{task['id']}")
        self._threads[task["id"]] = thread
        thread.start()
        return task

    def _run(self, task_id: str, payload: dict[str, Any], options: dict[str, Any]) -> None:
        try:
            self.tasks.update(task_id, stage="saved", status="running", message="正在保存上传文件")
            saved = save_browser_upload(
                filename=str(payload.get("filename") or ""),
                content_base64=str(payload.get("content_base64") or ""),
                upload_dir=self.upload_dir,
            )
            self.tasks.update(task_id, stage="parsing", status="running", message="正在解析文档内容")
            document_id = ingest_file(self.store, saved["path"])
            docs = [item for item in self.store.list_documents() if int(item.get("id") or 0) == int(document_id)]
            parser_name = docs[0].get("parser", "") if docs else ""
            self.tasks.update(
                task_id,
                stage="indexed",
                status="running",
                message="文档已解析，可进入知识识别",
                document_id=document_id,
                parser=parser_name,
                file_size=saved.get("size"),
                sha256=saved.get("sha256"),
            )
            candidate_count = 0
            extraction = None
            if options.get("auto_extract"):
                self.tasks.update(task_id, stage="extracting", status="running", message="正在识别法规、标准、认证和要求")
                extraction = extract_review_candidates_v2(self.store, int(document_id), use_llm=bool(options.get("use_model")))
                candidate_count = int(extraction.get("total_created") or 0)
                self.tasks.update(
                    task_id,
                    stage="review_ready",
                    status="running",
                    message=f"已识别 {candidate_count} 条待校核内容",
                    candidate_count=candidate_count,
                    extraction=extraction,
                )
            self.tasks.update(
                task_id,
                stage="completed",
                status="completed",
                message="处理完成，待校核内容可进入知识审核" if options.get("auto_extract") else "文件解析完成",
                candidate_count=candidate_count,
                extraction=extraction,
            )
        except Exception as exc:
            self.tasks.update(
                task_id,
                status="failed",
                message="处理失败",
                error=str(exc),
                error_type=type(exc).__name__,
            )

    def wait(self, task_id: str, timeout: float = 15.0) -> dict[str, Any] | None:
        end = time.time() + timeout
        while time.time() < end:
            item = self.tasks.get(task_id)
            if item and item.get("status") in {"completed", "failed"}:
                return item
            time.sleep(0.05)
        return self.tasks.get(task_id)
