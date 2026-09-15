from __future__ import annotations

import importlib.util
import os
from datetime import date
from typing import Any

from app.graph_governance import GraphGovernanceService

RELATION_TYPES = {"REFERENCES", "REQUIRES", "REPLACED_BY"}


class Neo4jGovernedGraph:
    """Optional Neo4j projection of the approved catalog graph.

    SQLite remains the governance/source-of-truth store in the prototype. Neo4j only
    receives approved nodes and approved relations, so candidate relations can never
    leak into GraphRAG by bypassing the review workflow.
    """

    def __init__(self) -> None:
        self.uri = os.getenv("KB_NEO4J_URI", "").strip()
        self.user = os.getenv("KB_NEO4J_USER", "").strip()
        self.password = os.getenv("KB_NEO4J_PASSWORD", "")
        self.database = os.getenv("KB_NEO4J_DATABASE", "neo4j").strip() or "neo4j"

    @property
    def configured(self) -> bool:
        return bool(self.uri and self.user and self.password)

    @property
    def driver_installed(self) -> bool:
        return importlib.util.find_spec("neo4j") is not None

    def status(self, *, ping: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "backend": "neo4j",
            "configured": self.configured,
            "driver_installed": self.driver_installed,
            "database": self.database,
            "credentials_exposed": False,
        }
        if not ping or not self.configured or not self.driver_installed:
            payload["available"] = self.configured and self.driver_installed
            return payload
        try:
            driver = self._driver()
            with driver.session(database=self.database) as session:
                session.run("RETURN 1 AS ok").single()
            driver.close()
            payload["available"] = True
        except Exception as exc:  # pragma: no cover - depends on external service
            payload["available"] = False
            payload["error_type"] = type(exc).__name__
        return payload

    def _driver(self):
        if not self.configured:
            raise RuntimeError("Neo4j backend is not configured")
        if not self.driver_installed:
            raise RuntimeError("neo4j driver is not installed; install requirements-production-optional.txt")
        from neo4j import GraphDatabase

        return GraphDatabase.driver(self.uri, auth=(self.user, self.password))

    def sync(self, graph: dict[str, Any], record_lookup: dict[int, dict[str, Any]]) -> dict[str, Any]:
        driver = self._driver()
        try:
            with driver.session(database=self.database) as session:
                session.run("MATCH (n:KBManagedRecord {kb_source:'governed_catalog'}) DETACH DELETE n")
                for node in graph.get("nodes", []):
                    record = record_lookup.get(int(node["id"]), {})
                    props = {
                        **node,
                        "record_id": int(node["id"]),
                        "source_document_id": record.get("source_document_id"),
                        "source_chunk_id": record.get("source_chunk_id"),
                        "source_filename": record.get("source_filename", ""),
                        "source_chunk_index": record.get("source_chunk_index"),
                        "kb_source": "governed_catalog",
                    }
                    props.pop("attributes", None)
                    session.run(
                        "MERGE (n:KBManagedRecord {record_id:$record_id}) SET n += $props",
                        record_id=int(node["id"]),
                        props=props,
                    )
                edge_count = 0
                for edge in graph.get("edges", []):
                    relation_type = str(edge.get("relation_type") or "")
                    if relation_type not in RELATION_TYPES:
                        continue
                    session.run(
                        f"MATCH (s:KBManagedRecord {{record_id:$source}}), (t:KBManagedRecord {{record_id:$target}}) "
                        f"MERGE (s)-[r:{relation_type}]->(t) "
                        "SET r.confidence=$confidence, r.kb_source='governed_catalog'",
                        source=int(edge["source"]),
                        target=int(edge["target"]),
                        confidence=float(edge.get("confidence") or 0),
                    )
                    edge_count += 1
            return {"status": "synced", "backend": "neo4j", "nodes": len(graph.get("nodes", [])), "edges": edge_count}
        finally:
            driver.close()

    def projection(self, *, region_code: str = "", product_class: str = "") -> dict[str, Any]:
        driver = self._driver()
        nodes: dict[int, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        query = """
        MATCH (n:KBManagedRecord {kb_source:'governed_catalog'})
        WHERE ($region = '' OR n.region_code = $region)
          AND ($product_class = '' OR n.product_class = '' OR n.product_class = '*'
               OR toLower(n.product_class) CONTAINS toLower($product_class)
               OR toLower($product_class) CONTAINS toLower(n.product_class))
        OPTIONAL MATCH (n)-[r]->(m:KBManagedRecord {kb_source:'governed_catalog'})
        WHERE m IS NULL OR (($region = '' OR m.region_code = $region)
          AND ($product_class = '' OR m.product_class = '' OR m.product_class = '*'
               OR toLower(m.product_class) CONTAINS toLower($product_class)
               OR toLower($product_class) CONTAINS toLower(m.product_class)))
        RETURN n, r, m
        """
        try:
            with driver.session(database=self.database) as session:
                for row in session.run(query, region=region_code, product_class=product_class):
                    n = row["n"]
                    if n is not None:
                        item = dict(n)
                        item["id"] = int(item.pop("record_id"))
                        nodes[item["id"]] = item
                    m = row["m"]
                    if m is not None:
                        item = dict(m)
                        item["id"] = int(item.pop("record_id"))
                        nodes[item["id"]] = item
                    r = row["r"]
                    if r is not None and n is not None and m is not None:
                        edges.append({
                            "source": int(dict(n)["record_id"]),
                            "target": int(dict(m)["record_id"]),
                            "relation_type": r.type,
                            "relation_label": r.type,
                            "confidence": float(r.get("confidence", 0)),
                        })
        finally:
            driver.close()
        return {
            "as_of": date.today().isoformat(),
            "region_code": region_code,
            "product_class": product_class,
            "nodes": list(nodes.values()),
            "edges": edges,
            "summary": {"nodes": len(nodes), "edges": len(edges)},
            "boundary": "Neo4j only contains the approved governed graph projection.",
        }


class GraphBackendRouter:
    """Select local governed graph or optional Neo4j without weakening governance."""

    def __init__(self, graph_service: GraphGovernanceService):
        self.graph_service = graph_service
        self.store = graph_service.store
        requested = os.getenv("KB_GRAPH_BACKEND", "sqlite").strip().lower()
        self.requested = requested if requested in {"sqlite", "neo4j"} else "sqlite"
        self.neo4j = Neo4jGovernedGraph()

    def status(self, *, ping: bool = False) -> dict[str, Any]:
        neo = self.neo4j.status(ping=ping)
        return {
            "requested_backend": self.requested,
            "active_default": "neo4j" if self.requested == "neo4j" and neo.get("available") else "sqlite-governed",
            "fallback_enabled": True,
            "neo4j": neo,
            "governance_boundary": "Only approved catalog nodes and approved relations are eligible for GraphRAG.",
        }

    def sync(self) -> dict[str, Any]:
        if self.requested != "neo4j":
            return {"status": "not_required", "backend": "sqlite-governed", "note": "Set KB_GRAPH_BACKEND=neo4j to enable Neo4j projection."}
        graph = self.graph_service.project_graph()
        lookup = {int(item["id"]): item for item in self.store.list_compliance_records(review_status="approved", limit=10000)}
        return self.neo4j.sync(graph, lookup)

    def projection(self, *, region_code: str = "", product_class: str = "", as_of: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        if self.requested == "neo4j" and not as_of:
            status = self.neo4j.status(ping=False)
            if status.get("available"):
                try:
                    graph = self.neo4j.projection(region_code=region_code, product_class=product_class)
                    if graph.get("nodes"):
                        return graph, {"backend": "neo4j", "fallback": False}
                except Exception as exc:  # pragma: no cover - depends on external service
                    return self.graph_service.project_graph(region_code=region_code, product_class=product_class, as_of=as_of), {
                        "backend": "sqlite-governed",
                        "fallback": True,
                        "neo4j_error_type": type(exc).__name__,
                    }
        return self.graph_service.project_graph(region_code=region_code, product_class=product_class, as_of=as_of), {
            "backend": "sqlite-governed",
            "fallback": self.requested == "neo4j",
        }
