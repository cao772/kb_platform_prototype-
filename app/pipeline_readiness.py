from __future__ import annotations

from typing import Any

from app.graph_governance import GraphGovernanceService
from app.ontology_registry import OntologyRegistryService
from app.regions import TARGET_MARKETS
from app.source_collection import SourceCollectionService
from app.source_registry import SourceRegistryService
from app.store import KnowledgeStore


class PipelineReadinessService:
    """Operational readiness summary for the ontology/source pipeline."""

    def __init__(
        self,
        store: KnowledgeStore,
        ontology_registry: OntologyRegistryService,
        source_registry: SourceRegistryService,
        source_collection: SourceCollectionService,
        graph: GraphGovernanceService,
    ):
        self.store = store
        self.ontology_registry = ontology_registry
        self.source_registry = source_registry
        self.source_collection = source_collection
        self.graph = graph

    def _count(self, table: str, where: str = "", params: tuple[Any, ...] = ()) -> int:
        with self.store.lock:
            row = self.store.conn.execute(
                f"SELECT COUNT(*) count FROM {table} {where}",
                params,
            ).fetchone()
        return int(row["count"] if row else 0)

    def summary(self) -> dict[str, Any]:
        ontology = self.ontology_registry.list_versions()["summary"]
        terms = self.ontology_registry.list_terms(status="active", limit=2000)["summary"]
        sources = self.source_registry.list_sources(limit=3000)["summary"]
        profiles = self.source_collection.list_profiles()["summary"]
        graph = self.graph.summary()
        store_stats = self.store.stats()

        source_updates = self._count("source_update_events")
        changed_updates = self._count(
            "source_update_events", "WHERE change_type='changed'"
        )
        confirmed_diffs = self._count(
            "source_diff_reviews", "WHERE review_status='confirmed'"
        )
        confirmed_impacts = self._count(
            "source_impact_cases", "WHERE review_status='confirmed'"
        )
        formal_translations = self._count("formal_knowledge_translations")
        confirmed_formal_translations = self._count(
            "formal_knowledge_translations", "WHERE review_status='confirmed'"
        )
        document_translations = self._count("document_translation_segments")
        confirmed_document_reviews = self._count(
            "document_translation_reviews", "WHERE review_status='confirmed'"
        )
        normalization_logs = self._count("candidate_normalization_log")

        checks = {
            "target_markets_21": len(TARGET_MARKETS) == 21,
            "ontology_active": bool(ontology.get("active_version")),
            "sources_registered": int(sources.get("registered_sources") or 0) > 0,
            "collection_profiles_ready": int(profiles.get("enabled") or 0) > 0,
            "graph_constraints_clean": int(graph.get("invalid_approved") or 0) == 0,
        }
        return {
            "status": "ready" if all(checks.values()) else "needs_attention",
            "checks": checks,
            "target_markets": len(TARGET_MARKETS),
            "formal_knowledge": int(store_stats.get("compliance_records") or 0),
            "pending_human_review": int(store_stats.get("pending_review") or 0),
            "ontology": {
                "active_version": ontology.get("active_version", ""),
                "versions": ontology.get("versions", 0),
                "active_terms": terms.get("active", 0),
            },
            "sources": {
                "registered": sources.get("registered_sources", 0),
                "target_sources": sources.get("target_sources", 300),
                "profiles": profiles.get("profiles", 0),
                "enabled_profiles": profiles.get("enabled", 0),
                "update_events": source_updates,
                "changed_events": changed_updates,
            },
            "governance": {
                "confirmed_difference_reviews": confirmed_diffs,
                "confirmed_impact_reviews": confirmed_impacts,
                "candidate_normalization_logs": normalization_logs,
                "approved_graph_relations": graph.get("approved", 0),
                "invalid_approved_graph_relations": graph.get("invalid_approved", 0),
            },
            "bilingual": {
                "formal_translation_records": formal_translations,
                "confirmed_formal_translations": confirmed_formal_translations,
                "document_translation_segments": document_translations,
                "confirmed_document_translation_reviews": confirmed_document_reviews,
            },
            "boundaries": [
                "原始文件、原始解析文本和正式知识原字段不可被翻译覆盖。",
                "候选知识、候选关系、变化项和影响项均需人工复核，可修改后再确认。",
                "GMA为派生准入路径，不复制维护法规、标准和认证事实。",
                "欧盟成员国查询纳入国家知识与EU共享知识；非成员国不自动继承EU。",
            ],
        }
