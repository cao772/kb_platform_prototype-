from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from app.compliance import TYPE_LABELS
from app.graph_backend import GraphBackendRouter
from app.query_router import QueryPlan
from app.store import KnowledgeStore

RELATION_LABELS = {
    "REFERENCES": "引用",
    "REQUIRES": "要求",
    "REPLACED_BY": "被替代",
}


class GovernedGraphContextProvider:
    """Build compact GraphRAG evidence from approved nodes and approved relations only."""

    def __init__(self, store: KnowledgeStore, backend: GraphBackendRouter):
        self.store = store
        self.backend = backend

    def _infer_product_class(self, question: str, region_code: str) -> str:
        records = self.store.list_compliance_records(review_status="approved", region_code=region_code or None, limit=2000)
        classes = sorted(
            {str(item.get("product_class") or "").strip() for item in records if str(item.get("product_class") or "").strip() not in {"", "*"}},
            key=len,
            reverse=True,
        )
        for product_class in classes:
            if product_class in question:
                return product_class
        return ""

    @staticmethod
    def _paths(nodes: dict[int, dict[str, Any]], edges: list[dict[str, Any]], *, max_paths: int = 12) -> list[dict[str, Any]]:
        adjacency: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for edge in edges:
            adjacency[int(edge["source"])].append(edge)
        starts = [nid for nid, item in nodes.items() if item.get("node_type") in {"regulation", "standard"}]
        goals = {"certification", "requirement", "test_item"}
        paths: list[dict[str, Any]] = []
        for start in starts:
            queue = deque([(start, [start], [])])
            while queue and len(paths) < max_paths:
                current, node_path, edge_path = queue.popleft()
                if len(edge_path) >= 5:
                    continue
                for edge in adjacency.get(current, []):
                    target = int(edge["target"])
                    if target in node_path or target not in nodes:
                        continue
                    next_nodes = node_path + [target]
                    next_edges = edge_path + [edge]
                    if nodes[target].get("node_type") in goals:
                        paths.append({"node_ids": next_nodes, "edges": next_edges})
                    queue.append((target, next_nodes, next_edges))
        return paths

    def build(self, question: str, plan: QueryPlan, *, as_of: str | None = None) -> dict[str, Any]:
        if not plan.needs_graph:
            return {
                "requested": False,
                "status": "not_needed",
                "facts": [],
                "paths": [],
                "citations": [],
                "backend": {"backend": "not_used", "fallback": False},
            }

        region_code = plan.regions[0] if len(plan.regions) == 1 else ""
        product_class = self._infer_product_class(question, region_code)
        graph, backend_trace = self.backend.projection(region_code=region_code, product_class=product_class, as_of=as_of)
        nodes = {int(item["id"]): dict(item) for item in graph.get("nodes", [])}
        edges = list(graph.get("edges", []))

        approved_records = {
            int(item["id"]): item
            for item in self.store.list_compliance_records(review_status="approved", region_code=region_code or None, limit=5000)
        }
        facts = []
        used_nodes: set[int] = set()
        for edge in edges[:24]:
            source_id, target_id = int(edge["source"]), int(edge["target"])
            source, target = nodes.get(source_id), nodes.get(target_id)
            if not source or not target:
                continue
            used_nodes.update({source_id, target_id})
            relation = str(edge.get("relation_type") or "")
            facts.append({
                "source_id": source_id,
                "source_type": source.get("node_type", ""),
                "source": source.get("label") or source.get("name") or source.get("code") or str(source_id),
                "relation_type": relation,
                "relation_label": RELATION_LABELS.get(relation, relation),
                "target_id": target_id,
                "target_type": target.get("node_type", ""),
                "target": target.get("label") or target.get("name") or target.get("code") or str(target_id),
                "confidence": edge.get("confidence", 0),
            })

        raw_paths = self._paths(nodes, edges)
        paths = []
        for path in raw_paths:
            path_nodes = [nodes[nid] for nid in path["node_ids"] if nid in nodes]
            used_nodes.update(path["node_ids"])
            paths.append({
                "node_ids": path["node_ids"],
                "labels": [item.get("label") or item.get("name") or item.get("code") or str(item.get("id")) for item in path_nodes],
                "types": [item.get("node_type", "") for item in path_nodes],
                "relations": [RELATION_LABELS.get(str(edge.get("relation_type") or ""), str(edge.get("relation_type") or "")) for edge in path["edges"]],
            })

        citations = []
        seen_evidence: set[tuple[int, int]] = set()
        for node_id in sorted(used_nodes):
            record = approved_records.get(node_id, {})
            document_id = record.get("source_document_id")
            chunk_id = record.get("source_chunk_id")
            if not document_id or not chunk_id:
                continue
            key = (int(document_id), int(chunk_id))
            if key in seen_evidence:
                continue
            seen_evidence.add(key)
            citations.append({
                "evidence_id": f"doc-{document_id}-chunk-{chunk_id}",
                "title": record.get("name") or record.get("source_filename") or "已审核图谱证据",
                "filename": record.get("source_filename") or "",
                "chunk": record.get("source_chunk_index"),
                "score": 1.0,
                "channels": ["approved_graph"],
            })

        if facts:
            status = "governed_graph_connected"
        elif nodes:
            status = "approved_nodes_no_relations"
        else:
            status = "approved_graph_empty"
        return {
            "requested": True,
            "status": status,
            "region_code": region_code,
            "product_class": product_class,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "facts": facts,
            "paths": paths,
            "citations": citations,
            "backend": backend_trace,
            "note": "GraphRAG only consumes human-approved catalog records and human-approved relations. Candidate relations are excluded.",
        }


def graph_context_as_prompt(context: dict[str, Any]) -> str:
    if not context.get("facts"):
        return ""
    lines = []
    for index, fact in enumerate(context["facts"][:16], 1):
        source_type = TYPE_LABELS.get(fact.get("source_type", ""), fact.get("source_type", ""))
        target_type = TYPE_LABELS.get(fact.get("target_type", ""), fact.get("target_type", ""))
        lines.append(
            f"[图谱事实{index}] {source_type}《{fact['source']}》 --{fact['relation_label']}--> {target_type}《{fact['target']}》"
        )
    if context.get("paths"):
        lines.append("认证/要求路径：")
        for index, path in enumerate(context["paths"][:8], 1):
            labels = path.get("labels", [])
            relations = path.get("relations", [])
            parts = []
            for pos, label in enumerate(labels):
                parts.append(label)
                if pos < len(relations):
                    parts.append(f" --{relations[pos]}--> ")
            lines.append(f"[路径{index}] {''.join(parts)}")
    return "\n".join(lines)
