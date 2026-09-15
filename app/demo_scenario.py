from __future__ import annotations

from pathlib import Path
from typing import Any

from app.agent import KnowledgeAgent
from app.governance import analyze_product_access_v2, world_map_v2
from app.graph_backend import GraphBackendRouter
from app.graph_governance import GraphGovernanceService
from app.store import KnowledgeStore

DEMO_WARNING = (
    "客户演示沙箱使用的是虚构法规、标准、认证编号和演示业务数据，"
    "仅用于展示知识治理、认证路径、GraphRAG 与影响分析能力，不代表任何真实法律或认证结论。"
)


class DemoScenarioService:
    """Self-contained customer demo using the same governed graph/agent chain.

    The demo has its own SQLite database and never writes to the formal knowledge DB.
    All identifiers are deliberately prefixed with DEMO to avoid confusion with real law.
    """

    def __init__(self, db_path: str | Path):
        self.store = KnowledgeStore(db_path)
        self.graph = GraphGovernanceService(self.store)
        self.backend = GraphBackendRouter(self.graph)
        self.agent = KnowledgeAgent(self.store, graph_service=self.graph, graph_backend=self.backend)
        self.record_ids: dict[str, int] = {}

    def _add_document(self, *, filename: str, title: str, text: str) -> tuple[int, int]:
        document_id = self.store.upsert_document(
            filename=filename,
            title=title,
            knowledge_type="法规知识",
            tags=["演示", "欧盟", "家用电器", "认证路径"],
            source_path=f"demo://{filename}",
            full_text=text,
            mime_type="text/plain",
            parser="stage5-demo-seed",
            metadata={"demo_only": True, "region": "EU", "product_class": "家用电器"},
            chunks=[{
                "chunk_index": 1,
                "text": text,
                "metadata": {"demo_only": True, "region": "EU", "product_class": "家用电器"},
            }],
        )
        chunk_id = int(self.store.document_chunks(document_id)[0]["id"])
        return document_id, chunk_id

    def _add_record(
        self,
        *,
        key: str,
        record_type: str,
        code: str,
        name: str,
        version: str,
        status: str,
        effective_from: str,
        effective_to: str,
        document_id: int,
        chunk_id: int,
        note: str,
    ) -> int:
        payload: dict[str, Any] = {
            "record_type": record_type,
            "code": code,
            "name": name,
            "region_code": "EU",
            "region_name": "欧盟（演示）",
            "product_class": "家用电器",
            "status": status,
            "version": version,
            "effective_from": effective_from,
            "effective_to": effective_to,
            "source_document_id": document_id,
            "source_chunk_id": chunk_id,
            "attributes": {
                "demo_only": True,
                "review_basis": "stage5-customer-demo",
                "authority": "演示主管机构",
                "applicability_scope": "仅用于展示家用电器出口欧盟的知识链路",
                "legal_effect": "DEMO_ONLY",
                "note": note,
            },
        }
        with self.store.lock:
            record_id = self.store._upsert_compliance_record_locked(payload)
            self.store.conn.commit()
        self.record_ids[key] = int(record_id)
        return int(record_id)

    def _add_relation(self, source_key: str, relation_type: str, target_key: str, *, evidence_document_id: int, evidence_chunk_id: int) -> None:
        created = self.graph._insert_candidate(
            self.record_ids[source_key],
            relation_type,
            self.record_ids[target_key],
            confidence=0.99,
            evidence_document_id=evidence_document_id,
            evidence_chunk_id=evidence_chunk_id,
        )
        if not created:
            return
        pending = self.graph.list_relations(status="pending", limit=500)
        relation = next(
            item for item in pending
            if int(item["source_record_id"]) == self.record_ids[source_key]
            and item["relation_type"] == relation_type
            and int(item["target_record_id"]) == self.record_ids[target_key]
        )
        self.graph.review_relation(int(relation["id"]), action="approve", note="客户演示沙箱预置关系")

    def seed(self) -> dict[str, Any]:
        self.graph.reset()
        self.store.reset()
        self.record_ids = {}

        doc1, chunk1 = self._add_document(
            filename="DEMO_欧盟家电准入规则_演示.txt",
            title="演示：欧盟家电准入规则与版本变更",
            text=(
                "【演示数据，不代表真实法规】家用电器进入欧盟演示市场时，"
                "DEMO-EU-REG-100（2026版）引用 DEMO-EN-STD-200，并要求 DEMO-CERT-300。"
                "2026版替代2024演示旧版。所有编号均为虚构演示编号。"
            ),
        )
        doc2, chunk2 = self._add_document(
            filename="DEMO_认证检测要求_演示.txt",
            title="演示：认证、技术文件与检测要求",
            text=(
                "【演示数据，不代表真实认证要求】DEMO-CERT-300 要求准备 DEMO-REQ-310 技术文件，"
                "并完成 DEMO-TEST-400 安全检测项目。该材料只用于演示知识图谱与准入分析。"
            ),
        )

        self._add_record(
            key="reg_old", record_type="regulation", code="DEMO-EU-REG-100",
            name="演示家电市场准入法规", version="2024", status="superseded",
            effective_from="2024-01-01", effective_to="2025-12-31",
            document_id=doc1, chunk_id=chunk1, note="演示旧版法规",
        )
        self._add_record(
            key="reg_new", record_type="regulation", code="DEMO-EU-REG-100",
            name="演示家电市场准入法规", version="2026", status="active",
            effective_from="2026-01-01", effective_to="",
            document_id=doc1, chunk_id=chunk1, note="演示现行版本",
        )
        self._add_record(
            key="standard", record_type="standard", code="DEMO-EN-STD-200",
            name="演示家电安全标准", version="2026", status="active",
            effective_from="2026-01-01", effective_to="",
            document_id=doc1, chunk_id=chunk1, note="被演示法规引用的标准",
        )
        self._add_record(
            key="cert", record_type="certification", code="DEMO-CERT-300",
            name="演示市场准入认证", version="2026", status="active",
            effective_from="2026-01-01", effective_to="",
            document_id=doc1, chunk_id=chunk1, note="演示认证节点",
        )
        self._add_record(
            key="requirement", record_type="requirement", code="DEMO-REQ-310",
            name="演示技术文件要求", version="2026", status="active",
            effective_from="2026-01-01", effective_to="",
            document_id=doc2, chunk_id=chunk2, note="演示认证所需技术资料",
        )
        self._add_record(
            key="test", record_type="test_item", code="DEMO-TEST-400",
            name="演示安全检测项目", version="2026", status="active",
            effective_from="2026-01-01", effective_to="",
            document_id=doc2, chunk_id=chunk2, note="演示认证所需检测项目",
        )

        self._add_relation("reg_old", "REPLACED_BY", "reg_new", evidence_document_id=doc1, evidence_chunk_id=chunk1)
        self._add_relation("reg_new", "REFERENCES", "standard", evidence_document_id=doc1, evidence_chunk_id=chunk1)
        self._add_relation("reg_new", "REQUIRES", "cert", evidence_document_id=doc1, evidence_chunk_id=chunk1)
        self._add_relation("cert", "REQUIRES", "requirement", evidence_document_id=doc2, evidence_chunk_id=chunk2)
        self._add_relation("cert", "REQUIRES", "test", evidence_document_id=doc2, evidence_chunk_id=chunk2)

        return self.snapshot()

    def ensure_seeded(self) -> None:
        if self.store.stats().get("documents", 0) < 2 or self.graph.summary().get("approved", 0) < 5:
            self.seed()
            return
        records = self.store.list_compliance_records(review_status="approved", limit=100)
        by_code_version = {(item.get("code"), item.get("version")): int(item["id"]) for item in records}
        self.record_ids = {
            "reg_old": by_code_version.get(("DEMO-EU-REG-100", "2024"), 0),
            "reg_new": by_code_version.get(("DEMO-EU-REG-100", "2026"), 0),
            "standard": by_code_version.get(("DEMO-EN-STD-200", "2026"), 0),
            "cert": by_code_version.get(("DEMO-CERT-300", "2026"), 0),
            "requirement": by_code_version.get(("DEMO-REQ-310", "2026"), 0),
            "test": by_code_version.get(("DEMO-TEST-400", "2026"), 0),
        }
        if not all(self.record_ids.values()):
            self.seed()

    def snapshot(self, *, as_of: str = "2026-09-15") -> dict[str, Any]:
        self.ensure_seeded()
        graph, backend_trace = self.backend.projection(region_code="EU", product_class="家用电器", as_of=as_of)
        paths = self.graph.certification_paths(region_code="EU", product_class="家用电器", as_of=as_of)
        impact = self.graph.impact_analysis(self.record_ids["reg_new"])
        access = analyze_product_access_v2(
            self.store,
            product="演示智能家电",
            product_class="家用电器",
            region_code="EU",
            include_demo=False,
            as_of=as_of,
        )
        map_data = world_map_v2(self.store, include_demo=False, as_of=as_of)
        return {
            "demo_only": True,
            "warning": DEMO_WARNING,
            "scenario": {
                "title": "演示智能家电进入欧盟市场",
                "product": "演示智能家电",
                "product_class": "家用电器",
                "region_code": "EU",
                "region_name": "欧盟",
                "as_of": as_of,
                "question": "演示智能家电出口欧盟需要核对哪些法规、认证和检测路径？",
            },
            "stats": self.store.stats(),
            "documents": self.store.list_documents(),
            "graph": {**graph, "backend_trace": backend_trace},
            "paths": paths,
            "access": access,
            "map": map_data,
            "impact": impact,
            "relations": self.graph.list_relations(status="approved", limit=100),
        }

    def answer(self, question: str = "") -> dict[str, Any]:
        self.ensure_seeded()
        query = question.strip() or "演示智能家电出口欧盟需要核对哪些法规、认证和检测路径？"
        result = self.agent.answer(query, knowledge_type="法规知识")
        result["demo_only"] = True
        result["warning"] = DEMO_WARNING
        return result
