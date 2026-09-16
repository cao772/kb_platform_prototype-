from __future__ import annotations

from http import HTTPStatus
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from app.map_service import RegulationMapService
from app.server import Handler as BaseHandler
from app.server import STATIC_DIR, store

regulation_map = RegulationMapService(store)


class Handler(BaseHandler):
    """Current platform handler with business map endpoints layered on the stable server."""

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/api/map/overview":
            try:
                self._send_json(regulation_map.overview(
                    product_class=params.get("product_class", [""])[0],
                    record_type=params.get("type", [""])[0],
                    as_of=params.get("as_of", [None])[0] or None,
                    only_changed=str(params.get("only_changed", [""])[0]).lower() in {"1", "true", "yes", "on"},
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/map/detail":
            try:
                self._send_json(regulation_map.detail(
                    params.get("region", [""])[0],
                    product_class=params.get("product_class", [""])[0],
                    as_of=params.get("as_of", [None])[0] or None,
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path in {"/map", "/map.html"}:
            self._send_file(STATIC_DIR / "map.html")
            return

        # Keep the static business home page unchanged on disk, but surface the
        # new operational pages from the current runtime entrypoint.
        if parsed.path in {"/", "/index.html"}:
            text = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
            anchor = '<div class="top-actions">'
            links = '<a class="top-link" href="/map">法规认证地图</a><a class="top-link" href="/changes">变化待办</a>'
            if anchor in text and 'href="/map"' not in text:
                text = text.replace(anchor, anchor + links, 1)
            body = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        super().do_GET()


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("Knowledge platform running at http://127.0.0.1:8765")
    print("Regulation certification map: http://127.0.0.1:8765/map")
    print("Formal knowledge catalog: http://127.0.0.1:8765/catalog")
    print("Regulation change & todo center: http://127.0.0.1:8765/changes")
    print("Document & model management: http://127.0.0.1:8765/admin")
    server.serve_forever()


if __name__ == "__main__":
    main()
