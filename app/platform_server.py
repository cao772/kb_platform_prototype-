from __future__ import annotations

import base64
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from app.map_service import RegulationMapService
from app.ontology_governance import OntologyGovernanceService
from app.ontology_registry import OntologyRegistryService
from app.regions import target_market_catalog
from app.source_registry import SourceRegistryService
from app.source_verification import SourceVerificationService
from app.source_collection import SourceCollectionService
from app.site_extraction import SiteExtractionService
from app.source_diff import SourceDifferenceService
from app.source_impact import SourceImpactService
from app.document_translation import DocumentTranslationService
from app.formal_translation import FormalKnowledgeTranslationService
from app.candidate_normalization import CandidateNormalizationService
from app.gma_bilingual import GmaBilingualService
from app.pipeline_readiness import PipelineReadinessService
from app.server import Handler as BaseHandler
from app.server import ROOT, STATIC_DIR, change_monitor, graph_service, store

regulation_map = RegulationMapService(store)
ontology_governance = OntologyGovernanceService(store)
ontology_registry = OntologyRegistryService(store)
source_registry = SourceRegistryService(store)
source_verification = SourceVerificationService(source_registry)
source_collection = SourceCollectionService(store, source_registry, ROOT / "data" / "source_downloads", change_monitor=change_monitor)
site_extraction = SiteExtractionService(store, source_registry, ROOT / "data" / "source_downloads")
source_diff = SourceDifferenceService(store)
source_impact = SourceImpactService(store, source_diff, graph_service)
document_translation = DocumentTranslationService(store)
formal_translation = FormalKnowledgeTranslationService(store)
candidate_normalization = CandidateNormalizationService(store, ontology_registry)
gma_bilingual = GmaBilingualService(store, graph_service, formal_translation)
pipeline_readiness = PipelineReadinessService(store, ontology_registry, source_registry, source_collection, graph_service)


