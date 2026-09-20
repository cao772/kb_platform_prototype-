from __future__ import annotations

import tempfile
from pathlib import Path

from app.graph_governance import GraphGovernanceService
from app.ontology_constraints import OntologyConstraintService
from app.store import KnowledgeStore


def add_record(store: KnowledgeStore, *, record_type: str, code: str, name: str, region: str) -> int:
    with store.lock:
        rid = store._upsert_compliance_record_locked({
            "record_type": record_type,
            "code": code,
            "name": name,
            "region_code": region,
            "region_name": region,
            "product_class": "家用电器",
            "status": "active",
            "version": "1",
            "effective_from": "2026-01-01",
            "source_document_id": None,
            "source_chunk_id": None,
            "attributes": {},
        })
        store.conn.commit()
    return int(rid)


def main() -> None:
    constraints = OntologyConstraintService()
    ok = constraints.validate_relation("REQUIRES", "regulation", "certification")
    assert ok["valid"] is True
    bad = constraints.validate_relation("REFERENCES", "certification", "regulation")
    assert bad["valid"] is False

    with tempfile.TemporaryDirectory() as tmp:
        store = KnowledgeStore(Path(tmp) / "constraints.db")
        graph = GraphGovernanceService(store)

        eu_reg = add_record(
            store, record_type="regulation", code="EU-REG-1",
            name="EU Product Regulation", region="EU",
        )
        de_cert = add_record(
            store, record_type="certification", code="DE-CERT-1",
            name="German Product Certification", region="DE",
        )
        de_std = add_record(
            store, record_type="standard", code="DE-STD-1",
            name="German Standard", region="DE",
        )

        # Approved EU -> DE relation is valid for a German market projection.
        with store.lock:
            store.conn.execute(
                """INSERT INTO graph_relations(
                       source_record_id,relation_type,target_record_id,confidence,status,reviewer_note
                   ) VALUES(?,?,?,0.99,'approved','人工确认')""",
                (eu_reg, "REQUIRES", de_cert),
            )
            store.conn.commit()

        project = graph.project_graph(region_code="DE", product_class="家用电器", as_of="2026-09-18")
        node_ids = {node["id"] for node in project["nodes"]}
        assert eu_reg in node_ids
        assert de_cert in node_ids
        assert de_std in node_ids
        assert any(edge["source"] == eu_reg and edge["target"] == de_cert for edge in project["edges"])

        path = graph.certification_paths(
            region_code="DE", product_class="家用电器", as_of="2026-09-18"
        )
        assert path["summary"]["paths"] >= 1

        # Invalid relation can exist only as legacy data; project excludes it.
        with store.lock:
            cur = store.conn.execute(
                """INSERT INTO graph_relations(
                       source_record_id,relation_type,target_record_id,confidence,status
                   ) VALUES(?,?,?,0.5,'approved')""",
                (de_cert, "REFERENCES", eu_reg),
            )
            invalid_approved_id = int(cur.lastrowid)
            cur = store.conn.execute(
                """INSERT INTO graph_relations(
                       source_record_id,relation_type,target_record_id,confidence,status
                   ) VALUES(?,?,?,0.5,'pending')""",
                (de_cert, "REFERENCES", de_std),
            )
            invalid_pending_id = int(cur.lastrowid)
            store.conn.commit()

        listed = graph.list_relations(status="approved")
        legacy = next(item for item in listed if item["id"] == invalid_approved_id)
        assert legacy["ontology_validation"]["valid"] is False

        project2 = graph.project_graph(region_code="DE", product_class="家用电器")
        assert not any(edge["id"] == invalid_approved_id for edge in project2["edges"])
        assert graph.summary()["invalid_approved"] == 1

        try:
            graph.review_relation(invalid_pending_id, action="approve", note="不应通过")
            raise AssertionError("invalid ontology relation should not be approved")
        except ValueError as exc:
            assert "ontology relation constraint failed" in str(exc)

    code = Path("app/graph_governance.py").read_text(encoding="utf-8")
    constraint_code = Path("app/ontology_constraints.py").read_text(encoding="utf-8")
    assert "knowledge_scope_codes" in code
    assert "ontology_validation" in code
    assert "OntologyConstraintService" in code
    assert "validate_relation" in constraint_code

    print("OK: stage25 ontology relation constraints and EU shared scopes passed")


if __name__ == "__main__":
    main()
