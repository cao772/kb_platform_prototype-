from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.agent import KnowledgeAgent
from app.architecture import current_architecture
from app.compliance import analyze_product_access, build_world_map, extract_review_candidates
from app.demo_graph import build_world_certification_demo_graph
from app.ingest import ingest_directory, ingest_file
from app.model_gateway import build_rag_prompt, current_model_config
from app.ontology import ontology_schema
from app.query_router import QueryRouter
from app.retrieval import RetrievalPipeline
from app.store import KnowledgeStore

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "knowledge.db"
SOURCE_DIR = ROOT.parent
STATIC_DIR = ROOT / "static"

store = KnowledgeStore(DB_PATH)
router = QueryRouter()
retrieval = RetrievalPipeline(store)
agent = KnowledgeAgent(store)


def json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def as_bool(value: str | None) -> bool:
    return str(value or "").lower() in {"1", "true", "yes", "on"}


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
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        if parsed.path == "/api/health":
            self._send_json({"ok": True, "stats": store.stats(), "version": "2026.09-compliance-governance-v1"})
            return
        if parsed.path == "/api/architecture":
            self._send_json(current_architecture())
            return
        if parsed.path == "/api/ontology":
            self._send_json(ontology_schema())
            return
        if parsed.path == "/api/query-plan":
            query = params.get("q", [""])[0]
            self._send_json(router.plan(query).to_dict())
            return
        if parsed.path == "/api/documents":
            self._send_json({"items": store.list_documents()})
            return
        if parsed.path == "/api/ingestion-events":
            self._send_json({"items": store.recent_ingestion_events()})
            return
        if parsed.path == "/api/model-config":
            config = current_model_config()
            self._send_json({"config": config.__dict__, "sample_prompt": build_rag_prompt("某产品出口欧盟需要哪些法规和认证依据？", [], query_plan={"route": "compliance_path"})})
            return
        if parsed.path == "/api/search":
            query = params.get("q", [""])[0]
            knowledge_type = params.get("type", [None])[0] or None
            plan = router.plan(query)
            items, trace = retrieval.retrieve(query, plan=plan, knowledge_type=knowledge_type)
            self._send_json({"query": query, "query_plan": plan.to_dict(), "trace": trace, "items": items})
            return
        if parsed.path == "/api/compliance/map":
            include_demo = as_bool(params.get("include_demo", [""])[0])
            self._send_json(build_world_map(store, include_demo=include_demo))
            return
        if parsed.path == "/api/compliance/records":
            region_code = params.get("region", [None])[0] or None
            product_class = params.get("product_class", [None])[0] or None
            self._send_json({"items": store.list_compliance_records(region_code=region_code, product_class=product_class)})
            return
        if parsed.path == "/api/extraction-tasks":
            status = params.get("status", [None])[0] or None
            self._send_json({"items": store.list_extraction_tasks(status=status)})
            return
        if parsed.path == "/api/graph/demo":
            graph = build_world_certification_demo_graph()
            self._send_json({"demo_data": True, "warning": "仅演示图谱遍历，不代表真实法规/认证要求。", "items": graph.reachable_requirements("product:demo_appliance", max_depth=5)})
            return
        if parsed.path in {"/", "/index.html"}:
            self._send_file(STATIC_DIR / "index.html")
            return
        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/ingest":
            ids = ingest_directory(store, SOURCE_DIR)
            self._send_json({"ok": True, "document_ids": ids, "stats": store.stats()})
            return
        if parsed.path == "/api/reset":
            store.reset()
            self._send_json({"ok": True, "stats": store.stats()})
            return
        if parsed.path in {"/api/answer", "/api/answer-v2"}:
            payload = self._read_json()
            self._send_json(agent.answer(payload.get("question", ""), knowledge_type=payload.get("knowledge_type") or None))
            return
        if parsed.path == "/api/upload-file":
            payload = self._read_json()
            path = payload.get("path")
            if not path:
                self._send_json({"error": "path is required"}, HTTPStatus.BAD_REQUEST)
                return
            try:
                document_id = ingest_file(store, path)
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"ok": True, "document_id": document_id, "stats": store.stats()})
            return
        if parsed.path == "/api/extraction/run":
            payload = self._read_json()
            document_id = payload.get("document_id")
            if not document_id:
                self._send_json({"error": "document_id is required"}, HTTPStatus.BAD_REQUEST)
                return
            try:
                result = extract_review_candidates(store, int(document_id))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"ok": True, **result, "stats": store.stats()})
            return
        if parsed.path == "/api/extraction/review":
            payload = self._read_json()
            task_id = payload.get("task_id")
            action = payload.get("action", "")
            if not task_id:
                self._send_json({"error": "task_id is required"}, HTTPStatus.BAD_REQUEST)
                return
            try:
                result = store.review_extraction_task(int(task_id), action=action, reviewer_note=payload.get("note", ""))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"ok": True, **result, "stats": store.stats()})
            return
        if parsed.path == "/api/compliance/access-check":
            payload = self._read_json()
            result = analyze_product_access(store, product=payload.get("product", ""), product_class=payload.get("product_class", ""), region_code=payload.get("region_code", ""), include_demo=bool(payload.get("include_demo", False)))
            self._send_json(result)
            return
        if parsed.path == "/api/regulation-self-check":
            payload = self._read_json()
            question = f"{payload.get('product', '')} 出口 {payload.get('region', '')} 的法规、认证、GMA、准入和风险要求是什么？"
            self._send_json(agent.answer(question, knowledge_type="法规知识"))
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
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("Knowledge platform prototype running at http://127.0.0.1:8765")
    server.serve_forever()


if __name__ == "__main__":
    main()
