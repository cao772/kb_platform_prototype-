from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from app.query_router import QueryPlan
from app.store import KnowledgeStore
from app.text_processing import cosine_score, tokenize


@dataclass(frozen=True)
class RetrievalConfig:
    keyword_top_k: int = 24
    semantic_top_k: int = 24
    final_top_k: int = 8
    rrf_k: int = 60


class RetrievalPipeline:
    """Hybrid candidate generation -> RRF fusion -> transparent reranking.

    The implementation is dependency-free for this prototype. In production the two
    channels map naturally to OpenSearch/Qdrant/pgvector and the rerank step can be
    replaced by a cross-encoder or late-interaction model.
    """

    def __init__(self, store: KnowledgeStore, config: RetrievalConfig | None = None):
        self.store = store
        self.config = config or RetrievalConfig()

    def retrieve(
        self,
        query: str,
        *,
        plan: QueryPlan,
        knowledge_type: str | None = None,
        limit: int | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        limit = limit or self.config.final_top_k
        keyword = self.store.keyword_search(query, limit=self.config.keyword_top_k, knowledge_type=knowledge_type)
        semantic = self.store.semantic_search(query, limit=self.config.semantic_top_k, knowledge_type=knowledge_type)

        by_id: dict[int, dict[str, Any]] = {}
        rrf_scores: dict[int, float] = defaultdict(float)
        channels: dict[int, list[str]] = defaultdict(list)

        for channel_name, rows in (("keyword", keyword), ("semantic", semantic)):
            for rank, item in enumerate(rows, start=1):
                chunk_id = int(item["chunk_id"])
                by_id.setdefault(chunk_id, dict(item))
                rrf_scores[chunk_id] += 1.0 / (self.config.rrf_k + rank)
                channels[chunk_id].append(channel_name)

        if not by_id:
            trace = {
                "strategy": "hybrid_rrf_rerank",
                "keyword_candidates": 0,
                "semantic_candidates": 0,
                "fused_candidates": 0,
                "route": plan.route,
            }
            return [], trace

        max_rrf = max(rrf_scores.values()) or 1.0
        query_tokens = tokenize(query)
        results: list[dict[str, Any]] = []
        for chunk_id, item in by_id.items():
            rrf_norm = rrf_scores[chunk_id] / max_rrf
            lexical = cosine_score(query_tokens, item.get("text", ""))
            title_overlap = cosine_score(query_tokens, item.get("title", ""))
            exact_bonus = 0.0
            compact_query = query.replace("？", "").replace("?", "").strip()
            if compact_query and compact_query in item.get("text", ""):
                exact_bonus = 0.15

            route_bonus = 0.0
            if plan.route == "compliance_path" and item.get("knowledge_type") == "法规知识":
                route_bonus += 0.08
            if plan.regions:
                metadata_text = str(item.get("metadata", {})) + item.get("text", "") + item.get("title", "")
                if any(region in metadata_text for region in plan.regions):
                    route_bonus += 0.06

            rerank_score = min(
                1.0,
                0.55 * rrf_norm + 0.25 * lexical + 0.12 * title_overlap + exact_bonus + route_bonus,
            )
            enriched = dict(item)
            enriched.update(
                {
                    "score": round(rerank_score, 4),
                    "rrf_score": round(rrf_norm, 4),
                    "rerank_score": round(rerank_score, 4),
                    "retrieval_channels": channels[chunk_id],
                }
            )
            results.append(enriched)

        results.sort(key=lambda row: row["score"], reverse=True)
        trace = {
            "strategy": "hybrid_rrf_rerank",
            "keyword_candidates": len(keyword),
            "semantic_candidates": len(semantic),
            "fused_candidates": len(results),
            "returned": min(limit, len(results)),
            "route": plan.route,
            "channels": list(plan.retrieval_channels),
        }
        return results[:limit], trace