class Handler(BaseHandler):
    """Current platform handler with business map and ontology governance endpoints."""

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/api/regions":
            self._send_json(target_market_catalog())
            return

        if parsed.path == "/api/sources":
            try:
                self._send_json(source_registry.list_sources(
                    region_code=params.get("region", [""])[0],
                    source_type=params.get("type", [""])[0],
                    status=params.get("status", [""])[0],
                    harvestability=params.get("harvestability", [""])[0],
                    verification_status=params.get("verification_status", [""])[0],
                    q=params.get("q", [""])[0],
                    limit=min(int(params.get("limit", ["1000"])[0] or 1000), 3000),
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/sources/detail":
            try:
                item = source_registry.detail(int(params.get("id", ["0"])[0] or 0))
                item["collection_profiles"] = source_collection.list_profiles(
                    source_key=item.get("source_key", "")
                ).get("items", [])
                self._send_json(item)
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/sources/verification-summary":
            try:
                self._send_json(source_registry.verification_summary())
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/sources/export":
            try:
                content = source_registry.export_xlsx(
                    template_only=str(params.get("template", [""])[0]).lower() in {"1", "true", "yes", "on"}
                )
                filename = "source_registry_template.xlsx" if str(params.get("template", [""])[0]).lower() in {"1", "true", "yes", "on"} else "source_registry.xlsx"
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/collection/profiles":
            try:
                self._send_json(source_collection.list_profiles(
                    source_key=params.get("source_key", [""])[0],
                    enabled_only=str(params.get("enabled", [""])[0]).lower() in {"1","true","yes","on"},
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/collection/first-wave":
            try:
                self._send_json(source_collection.first_wave_profiles())
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/collection/site-plans":
            try:
                self._send_json(site_extraction.list_plans(
                    source_key=params.get("source_key", [""])[0]
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/collection/site-items":
            try:
                self._send_json(site_extraction.list_items(
                    source_key=params.get("source_key", [""])[0],
                    limit=min(int(params.get("limit", ["200"])[0] or 200), 2000),
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/collection/site-runs":
            try:
                self._send_json(site_extraction.list_runs(
                    source_key=params.get("source_key", [""])[0],
                    limit=min(int(params.get("limit", ["100"])[0] or 100), 500),
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/collection/site-relevance-overview":
            try:
                self._send_json(site_extraction.relevance_overview(
                    source_key=params.get("source_key", [""])[0]
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/collection/runs":
            try:
                self._send_json(source_collection.list_runs(
                    limit=min(int(params.get("limit", ["100"])[0] or 100), 500)
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/collection/updates":
            try:
                self._send_json(source_collection.list_updates(
                    status=params.get("status", [""])[0],
                    limit=min(int(params.get("limit", ["100"])[0] or 100), 500),
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/collection/diff":
            try:
                self._send_json(source_diff.analyze(
                    int(params.get("event_id", ["0"])[0] or 0),
                    refresh=str(params.get("refresh", [""])[0]).lower() in {"1","true","yes","on"},
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/collection/diff-reports":
            try:
                self._send_json(source_diff.list_reports(
                    limit=min(int(params.get("limit", ["100"])[0] or 100), 500)
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/collection/diff-review":
            try:
                self._send_json(source_diff.review_state(
                    int(params.get("event_id", ["0"])[0] or 0)
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/collection/impact-matches":
            try:
                self._send_json(source_impact.match_formal_records(
                    int(params.get("event_id", ["0"])[0] or 0),
                    limit=min(int(params.get("limit", ["50"])[0] or 50), 200),
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/collection/impact-case":
            try:
                self._send_json(source_impact.case(
                    int(params.get("event_id", ["0"])[0] or 0)
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/collection/impact-cases":
            try:
                self._send_json(source_impact.list_cases(
                    status=params.get("status", [""])[0],
                    limit=min(int(params.get("limit", ["100"])[0] or 100), 500),
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/source-document":
            try:
                self._send_json(document_translation.detail(
                    int(params.get("id", ["0"])[0] or 0),
                    target_language=params.get("lang", ["zh-CN"])[0] or "zh-CN",
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/source-document/raw":
            try:
                self._send_file(document_translation.raw_path(
                    int(params.get("id", ["0"])[0] or 0)
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/formal-translation":
            try:
                self._send_json(formal_translation.detail(
                    int(params.get("id", ["0"])[0] or 0),
                    target_language=params.get("lang", ["zh-CN"])[0] or "zh-CN",
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/candidate-normalization":
            try:
                self._send_json(candidate_normalization.suggestions(
                    int(params.get("task_id", ["0"])[0] or 0)
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/candidate-normalization/pending":
            try:
                self._send_json(candidate_normalization.list_pending(
                    limit=min(int(params.get("limit", ["100"])[0] or 100), 500)
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/ontology-source/readiness":
            try:
                self._send_json(pipeline_readiness.summary())
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/gma/path":
            try:
                self._send_json(gma_bilingual.path(
                    region_code=params.get("region", [""])[0],
                    product_class=params.get("product_class", [""])[0],
                    as_of=params.get("as_of", [None])[0] or None,
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
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

        if parsed.path == "/api/ontology/versions":
            self._send_json(ontology_registry.list_versions())
            return

        if parsed.path == "/api/ontology/terms":
            try:
                self._send_json(ontology_registry.list_terms(
                    term_type=params.get("type", [""])[0],
                    status=params.get("status", [""])[0],
                    q=params.get("q", [""])[0],
                    limit=min(int(params.get("limit", ["500"])[0] or 500), 2000),
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/ontology/normalize":
            try:
                self._send_json(ontology_registry.normalize(
                    params.get("q", [""])[0],
                    term_type=params.get("type", [""])[0],
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

        if parsed.path in {"/ontology-governance", "/ontology-governance.html"}:
            self._send_file(STATIC_DIR / "ontology_governance.html")
            return

        if parsed.path in {"/pipeline-readiness", "/pipeline-readiness.html"}:
            self._send_file(STATIC_DIR / "pipeline_readiness.html")
            return

        if parsed.path in {"/gma-path", "/gma-path.html"}:
            self._send_file(STATIC_DIR / "gma_path.html")
            return

        if parsed.path in {"/candidate-normalization", "/candidate-normalization.html"}:
            self._send_file(STATIC_DIR / "candidate_normalization.html")
            return

        if parsed.path in {"/source-document", "/source-document.html"}:
            self._send_file(STATIC_DIR / "source_document.html")
            return

        if parsed.path in {"/source-impact", "/source-impact.html"}:
            self._send_file(STATIC_DIR / "source_impact.html")
            return

        if parsed.path in {"/source-diff", "/source-diff.html"}:
            self._send_file(STATIC_DIR / "source_diff.html")
            return

        if parsed.path in {"/collection", "/collection.html"}:
            self._send_file(STATIC_DIR / "collection.html")
            return

        if parsed.path in {"/sources", "/sources.html"}:
            self._send_file(STATIC_DIR / "sources.html")
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
                '<a class="top-link" href="/sources">来源台账</a>'
                '<a class="top-link" href="/collection">采集执行</a>'
                '<a class="top-link" href="/ontology">知识本体</a>'
                '<a class="top-link" href="/gma-path">GMA准入路径</a>'
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
        if parsed.path == "/api/collection/diff-review":
            try:
                payload = self._read_json()
                self._send_json(source_diff.save_review(
                    int(payload.get("event_id") or 0),
                    items=payload.get("items") or [],
                    action=payload.get("action", "save"),
                    operator=payload.get("operator", ""),
                    note=payload.get("note", ""),
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/collection/diff-translate":
            try:
                payload = self._read_json()
                self._send_json(source_diff.translate_review_items(
                    int(payload.get("event_id") or 0),
                    items=payload.get("items"),
                    target_language=payload.get("target_language", "zh-CN"),
                    force=bool(payload.get("force", False)),
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/gma/path/translate":
            try:
                payload = self._read_json()
                self._send_json(gma_bilingual.translate_path(
                    region_code=payload.get("region_code", ""),
                    product_class=payload.get("product_class", ""),
                    as_of=payload.get("as_of") or None,
                    force=bool(payload.get("force", False)),
                    max_records=int(payload.get("max_records") or 100),
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/candidate-normalization/apply":
            try:
                payload = self._read_json()
                self._send_json(candidate_normalization.apply(
                    int(payload.get("task_id") or 0),
                    changes=payload.get("changes") or {},
                    operator=payload.get("operator", ""),
                    note=payload.get("note", ""),
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/formal-translation/translate":
            try:
                payload = self._read_json()
                self._send_json(formal_translation.translate(
                    int(payload.get("record_id") or 0),
                    target_language=payload.get("target_language", "zh-CN"),
                    force=bool(payload.get("force", False)),
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/formal-translation/review":
            try:
                payload = self._read_json()
                self._send_json(formal_translation.save_review(
                    int(payload.get("record_id") or 0),
                    target_language=payload.get("target_language", "zh-CN"),
                    translations=payload.get("translations") or {},
                    action=payload.get("action", "save"),
                    operator=payload.get("operator", ""),
                    note=payload.get("note", ""),
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/source-document/translate":
            try:
                payload = self._read_json()
                self._send_json(document_translation.translate(
                    int(payload.get("document_id") or 0),
                    target_language=payload.get("target_language", "zh-CN"),
                    force=bool(payload.get("force", False)),
                    max_segments=int(payload.get("max_segments") or 500),
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/source-document/review":
            try:
                payload = self._read_json()
                self._send_json(document_translation.save_review(
                    int(payload.get("document_id") or 0),
                    target_language=payload.get("target_language", "zh-CN"),
                    segments=payload.get("segments") or [],
                    action=payload.get("action", "save"),
                    operator=payload.get("operator", ""),
                    note=payload.get("note", ""),
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/collection/impact-build":
            try:
                payload = self._read_json()
                self._send_json(source_impact.build_case(
                    int(payload.get("event_id") or 0),
                    record_id=int(payload.get("record_id") or 0),
                    refresh=bool(payload.get("refresh", False)),
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/collection/impact-review":
            try:
                payload = self._read_json()
                self._send_json(source_impact.save_case(
                    int(payload.get("event_id") or 0),
                    items=payload.get("items") or [],
                    action=payload.get("action", "save"),
                    operator=payload.get("operator", ""),
                    note=payload.get("note", ""),
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/ontology/version/create":
            try:
                payload = self._read_json()
                self._send_json(ontology_registry.create_version(
                    version_code=payload.get("version_code", ""),
                    change_note=payload.get("change_note", ""),
                    created_by=payload.get("created_by", ""),
                    source_version_id=int(payload.get("source_version_id") or 0) or None,
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/ontology/version/activate":
            try:
                payload = self._read_json()
                self._send_json(ontology_registry.activate_version(
                    int(payload.get("id") or 0),
                    operator=payload.get("operator", ""),
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/ontology/terms/upsert":
            try:
                self._send_json(ontology_registry.upsert_term(self._read_json()))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/collection/profile-create":
            try:
                self._send_json(source_collection.create_profile(self._read_json()), HTTPStatus.CREATED)
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/collection/first-wave/run":
            try:
                payload = self._read_json()
                self._send_json(source_collection.run_first_wave_batch(
                    offset=int(payload.get("offset") or 0),
                    limit=int(payload.get("limit") or 5),
                    auto_ingest=bool(payload.get("auto_ingest", True)),
                    auto_extract=bool(payload.get("auto_extract", False)),
                    use_model=bool(payload.get("use_model", False)),
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/collection/site-plan/update":
            try:
                payload = self._read_json()
                self._send_json(site_extraction.update_plan(
                    str(payload.get("source_key") or ""),
                    config=payload.get("config") or {},
                    enabled=bool(payload.get("enabled", True)),
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/collection/site-crawl/run":
            try:
                payload = self._read_json()
                self._send_json(site_extraction.run(
                    str(payload.get("source_key") or ""),
                    max_pages=int(payload.get("max_pages") or 0) or None,
                    max_items=int(payload.get("max_items") or 0) or None,
                    auto_ingest=bool(payload.get("auto_ingest", False)),
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/collection/site-item/review":
            try:
                payload = self._read_json()
                self._send_json(site_extraction.review_item(
                    int(payload.get("item_id") or 0),
                    status=str(payload.get("status") or ""),
                    operator=str(payload.get("operator") or ""),
                    note=str(payload.get("note") or ""),
                    auto_ingest=bool(payload.get("auto_ingest", False)),
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/collection/run":
            try:
                payload = self._read_json()
                result = source_collection.run(
                    int(payload.get("profile_id") or 0),
                    auto_ingest=bool(payload.get("auto_ingest", True)),
                    auto_extract=bool(payload.get("auto_extract", False)),
                    use_model=bool(payload.get("use_model", False)),
                )
                self._send_json(result)
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/sources/create":
            try:
                self._send_json(source_registry.create(self._read_json()), HTTPStatus.CREATED)
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/sources/verify":
            try:
                payload = self._read_json()
                source_ids = payload.get("source_ids")
                if source_ids is None:
                    source_ids = [int(payload.get("id") or 0)]
                self._send_json(source_verification.verify_many(
                    source_ids,
                    timeout=max(3, min(int(payload.get("timeout") or 12), 30)),
                    max_workers=max(1, min(int(payload.get("max_workers") or 6), 8)),
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/sources/update":
            try:
                payload = self._read_json()
                source_id = int(payload.pop("id", 0) or 0)
                self._send_json(source_registry.update(source_id, payload))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/sources/archive":
            try:
                payload = self._read_json()
                self._send_json(source_registry.archive(
                    int(payload.get("id") or 0),
                    note=str(payload.get("note") or "来源已归档"),
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/sources/batch":
            try:
                payload = self._read_json()
                self._send_json(source_registry.batch_update(
                    payload.get("source_ids") or [],
                    payload,
                ))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if parsed.path == "/api/sources/import":
            try:
                payload = self._read_json()
                encoded = str(payload.get("content_base64") or "")
                if "," in encoded:
                    encoded = encoded.split(",", 1)[1]
                content = base64.b64decode(encoded, validate=True)
                self._send_json(source_registry.import_xlsx(content))
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

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
    print("Source registry: http://127.0.0.1:8765/sources")
    print("Source collection: http://127.0.0.1:8765/collection")
    print("Knowledge ontology: http://127.0.0.1:8765/ontology")
    print("Regulation certification map: http://127.0.0.1:8765/map")
    print("Formal knowledge catalog: http://127.0.0.1:8765/catalog")
    print("Regulation change & todo center: http://127.0.0.1:8765/changes")
    print("Document & model management: http://127.0.0.1:8765/admin")
    server.serve_forever()


if __name__ == "__main__":
    main()
