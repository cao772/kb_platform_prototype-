from __future__ import annotations

import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.agent import KnowledgeAgent
from app.architecture import current_architecture
from app.demo_graph import build_world_certification_demo_graph
from app.governance import ALLOWED_STATUSES, analyze_product_access_v2, review_task_with_edits, world_map_v2
from app.graph_backend import GraphBackendRouter
from app.graph_governance import GraphGovernanceService
from app.ingest import ingest_directory, ingest_file
from app.model_gateway import build_rag_prompt, current_model_config, test_model_connection
from app.ontology import ontology_schema
from app.processing import DocumentProcessingService
from app.query_router import QueryRouter
from app.retrieval import RetrievalPipeline
from app.runtime_settings import RuntimeSettingsStore
from app.store import KnowledgeStore
from app.structured_extraction import extract_review_candidates_v2
from app.upload import ALLOWED_SUFFIXES, DEFAULT_MAX_BYTES, save_browser_upload

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "knowledge.db"
SOURCE_DIR = ROOT.parent
UPLOAD_DIR = ROOT / "data" / "uploads"
STATIC_DIR = ROOT / "static"
SETTINGS_PATH = ROOT / "data" / "runtime_settings.json"
PROCESSING_TASK_PATH = ROOT / "data" / "processing_tasks.json"

store = KnowledgeStore(DB_PATH)
router = QueryRouter()
retrieval = RetrievalPipeline(store)
graph_service = GraphGovernanceService(store)
graph_backend = GraphBackendRouter(graph_service)
agent = KnowledgeAgent(store, graph_service=graph_service, graph_backend=graph_backend)
runtime_settings = RuntimeSettingsStore(SETTINGS_PATH)
processing = DocumentProcessingService(store, UPLOAD_DIR, PROCESSING_TASK_PATH)


