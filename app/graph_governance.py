from __future__ import annotations

from collections import defaultdict, deque
from datetime import date
from typing import Any

from app.governance import lifecycle_state
from app.regions import canonical_region_code, knowledge_scope_codes
from app.store import KnowledgeStore

GRAPH_SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_relations (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 source_record_id INTEGER NOT NULL,
 relation_type TEXT NOT NULL,
 target_record_id INTEGER NOT NULL,
 confidence REAL DEFAULT 0,
 evidence_document_id INTEGER,
 evidence_chunk_id INTEGER,
 status TEXT NOT NULL DEFAULT 'pending',
 reviewer_note TEXT DEFAULT '',
 created_at TEXT DEFAULT CURRENT_TIMESTAMP,
 reviewed_at TEXT DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_graph_relation_unique
 ON graph_relations(source_record_id,relation_type,target_record_id);
CREATE INDEX IF NOT EXISTS idx_graph_relation_status ON graph_relations(status);
"""

RELATION_LABELS = {
    "REFERENCES": "引用",
    "REQUIRES": "要求",
    "REPLACED_BY": "被替代",
}

ALLOWED_RELATION_PAIRS = {
    ("regulation", "standard"): "REFERENCES",
    ("regulation", "certification"): "REQUIRES",
    ("regulation", "requirement"): "REQUIRES",
    ("standard", "certification"): "REQUIRES",
    ("standard", "requirement"): "REQUIRES",
    ("standard", "test_item"): "REQUIRES",
    ("certification", "requirement"): "REQUIRES",
    ("certification", "test_item"): "REQUIRES",
}


def _matches_product_class(left: str, right: str) -> bool:
    a = (left or "").strip().lower()
    b = (right or "").strip().lower()
    if not a or not b or a == "*" or b == "*":
        return True
    return a in b or b in a


def _mentions(text: str, target: dict[str, Any]) -> bool:
    haystack = (text or "").lower()
    code = str(target.get("code") or "").strip().lower()
    name = str(target.get("name") or "").strip().lower()
    if code and len(code) >= 3 and code in haystack:
        return True
    return bool(name and len(name) >= 4 and name in haystack)


def _sort_key(record: dict[str, Any]) -> tuple[str, str, int]:
    return (
        str(record.get("effective_from") or ""),
        str(record.get("version") or ""),
        int(record.get("id") or 0),
    )


class GraphGovernanceService:
    """Governed graph projection over approved compliance records.

    Graph relations are independent review objects. Suggestions never become facts
    until a human approves them. This keeps relationship reasoning auditable and
    prevents the UI graph from silently inventing regulatory dependencies.
    """

    def __init__(self, store: KnowledgeStore):
        self.store = store
        with self.store.lock:
            self.store.conn.executescript(GRAPH_SCHEMA)
            self.store.conn.commit()

    def reset(self) -> None:
        with self.store.lock:
            self.store.conn.execute("DELETE FROM graph_relations")
            self.store.conn.commit()

    def _chunk_text(self, record: dict[str, Any]) -> str:
        chunk_id = record.get("source_chunk_id")
        if not chunk_id:
            return ""
        with self.store.lock:
            row = self.store.conn.execute("SELECT text FROM chunks WHERE id=?", (chunk_id,)).fetchone()
        return str(row["text"] if row else "")

    def _insert_candidate(
        self,
        source_id: int,
        relation_type: str,
        target_id: int,
        *,
        confidence: float,
        evidence_document_id: int | None,
        evidence_chunk_id: int | None,
    ) -> bool:
        with self.store.lock:
            existing = self.store.conn.execute(
                "SELECT id FROM graph_relations WHERE source_record_id=? AND relation_type=? AND target_record_id=?",
                (source_id, relation_type, target_id),
            ).fetchone()
            if existing:
                return False
            self.store.conn.execute(
                """INSERT INTO graph_relations(
                       source_record_id,relation_type,target_record_id,confidence,
                       evidence_document_id,evidence_chunk_id,status)
                   VALUES(?,?,?,?,?,?, 'pending')""",
                (source_id, relation_type, target_id, confidence, evidence_document_id, evidence_chunk_id),
            )
            self.store.conn.commit()
        return True

    def _cleanup_orphans(self) -> None:
        with self.store.lock:
            self.store.conn.execute(
                """DELETE FROM graph_relations
                   WHERE source_record_id NOT IN (SELECT id FROM compliance_records)
                      OR target_record_id NOT IN (SELECT id FROM compliance_records)"""
            )
            self.store.conn.commit()

    def summary(self) -> dict[str, Any]:
        self._cleanup_orphans()
        with self.store.lock:
            rows = self.store.conn.execute(
                "SELECT status,COUNT(*) count FROM graph_relations GROUP BY status"
            ).fetchall()
            types = self.store.conn.execute(
                "SELECT relation_type,COUNT(*) count FROM graph_relations WHERE status='approved' GROUP BY relation_type"
            ).fetchall()
        counts = {row["status"]: row["count"] for row in rows}
        return {
            "relations": sum(counts.values()),
            "pending": counts.get("pending", 0),
            "approved": counts.get("approved", 0),
            "rejected": counts.get("rejected", 0),
            "approved_types": {row["relation_type"]: row["count"] for row in types},
            "principle": "节点来自已审核正式知识；关系候选必须人工审核后才进入正式图谱。",
        }

    def suggest_relations(self, *, region_code: str = "", product_class: str = "") -> dict[str, Any]:
        records = self.store.list_compliance_records(
            review_status="approved",
            region_code=region_code or None,
            limit=2000,
        )
        if product_class:
            records = [r for r in records if _matches_product_class(r.get("product_class", ""), product_class)]

        created = 0
        reasons = defaultdict(int)

        # Version replacement suggestions: same object identity, ordered versions.
        groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            code = str(record.get("code") or "").strip()
            if not code:
                continue
            key = (
                record.get("record_type", ""),
                code.lower(),
                record.get("region_code", ""),
                record.get("product_class", ""),
            )
            groups[key].append(record)
        for items in groups.values():
            if len(items) < 2:
                continue
            ordered = sorted(items, key=_sort_key)
            for older, newer in zip(ordered, ordered[1:]):
                if (older.get("version") or older.get("effective_from")) == (newer.get("version") or newer.get("effective_from")):
                    continue
                if self._insert_candidate(
                    int(older["id"]),
                    "REPLACED_BY",
                    int(newer["id"]),
                    confidence=0.96,
                    evidence_document_id=older.get("source_document_id"),
                    evidence_chunk_id=older.get("source_chunk_id"),
                ):
                    created += 1
                    reasons["version_chain"] += 1

        # Semantic/structural relationship suggestions only when evidence is nearby.
        for source in records:
            source_text = self._chunk_text(source)
            for target in records:
                if source["id"] == target["id"]:
                    continue
                relation_type = ALLOWED_RELATION_PAIRS.get((source.get("record_type"), target.get("record_type")))
                if not relation_type:
                    continue
                if source.get("region_code") and target.get("region_code") and source.get("region_code") != target.get("region_code"):
                    continue
                if not _matches_product_class(source.get("product_class", ""), target.get("product_class", "")):
                    continue

                confidence = 0.0
                reason = ""
                same_chunk = source.get("source_chunk_id") and source.get("source_chunk_id") == target.get("source_chunk_id")
                same_document = source.get("source_document_id") and source.get("source_document_id") == target.get("source_document_id")
                if _mentions(source_text, target):
                    confidence, reason = 0.94, "explicit_mention"
                elif same_chunk:
                    confidence, reason = 0.88, "same_evidence_chunk"
                elif same_document:
                    confidence, reason = 0.72, "same_source_document"
                if not reason:
                    continue
                if self._insert_candidate(
                    int(source["id"]),
                    relation_type,
                    int(target["id"]),
                    confidence=confidence,
                    evidence_document_id=source.get("source_document_id"),
                    evidence_chunk_id=source.get("source_chunk_id"),
                ):
                    created += 1
                    reasons[reason] += 1

        return {
            "created": created,
            "reasons": dict(reasons),
            "summary": self.summary(),
            "note": "这里只生成关系候选；未审核关系不会进入认证路径或影响分析。",
        }

    def list_relations(self, *, status: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        self._cleanup_orphans()
        clauses = []
        params: list[Any] = []
        if status:
            clauses.append("g.status=?")
            params.append(status)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        with self.store.lock:
            rows = self.store.conn.execute(
                f"""SELECT g.*,
                           s.name source_name,s.code source_code,s.record_type source_type,s.version source_version,
                           t.name target_name,t.code target_code,t.record_type target_type,t.version target_version
                    FROM graph_relations g
                    JOIN compliance_records s ON s.id=g.source_record_id
                    JOIN compliance_records t ON t.id=g.target_record_id
                    {where}
                    ORDER BY CASE g.status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
                             g.confidence DESC,g.id DESC LIMIT ?""",
                params,
            ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["relation_label"] = RELATION_LABELS.get(item["relation_type"], item["relation_type"])
            output.append(item)
        return output

    def review_relation(self, relation_id: int, *, action: str, note: str = "") -> dict[str, Any]:
        if action not in {"approve", "reject"}:
            raise ValueError("action must be approve or reject")
        with self.store.lock:
            row = self.store.conn.execute("SELECT * FROM graph_relations WHERE id=?", (relation_id,)).fetchone()
            if not row:
                raise ValueError("graph relation not found")
            if row["status"] != "pending":
                raise ValueError(f"graph relation already {row['status']}")
            status = "approved" if action == "approve" else "rejected"
            self.store.conn.execute(
                "UPDATE graph_relations SET status=?,reviewer_note=?,reviewed_at=CURRENT_TIMESTAMP WHERE id=?",
                (status, note, relation_id),
            )
            self.store.conn.commit()
        return {"relation_id": relation_id, "status": status}

    def _filtered_records(self, *, region_code: str = "", product_class: str = "", as_of: str | None = None) -> list[dict[str, Any]]:
        requested_region = canonical_region_code(region_code)
        scope_codes = set(knowledge_scope_codes(requested_region)) if requested_region else set()
        records = self.store.list_compliance_records(
            review_status="approved",
            limit=5000,
        )
        output = []
        for source in records:
            source_region = canonical_region_code(str(source.get("region_code") or ""))
            if scope_codes and source_region not in scope_codes:
                continue
            if product_class and not _matches_product_class(source.get("product_class", ""), product_class):
                continue
            item = dict(source)
            item["lifecycle_state"] = lifecycle_state(item, as_of=as_of)
            if item["lifecycle_state"] in {"repealed", "withdrawn", "superseded", "expired", "future"}:
                continue
            output.append(item)
        return output

    def project_graph(self, *, region_code: str = "", product_class: str = "", as_of: str | None = None) -> dict[str, Any]:
        records = self._filtered_records(region_code=region_code, product_class=product_class, as_of=as_of)
        node_ids = {int(item["id"]) for item in records}
        relations = [
            item for item in self.list_relations(status="approved", limit=5000)
            if int(item["source_record_id"]) in node_ids and int(item["target_record_id"]) in node_ids
        ]
        connected = {int(x["source_record_id"]) for x in relations} | {int(x["target_record_id"]) for x in relations}
        nodes = []
        for item in records:
            nodes.append({
                "id": int(item["id"]),
                "label": item.get("name") or item.get("code") or f"record-{item['id']}",
                "code": item.get("code", ""),
                "node_type": item.get("record_type", ""),
                "version": item.get("version", ""),
                "status": item.get("status", ""),
                "lifecycle_state": item.get("lifecycle_state", ""),
                "region_code": item.get("region_code", ""),
                "product_class": item.get("product_class", ""),
                "evidence_backed": bool(item.get("source_document_id") and item.get("source_chunk_id")),
                "attributes": item.get("attributes", {}),
            })
        edges = [{
            "id": int(item["id"]),
            "source": int(item["source_record_id"]),
            "target": int(item["target_record_id"]),
            "relation_type": item["relation_type"],
            "relation_label": item["relation_label"],
            "confidence": item["confidence"],
        } for item in relations]
        return {
            "as_of": as_of or date.today().isoformat(),
            "region_code": region_code,
            "product_class": product_class,
            "nodes": nodes,
            "edges": edges,
            "summary": {
                "nodes": len(nodes),
                "edges": len(edges),
                "connected_nodes": len(connected),
                "orphan_nodes": max(0, len(nodes) - len(connected)),
                "pending_relations": self.summary()["pending"],
            },
            "boundary": "图谱只投影已审核节点和已审核关系；候选关系不参与路径推理。",
        }

    def certification_paths(self, *, region_code: str, product_class: str, as_of: str | None = None) -> dict[str, Any]:
        graph = self.project_graph(region_code=region_code, product_class=product_class, as_of=as_of)
        nodes = {int(item["id"]): item for item in graph["nodes"]}
        adjacency: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for edge in graph["edges"]:
            adjacency[int(edge["source"])].append(edge)

        starts = [nid for nid, item in nodes.items() if item["node_type"] in {"regulation", "standard"}]
        goal_types = {"certification", "requirement", "test_item"}
        paths = []
        reached = set()
        for start in starts:
            queue = deque([(start, [start], [])])
            seen = {(start, 0)}
            while queue and len(paths) < 100:
                current, node_path, edge_path = queue.popleft()
                if len(edge_path) >= 5:
                    continue
                for edge in adjacency.get(current, []):
                    target = int(edge["target"])
                    if target in node_path:
                        continue
                    next_nodes = node_path + [target]
                    next_edges = edge_path + [edge]
                    if nodes[target]["node_type"] in goal_types:
                        reached.add(target)
                        paths.append({
                            "node_ids": next_nodes,
                            "nodes": [nodes[nid] for nid in next_nodes],
                            "edges": next_edges,
                        })
                    state = (target, len(next_edges))
                    if state not in seen:
                        seen.add(state)
                        queue.append((target, next_nodes, next_edges))

        target_nodes = [nid for nid, item in nodes.items() if item["node_type"] in goal_types]
        gaps = [nodes[nid] for nid in target_nodes if nid not in reached]
        if not nodes:
            status = "insufficient_data"
        elif not graph["edges"]:
            status = "needs_relation_review"
        elif not paths:
            status = "no_certification_path"
        elif gaps:
            status = "partial"
        else:
            status = "ready"
        return {
            "status": status,
            "region_code": region_code,
            "product_class": product_class,
            "as_of": graph["as_of"],
            "paths": paths,
            "unconnected_targets": gaps,
            "summary": {
                "paths": len(paths),
                "start_nodes": len(starts),
                "target_nodes": len(target_nodes),
                "unconnected_targets": len(gaps),
            },
            "next_action": (
                "先生成并审核关系候选，才能形成正式认证路径。"
                if status == "needs_relation_review"
                else "补齐未连通认证/要求节点的关系证据。"
                if status in {"partial", "no_certification_path"}
                else "路径已具备结构化依据，可进入专家复核。"
            ),
        }

    def impact_analysis(self, record_id: int, *, max_depth: int = 5) -> dict[str, Any]:
        records = {int(item["id"]): item for item in self.store.list_compliance_records(review_status="approved", limit=5000)}
        if record_id not in records:
            raise ValueError("approved compliance record not found")
        relations = self.list_relations(status="approved", limit=10000)
        outgoing: dict[int, list[dict[str, Any]]] = defaultdict(list)
        incoming: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for edge in relations:
            outgoing[int(edge["source_record_id"])].append(edge)
            incoming[int(edge["target_record_id"])].append(edge)

        def walk(start: int, adjacency: dict[int, list[dict[str, Any]]], direction: str) -> list[dict[str, Any]]:
            queue = deque([(start, 0, [])])
            best_depth: dict[int, int] = {start: 0}
            found: dict[int, dict[str, Any]] = {}
            while queue:
                current, depth, path = queue.popleft()
                if depth >= max_depth:
                    continue
                for edge in adjacency.get(current, []):
                    target = int(edge["target_record_id"] if direction == "downstream" else edge["source_record_id"])
                    next_path = path + [edge]
                    if target not in best_depth or depth + 1 < best_depth[target]:
                        best_depth[target] = depth + 1
                        found[target] = {
                            "record": records.get(target, {"id": target, "name": "unknown"}),
                            "depth": depth + 1,
                            "via": [{"relation_type": x["relation_type"], "source": x["source_record_id"], "target": x["target_record_id"]} for x in next_path],
                        }
                        queue.append((target, depth + 1, next_path))
            return sorted(found.values(), key=lambda x: (x["depth"], str(x["record"].get("record_type", "")), str(x["record"].get("name", ""))))

        downstream = walk(record_id, outgoing, "downstream")
        upstream = walk(record_id, incoming, "upstream")
        affected_types = defaultdict(int)
        for item in downstream:
            affected_types[item["record"].get("record_type", "unknown")] += 1
        version_edges = [
            edge for edge in relations
            if edge["relation_type"] == "REPLACED_BY" and (int(edge["source_record_id"]) == record_id or int(edge["target_record_id"]) == record_id)
        ]
        return {
            "record": records[record_id],
            "downstream": downstream,
            "upstream": upstream,
            "version_edges": version_edges,
            "summary": {
                "downstream_count": len(downstream),
                "upstream_count": len(upstream),
                "affected_types": dict(affected_types),
            },
            "interpretation": "下游表示该知识发生变更后应重点复核的标准、认证、要求和检测项目；结果来自已审核关系，不等同自动法律影响结论。",
        }
