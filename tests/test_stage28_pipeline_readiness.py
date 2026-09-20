from __future__ import annotations

import tempfile
from pathlib import Path

from app.candidate_normalization import CandidateNormalizationService
from app.document_translation import DocumentTranslationService
from app.formal_translation import FormalKnowledgeTranslationService
from app.graph_governance import GraphGovernanceService
from app.ontology_registry import OntologyRegistryService
from app.pipeline_readiness import PipelineReadinessService
from app.source_collection import SourceCollectionService
from app.source_diff import SourceDifferenceService
from app.source_impact import SourceImpactService
from app.source_registry import SourceRegistryService
from app.store import KnowledgeStore


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        store = KnowledgeStore(root / "all_services.db")
        ontology_registry = OntologyRegistryService(store)
        source_registry = SourceRegistryService(store)
        graph = GraphGovernanceService(store)
        source_collection = SourceCollectionService(
            store, source_registry, root / "downloads"
        )
        source_diff = SourceDifferenceService(store)
        formal_translation = FormalKnowledgeTranslationService(store)
        document_translation = DocumentTranslationService(store)
        SourceImpactService(store, source_diff, graph)
        CandidateNormalizationService(store, ontology_registry)

        readiness = PipelineReadinessService(
            store,
            ontology_registry,
            source_registry,
            source_collection,
            graph,
        ).summary()
        assert readiness["status"] == "ready"
        assert readiness["target_markets"] == 21
        assert readiness["checks"]["target_markets_21"] is True
        assert readiness["checks"]["ontology_active"] is True
        assert readiness["checks"]["sources_registered"] is True
        assert readiness["checks"]["collection_profiles_ready"] is True
        assert readiness["checks"]["graph_constraints_clean"] is True
        assert readiness["sources"]["registered"] >= 40
        assert readiness["sources"]["profiles"] >= 5

        # Ensure all key governance tables coexist in one database.
        expected_tables = {
            "knowledge_sources",
            "collection_profiles",
            "collection_runs",
            "source_snapshots",
            "source_update_events",
            "source_diff_reports",
            "source_diff_reviews",
            "source_impact_cases",
            "ontology_versions",
            "ontology_terms",
            "candidate_normalization_log",
            "formal_knowledge_translations",
            "document_translation_segments",
            "document_translation_reviews",
            "graph_relations",
        }
        with store.lock:
            rows = store.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        actual = {row["name"] for row in rows}
        assert expected_tables <= actual

        # Silence unused local warning while ensuring service construction succeeds.
        assert document_translation is not None
        assert formal_translation is not None

    runtime = Path("app/platform_server.py").read_text(encoding="utf-8")
    readiness_page = Path("static/pipeline_readiness.html").read_text(encoding="utf-8")
    gma_page = Path("static/gma_path.html").read_text(encoding="utf-8")
    normalize_page = Path("static/candidate_normalization.html").read_text(encoding="utf-8")

    assert '"/api/ontology-source/readiness"' in runtime
    assert '"/pipeline-readiness", "/pipeline-readiness.html"' in runtime
    assert '"/gma-path", "/gma-path.html"' in runtime
    assert '"/candidate-normalization", "/candidate-normalization.html"' in runtime
    assert "稳定业务边界" in readiness_page
    assert "产品市场准入知识路径" in gma_page
    assert "候选知识术语归一与人工复核" in normalize_page

    print("OK: stage28 ontology-source pipeline integration readiness passed")


if __name__ == "__main__":
    main()
