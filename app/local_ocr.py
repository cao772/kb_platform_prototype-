"""Optional local PaddleOCR bridge with process timeout and cached-model configuration."""
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def configuration():
    config_path = ROOT / 'data/local_ocr.json'
    try:
        config = json.loads(config_path.read_text())
    except (OSError, ValueError):
        return {}
    if not Path(config.get('python', '')).is_file() or not Path(config.get('model_root', '')).is_dir():
        return {}
    return config


def recognize(image_bytes):
    config = configuration()
    if not config:
        return None, {'mode': 'local_ocr_unavailable'}
    try:
        with tempfile.TemporaryDirectory(prefix='kb-local-ocr-') as folder:
            image = Path(folder) / 'page.png'
            image.write_bytes(image_bytes)
            result = subprocess.run(
                [config['python'], str(ROOT / 'scripts/local_paddle_worker.py'), str(image), config['model_root']],
                capture_output=True, text=True, timeout=int(config.get('timeout_seconds', 120)),
                env={**os.environ, 'PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK': 'True', 'OMP_NUM_THREADS': '2'},
            )
        if result.returncode:
            return None, {'mode': 'local_ocr_failed', 'reason': result.stderr[-1800:]}
        payload = json.loads(result.stdout)
        return payload['text'], {'mode': 'local_ocr', 'engine': 'PaddleOCR', 'blocks': payload['blocks']}
    except Exception as exc:
        return None, {'mode': 'local_ocr_failed', 'reason': str(exc)}
