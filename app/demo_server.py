from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from app.demo_scenario import DemoScenarioService

ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "static"
DEMO_DB = ROOT / "data" / "demo_scenario.db"
service = DemoScenarioService(DEMO_DB)
service.ensure_seeded()


def json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, payload: object, status: int = 200) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/demo/health":
            self._send_json({"ok": True, "demo_only": True, "stats": service.store.stats(), "graph": service.graph.summary()})
            return
        if parsed.path == "/api/demo/scenario":
            try:
                self._send_json(service.snapshot())
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path in {"/", "/demo", "/demo.html"}:
            self._send_file(STATIC_DIR / "demo.html")
            return
        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/demo/reset":
            try:
                self._send_json({"ok": True, "scenario": service.seed()})
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/demo/answer":
            try:
                payload = self._read_json()
                self._send_json(service.answer(payload.get("question", "")))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def _send_file(self, path: Path) -> None:
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.address_string()} - {fmt % args}")


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 8766), Handler)
    print("Customer demo sandbox running at http://127.0.0.1:8766")
    print("Synthetic demo data only; never use as real compliance advice.")
    server.serve_forever()


if __name__ == "__main__":
    main()