def json_bytes(payload: object) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def as_bool(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").lower() in {"1", "true", "yes", "on"}


def public_model_config(purpose: str) -> dict:
    config = current_model_config(purpose)
    return {
        "purpose": purpose,
        "provider": config.provider,
        "base_url": config.base_url,
        "model": config.model,
        "enabled": config.enabled,
        "configured": config.configured,
        "timeout_seconds": config.timeout_seconds,
        "api_key_set": bool(config.api_key),
    }


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, payload: object, status: int = 200) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self, *, max_bytes: int | None = None) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if max_bytes is not None and length > max_bytes:
            raise ValueError(f"request body too large: {length} bytes")
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/api/health":
            self._send_json({
                "ok": True,
                "stats": store.stats(),
                "graph": graph_service.summary(),
                "graph_backend": graph_backend.status(ping=False),
                "processing": {"tasks": len(processing.tasks.list(limit=300))},
                "version": "2026.09-business-admin-v6",
            })
            return
        if parsed.path == "/api/capabilities":
            extraction = current_model_config("extraction")
            vision = current_model_config("vision")
            qa = current_model_config("qa")
            self._send_json({
                "upload": {"suffixes": sorted(ALLOWED_SUFFIXES), "max_bytes": DEFAULT_MAX_BYTES, "browser_upload": True},
                "document_processing": {"task_tracking": True, "structured_parse": True, "vision_ocr": vision.configured},
                "knowledge_extraction": {"rule": True, "model_optional": True, "model_configured": extraction.configured},
                "question_answering": {"model_optional": True, "model_configured": qa.configured},
                "governance": {"editable_review": True, "lifecycle_statuses": sorted(ALLOWED_STATUSES), "evidence_required": True},
                "knowledge_graph": {
                    "relation_review": True,
                    "version_chain": True,
                    "certification_path": True,
                    "impact_analysis": True,
                    "graphrag_in_agent": True,
                    "formal_graph_uses_approved_relations_only": True,
                    "backend": graph_backend.status(ping=False),
                },
            })
            return
        if parsed.path == "/api/admin/settings":
            self._send_json(runtime_settings.public())
            return
        if parsed.path == "/api/processing/tasks":
            limit = min(int(params.get("limit", ["100"])[0] or 100), 300)
            self._send_json({"items": processing.tasks.list(limit=limit)})
            return
        if parsed.path == "/api/processing/task":
            task_id = params.get("id", [""])[0]
            item = processing.tasks.get(task_id)
            if not item:
                self._send_json({"error": "processing task not found"}, HTTPStatus.NOT_FOUND)
            else:
                self._send_json(item)
            return
        if parsed.path == "/api/architecture":
            self._send_json(current_architecture())
            return
        if parsed.path == "/api/ontology":
            self._send_json(ontology_schema())
            return
        if parsed.path == "/api/query-plan":
            self._send_json(router.plan(params.get("q", [""])[0]).to_dict())
            return
        if parsed.path == "/api/documents":
            self._send_json({"items": store.list_documents()})
            return
        if parsed.path == "/api/ingestion-events":
            self._send_json({"items": store.recent_ingestion_events()})
            return
        if parsed.path == "/api/model-config":
            self._send_json({
                "models": {purpose: public_model_config(purpose) for purpose in ("extraction", "qa", "vision")},
                "sample_prompt": build_rag_prompt("某产品出口欧盟需要哪些法规和认证依据？", [], query_plan={"route": "compliance_path"}),
            })
            return
        if parsed.path == "/api/search":
            query = params.get("q", [""])[0]
            knowledge_type = params.get("type", [None])[0] or None
            plan = router.plan(query)
            items, trace = retrieval.retrieve(query, plan=plan, knowledge_type=knowledge_type)
            self._send_json({"query": query, "query_plan": plan.to_dict(), "trace": trace, "items": items})
            return
        if parsed.path == "/api/compliance/map":
            try:
                self._send_json(world_map_v2(
                    store,
                    include_demo=as_bool(params.get("include_demo", [""])[0]),
                    as_of=params.get("as_of", [None])[0] or None,
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/compliance/records":
            self._send_json({"items": store.list_compliance_records(
                region_code=params.get("region", [None])[0] or None,
                product_class=params.get("product_class", [None])[0] or None,
            )})
            return
        if parsed.path == "/api/extraction-tasks":
            self._send_json({"items": store.list_extraction_tasks(status=params.get("status", [None])[0] or None)})
            return
        if parsed.path == "/api/graph/summary":
            self._send_json(graph_service.summary())
            return
        if parsed.path == "/api/graph/backend":
            self._send_json(graph_backend.status(ping=as_bool(params.get("ping", [""])[0])))
            return
        if parsed.path == "/api/graph/relations":
            self._send_json({"items": graph_service.list_relations(status=params.get("status", [None])[0] or None)})
            return
        if parsed.path == "/api/graph/project":
            try:
                graph, backend_trace = graph_backend.projection(
                    region_code=params.get("region", [""])[0],
                    product_class=params.get("product_class", [""])[0],
                    as_of=params.get("as_of", [None])[0] or None,
                )
                self._send_json({**graph, "backend_trace": backend_trace})
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/graph/demo":
            graph = build_world_certification_demo_graph()
            self._send_json({"demo_data": True, "warning": "仅用于展示关系结构。", "items": graph.reachable_requirements("product:demo_appliance", max_depth=5)})
            return
        if parsed.path in {"/admin", "/admin.html"}:
            self._send_file(STATIC_DIR / "admin.html")
            return
        if parsed.path in {"/graph", "/graph.html"}:
            self._send_file(STATIC_DIR / "graph.html")
            return
        if parsed.path in {"/", "/index.html"}:
            self._send_file(STATIC_DIR / "index.html")
            return
        self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/api/admin/settings":
            try:
                self._send_json(runtime_settings.save(self._read_json()))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/admin/model-test":
            try:
                purpose = str(self._read_json().get("purpose") or "")
                if purpose not in {"extraction", "qa", "vision"}:
                    raise ValueError("unsupported model purpose")
                self._send_json(test_model_connection(purpose))
            except Exception as exc:
                self._send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/processing/start":
            try:
                payload = self._read_json(max_bytes=40 * 1024 * 1024)
                task = processing.start_browser_upload(payload)
                self._send_json({"ok": True, "task": task})
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if parsed.path == "/api/ingest":
            ids = ingest_directory(store, SOURCE_DIR)
            self._send_json({"ok": True, "document_ids": ids, "stats": store.stats()})
            return
        if parsed.path == "/api/reset":
            graph_service.reset()
            store.reset()
            self._send_json({"ok": True, "stats": store.stats(), "graph": graph_service.summary()})
            return
        if parsed.path in {"/api/answer", "/api/answer-v2"}:
            payload = self._read_json()
            self._send_json(agent.answer(payload.get("question", ""), knowledge_type=payload.get("knowledge_type") or None))
            return
        if parsed.path == "/api/upload":
            try:
                payload = self._read_json(max_bytes=40 * 1024 * 1024)
                saved = save_browser_upload(filename=payload.get("filename", ""), content_base64=payload.get("content_base64", ""), upload_dir=UPLOAD_DIR)
                document_id = ingest_file(store, saved["path"])
                extraction = None
                if as_bool(payload.get("auto_extract", True)):
                    extraction = extract_review_candidates_v2(store, document_id, use_llm=as_bool(payload.get("use_llm", True)))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"ok": True, "upload": {"filename": saved["filename"], "size": saved["size"], "sha256": saved["sha256"]}, "document_id": document_id, "extraction": extraction, "stats": store.stats()})
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
                result = extract_review_candidates_v2(store, int(document_id), use_llm=as_bool(payload.get("use_llm", True)))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"ok": True, **result, "stats": store.stats()})
            return
        if parsed.path == "/api/extraction/review":
            payload = self._read_json()
            task_id = payload.get("task_id")
            if not task_id:
                self._send_json({"error": "task_id is required"}, HTTPStatus.BAD_REQUEST)
                return
            try:
                result = review_task_with_edits(store, int(task_id), action=payload.get("action", ""), edits=payload.get("edits") or {}, reviewer_note=payload.get("note", ""))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"ok": True, **result, "stats": store.stats()})
            return
        if parsed.path == "/api/compliance/access-check":
            payload = self._read_json()
            try:
                result = analyze_product_access_v2(
                    store,
                    product=payload.get("product", ""),
                    product_class=payload.get("product_class", ""),
                    region_code=payload.get("region_code", ""),
                    include_demo=as_bool(payload.get("include_demo", False)),
                    as_of=payload.get("as_of") or None,
                )
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json(result)
            return
        if parsed.path == "/api/graph/suggest":
            payload = self._read_json()
            try:
                result = graph_service.suggest_relations(region_code=payload.get("region_code", ""), product_class=payload.get("product_class", ""))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"ok": True, **result})
            return
        if parsed.path == "/api/graph/review":
            payload = self._read_json()
            try:
                result = graph_service.review_relation(int(payload.get("relation_id")), action=payload.get("action", ""), note=payload.get("note", ""))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"ok": True, **result, "summary": graph_service.summary()})
            return
        if parsed.path == "/api/graph/sync":
            try:
                result = graph_backend.sync()
            except Exception as exc:
                self._send_json({"error": str(exc), "error_type": type(exc).__name__}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"ok": True, **result, "backend": graph_backend.status(ping=False)})
            return
        if parsed.path == "/api/graph/path":
            payload = self._read_json()
            try:
                result = graph_service.certification_paths(region_code=payload.get("region_code", ""), product_class=payload.get("product_class", ""), as_of=payload.get("as_of") or None)
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json(result)
            return
        if parsed.path == "/api/graph/impact":
            payload = self._read_json()
            try:
                result = graph_service.impact_analysis(int(payload.get("record_id")))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json(result)
            return
        if parsed.path == "/api/regulation-self-check":
            payload = self._read_json()
            question = f"{payload.get('product', '')} 出口 {payload.get('region', '')} 的法规、认证、准入和风险要求是什么？"
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
    print("Knowledge platform running at http://127.0.0.1:8765")
    print("Document & model management: http://127.0.0.1:8765/admin")
    server.serve_forever()


if __name__ == "__main__":
    main()
