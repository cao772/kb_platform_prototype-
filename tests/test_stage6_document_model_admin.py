from __future__ import annotations

import base64
import os
import tempfile
from pathlib import Path

from app.processing import DocumentProcessingService
from app.runtime_settings import RuntimeSettingsStore, model_profile
from app.store import KnowledgeStore


def main() -> None:
    admin_page = (Path(__file__).resolve().parents[1] / "static" / "admin.html").read_text(encoding="utf-8")
    assert "const refreshedCard=document.querySelector" in admin_page
    assert "saveSettings({throwOnError:true})" in admin_page

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        settings_file = root / "runtime_settings.json"
        old_path = os.environ.get("KB_SETTINGS_PATH")
        os.environ["KB_SETTINGS_PATH"] = str(settings_file)
        try:
            settings = RuntimeSettingsStore(settings_file)
            saved = settings.save({
                "parser": {
                    "backend": "lightweight",
                    "ocr_mode": "auto",
                    "vision_max_pages": 4,
                    "auto_extract": True,
                    "use_model_extraction": False,
                },
                "models": {
                    "extraction": {
                        "enabled": True,
                        "base_url": "http://model.local/v1",
                        "model": "extract-model",
                        "api_key": "secret-example-key",
                        "timeout_seconds": 33,
                    },
                    "qa": {
                        "enabled": False,
                        "base_url": "http://qa.local/v1",
                        "model": "qa-model",
                    },
                },
            })
            assert saved["parser"]["vision_max_pages"] == 4
            assert saved["models"]["extraction"]["configured"] is True
            assert saved["models"]["extraction"]["api_key_set"] is True
            assert "secret-example-key" not in saved["models"]["extraction"]["api_key"]
            resolved = model_profile("extraction")
            assert resolved["model"] == "extract-model"
            assert resolved["api_key"] == "secret-example-key"

            store = KnowledgeStore(root / "knowledge.db")
            processing = DocumentProcessingService(store, root / "uploads", root / "processing_tasks.json")
            content = "家用电器法规资料。版本2026。认证要求需要人工校核。".encode("utf-8")
            task = processing.start_browser_upload({
                "filename": "法规资料.txt",
                "content_base64": base64.b64encode(content).decode("ascii"),
                "auto_extract": True,
                "use_model": False,
            })
            finished = processing.wait(task["id"], timeout=10)
            assert finished is not None
            assert finished["status"] == "completed", finished
            assert finished["progress"] == 100
            assert finished["document_id"]
            assert store.stats()["documents"] == 1
            assert processing.tasks.get(task["id"])["filename"] == "法规资料.txt"
        finally:
            if old_path is None:
                os.environ.pop("KB_SETTINGS_PATH", None)
            else:
                os.environ["KB_SETTINGS_PATH"] = old_path

    print("OK: stage6 document processing and model settings passed")


if __name__ == "__main__":
    main()
