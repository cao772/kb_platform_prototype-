from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass(frozen=True)
class Node:
    id: str
    label: str
    node_type: str
    properties: dict


@dataclass(frozen=True)
class Edge:
    source: str
    relation: str
    target: str
    weight: float = 1.0


class KnowledgeGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self.outgoing: dict[str, list[Edge]] = defaultdict(list)

    def add_node(self, node_id: str, label: str, node_type: str, **properties: object) -> None:
        self.nodes[node_id] = Node(node_id, label, node_type, properties)

    def add_edge(self, source: str, relation: str, target: str, weight: float = 1.0) -> None:
        edge = Edge(source, relation, target, weight)
        self.edges.append(edge)
        self.outgoing[source].append(edge)

    def reachable_requirements(self, product_id: str, *, max_depth: int = 4) -> list[dict]:
        queue = deque([(product_id, [], 0)])
        seen = {(product_id, 0)}
        results: list[dict] = []
        while queue:
            current, path, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for edge in self.outgoing.get(current, []):
                target = self.nodes[edge.target]
                next_path = path + [edge]
                if target.node_type in {"regulation", "standard", "certification", "requirement"}:
                    results.append({
                        "target_id": target.id,
                        "target_label": target.label,
                        "target_type": target.node_type,
                        "path": [f"{item.source} -[{item.relation}]-> {item.target}" for item in next_path],
                        "score": round(sum(item.weight for item in next_path) / len(next_path), 4),
                    })
                state = (edge.target, depth + 1)
                if state not in seen:
                    seen.add(state)
                    queue.append((edge.target, next_path, depth + 1))
        return sorted(results, key=lambda item: item["score"], reverse=True)
