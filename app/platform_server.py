from __future__ import annotations

from http import HTTPStatus
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from app.map_service import RegulationMapService
from app.ontology_governance import OntologyGovernanceService
from app.regions import target_market_catalog
from app.server import Handler as BaseHandler
from app.server import STATIC_DIR, store

regulation_map = RegulationMapService(store)
ontology_governance = OntologyGovernanceService(store)


class Handler(BaseHandler):
    """Current platform handler with business map and ontology governance endpoints."""

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/api/regions":
            self._send_json(target_market_catalog())
            return

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

        if parsed.path == "/api/ontology/blueprint":
            self._send_json(ontology_governance.blueprint())
            return

        if parsed.path == "/api/ontology/coverage":
            try:
                self._send_json(ontology_governance.coverage(
                    region_code=params.get("region", [""])[0],
                    product_class=params.get("product_class", [""])[0],
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/ontology/candidates":
            try:
                limit = min(int(params.get("limit", ["200"])[0] or 200), 500)
                self._send_json(ontology_governance.candidate_queue(limit=limit))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/ontology/domain":
            try:
                self._send_json(ontology_governance.domain_view(
                    params.get("domain", [""])[0],
                    region_code=params.get("region", [""])[0],
                    product_class=params.get("product_class", [""])[0],
                    limit=min(int(params.get("limit", ["500"])[0] or 500), 2000),
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path in {"/map", "/map.html"}:
            self._send_file(STATIC_DIR / "map.html")
            return

        if parsed.path in {"/ontology", "/ontology.html"}:
            self._send_file(STATIC_DIR / "ontology.html")
            return

        # Keep the static business home page unchanged on disk, but surface the
        # current operational pages from the runtime entrypoint.
        if parsed.path in {"/", "/index.html"}:
            text = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
            anchor = '<div class="top-actions">'
            links = (
                '<a class="top-link" href="/ontology">知识本体</a>'
                '<a class="top-link" href="/map">法规认证地图</a>'
                '<a class="top-link" href="/changes">变化待办</a>'
            )
            if anchor in text and 'href="/ontology"' not in text:
                text = text.replace(anchor, anchor + links, 1)
            body = text.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/ontology/validate":
            try:
                payload = self._read_json()
                formal = str(payload.pop("_formal", "")).lower() in {"1", "true", "yes", "on"}
                self._send_json(ontology_governance.validate_payload(payload, formal=formal))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        super().do_POST()


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("Knowledge platform running at http://127.0.0.1:8765")
    print("Knowledge ontology: http://127.0.0.1:8765/ontology")
    print("Regulation certification map: http://127.0.0.1:8765/map")
    print("Formal knowledge catalog: http://127.0.0.1:8765/catalog")
    print("Regulation change & todo center: http://127.0.0.1:8765/changes")
    print("Document & model management: http://127.0.0.1:8765/admin")
    server.serve_forever()


if __name__ == "__main__":
    main()
