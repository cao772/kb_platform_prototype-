from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

MODEL_PURPOSES = ("extraction", "qa", "vision")
MODEL_LABELS = {
    "extraction": "知识抽取模型",
    "qa": "法规问答模型",
    "vision": "图片/扫描件识别模型",
}

DEFAULT_SETTINGS: dict[str, Any] = {
    "parser": {
        "backend": "lightweight",
        "ocr_mode": "auto",
        "vision_max_pages": 6,
        "auto_extract": True,
        "use_model_extraction": True,
    },
    "models": {
        purpose: {
            "enabled": False,
            "provider": "openai-compatible",
            "base_url": "",
            "model": "",
            "api_key": "",
            "timeout_seconds": 60,
        }
        for purpose in MODEL_PURPOSES
    },
}


def settings_path() -> Path:
    env = os.getenv("KB_SETTINGS_PATH", "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[1] / "data" / "runtime_settings.json"


def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return "******"
    return "******" + value[-4:]


class RuntimeSettingsStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else settings_path()
        self.lock = threading.RLock()

    def load(self) -> dict[str, Any]:
        with self.lock:
            if not self.path.exists():
                return deepcopy(DEFAULT_SETTINGS)
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raw = {}
            return _deep_merge(DEFAULT_SETTINGS, raw if isinstance(raw, dict) else {})

    def public(self) -> dict[str, Any]:
        data = self.load()
        for purpose in MODEL_PURPOSES:
            profile = data["models"][purpose]
            profile["api_key_set"] = bool(profile.get("api_key"))
            profile["api_key"] = _mask_secret(str(profile.get("api_key") or ""))
            profile["configured"] = bool(profile.get("enabled") and profile.get("base_url") and profile.get("model"))
            profile["label"] = MODEL_LABELS[purpose]
        return data

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            current = self.load()
            parser = payload.get("parser") if isinstance(payload.get("parser"), dict) else None
            if parser is not None:
                backend = str(parser.get("backend", current["parser"]["backend"]))
                if backend not in {"lightweight", "docling"}:
                    raise ValueError("parser backend must be lightweight or docling")
                ocr_mode = str(parser.get("ocr_mode", current["parser"]["ocr_mode"]))
                if ocr_mode not in {"auto", "model", "off"}:
                    raise ValueError("ocr_mode must be auto, model or off")
                current["parser"].update({
                    "backend": backend,
                    "ocr_mode": ocr_mode,
                    "vision_max_pages": max(1, min(int(parser.get("vision_max_pages", current["parser"]["vision_max_pages"])), 30)),
                    "auto_extract": bool(parser.get("auto_extract", current["parser"]["auto_extract"])),
                    "use_model_extraction": bool(parser.get("use_model_extraction", current["parser"]["use_model_extraction"])),
                })

            models = payload.get("models") if isinstance(payload.get("models"), dict) else {}
            for purpose in MODEL_PURPOSES:
                patch = models.get(purpose)
                if not isinstance(patch, dict):
                    continue
                profile = current["models"][purpose]
                profile["enabled"] = bool(patch.get("enabled", profile["enabled"]))
                profile["provider"] = str(patch.get("provider", profile["provider"]) or "openai-compatible").strip()
                profile["base_url"] = str(patch.get("base_url", profile["base_url"]) or "").strip()
                profile["model"] = str(patch.get("model", profile["model"]) or "").strip()
                profile["timeout_seconds"] = max(5, min(int(patch.get("timeout_seconds", profile["timeout_seconds"])), 300))
                if "api_key" in patch:
                    supplied = str(patch.get("api_key") or "").strip()
                    if supplied and not supplied.startswith("******"):
                        profile["api_key"] = supplied
                    elif patch.get("clear_api_key") is True:
                        profile["api_key"] = ""

            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
            temp_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            return self.public()


def current_parser_settings() -> dict[str, Any]:
    return RuntimeSettingsStore().load()["parser"]


def model_profile(purpose: str) -> dict[str, Any]:
    if purpose not in MODEL_PURPOSES:
        raise ValueError(f"unsupported model purpose: {purpose}")
    profile = RuntimeSettingsStore().load()["models"][purpose]

    prefix_map = {
        "extraction": "KB_LLM",
        "qa": "KB_QA_LLM",
        "vision": "KB_VISION_LLM",
    }
    prefix = prefix_map[purpose]
    fallback_prefix = "KB_LLM" if purpose == "qa" else prefix
    base_url = os.getenv(f"{prefix}_BASE_URL", "") or (os.getenv(f"{fallback_prefix}_BASE_URL", "") if fallback_prefix != prefix else "") or profile.get("base_url", "")
    model = os.getenv(f"{prefix}_MODEL", "") or (os.getenv(f"{fallback_prefix}_MODEL", "") if fallback_prefix != prefix else "") or profile.get("model", "")
    api_key = os.getenv(f"{prefix}_API_KEY", "") or (os.getenv(f"{fallback_prefix}_API_KEY", "") if fallback_prefix != prefix else "") or profile.get("api_key", "")
    timeout = os.getenv(f"{prefix}_TIMEOUT", "") or profile.get("timeout_seconds", 60)
    enabled_env = os.getenv(f"{prefix}_ENABLED")
    enabled = profile.get("enabled", False) if enabled_env is None else enabled_env.lower() in {"1", "true", "yes", "on"}
    if os.getenv(f"{prefix}_BASE_URL") and os.getenv(f"{prefix}_MODEL"):
        enabled = True
    return {
        **profile,
        "purpose": purpose,
        "base_url": str(base_url or "").strip(),
        "model": str(model or "").strip(),
        "api_key": str(api_key or ""),
        "timeout_seconds": float(timeout or 60),
        "enabled": bool(enabled),
        "configured": bool(enabled and base_url and model),
    }
