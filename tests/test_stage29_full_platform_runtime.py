from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.request import Request, urlopen

from app.platform_server import Handler


def _get(base: str, path: str) -> tuple[int, str, str]:
    with urlopen(base + path, timeout=10) as response:
        return (
            response.status,
            response.headers.get("Content-Type", ""),
            response.read().decode("utf-8", errors="replace"),
        )


def _post_json(base: str, path: str, payload: dict) -> tuple[int, dict]:
    request = Request(
        base + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    try:
        pages = {
            "/": "法规认证知识平台",
            "/catalog": "正式知识目录",
            "/changes": "变化待办",
            "/map": "法规认证地图",
            "/graph": "知识图谱",
            "/ontology": "知识本体",
            "/admin": "资料及模型配置",
            "/parse-review": "文档解析复核",
            "/sources": "来源台账",
            "/collection": "采集执行",
            "/ontology-governance": "知识本体",
            "/candidate-normalization": "候选知识",
            "/source-diff": "来源版本",
            "/source-impact": "影响",
            "/gma-path": "GMA",
            "/pipeline-readiness": "建设",
        }
        for path, marker in pages.items():
            status, content_type, body = _get(base, path)
            assert status == 200, (path, status)
            assert "text/html" in content_type, (path, content_type)
            assert marker in body, (path, marker)

        api_paths = (
            "/api/health",
            "/api/business/dashboard",
            "/api/regions",
            "/api/map/overview",
            "/api/ontology-source/readiness",
            "/api/collection/profiles",
            "/api/collection/updates",
            "/api/ontology/versions",
            "/api/candidate-normalization/pending",
            "/api/gma/path?region=DE&product_class=%E5%AE%B6%E7%94%A8%E7%94%B5%E5%99%A8",
        )
        for path in api_paths:
            status, content_type, body = _get(base, path)
            assert status == 200, (path, status, body[:300])
            assert "application/json" in content_type, (path, content_type)
            payload = json.loads(body)
            assert isinstance(payload, dict), (path, type(payload))

        status, access = _post_json(
            base,
            "/api/gma/access",
            {
                "product": "家用电器",
                "product_class": "家用电器",
                "region_code": "DE",
            },
        )
        assert status == 200
        assert access["region_code"] == "DE"
        assert "stages" in access
        assert "decision_boundary" in access

        _, _, dashboard_body = _get(base, "/api/business/dashboard")
        dashboard = json.loads(dashboard_body)
        assert dashboard["headline"]["target_markets"] == 21
        assert len(dashboard["knowledge_libraries"]) == 4

        _, _, readiness_body = _get(base, "/api/ontology-source/readiness")
        readiness = json.loads(readiness_body)
        assert readiness["target_markets"] == 21
        assert readiness["checks"]["target_markets_21"] is True

    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    print("OK: stage29 integrated platform runtime smoke passed")


if __name__ == "__main__":
    main()
