from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path
from typing import Any

ALLOWED_SUFFIXES = {
    ".docx", ".pdf", ".txt", ".md", ".csv", ".xlsx", ".pptx",
    ".json", ".xml", ".html", ".htm",
    ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp",
}
DEFAULT_MAX_BYTES = 25 * 1024 * 1024


def sanitize_filename(filename: str) -> str:
    name = Path(filename or "").name.strip()
    name = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "_", name)
    if not name or name in {".", ".."}:
        raise ValueError("invalid filename")
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError(f"unsupported file type: {suffix or '(none)'}")
    return name[:180]


def save_browser_upload(
    *,
    filename: str,
    content_base64: str,
    upload_dir: str | Path,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    safe_name = sanitize_filename(filename)
    try:
        data = base64.b64decode(content_base64, validate=True)
    except Exception as exc:
        raise ValueError("content_base64 is invalid") from exc
    if not data:
        raise ValueError("uploaded file is empty")
    if len(data) > max_bytes:
        raise ValueError(f"file too large: {len(data)} bytes, max {max_bytes}")

    target_dir = Path(upload_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / safe_name
    temp = target.with_suffix(target.suffix + ".uploading")
    temp.write_bytes(data)
    temp.replace(target)
    return {
        "filename": safe_name,
        "path": str(target.resolve()),
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
