from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class OntologyNodeType:
    key: str
    label: str
    required_properties: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        data = asdict(self)
        data["required_properties"] = list(self.required_properties)
        return data


@dataclass(frozen=True)
class OntologyRelation:
    key: str
    label: str
    source_types: tuple[str, ...]
    target_types: tuple[str, ...]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["source_types"] = list(self.source_types)
        data["target_types"] = list(self.target_types)
        return data


NODE_TYPES = (
    OntologyNodeType("product", "产品", ("name",)),
    OntologyNodeType("product_class", "产品分类", ("name",)),
    OntologyNodeType("region", "国家/地区", ("name", "code")),
    OntologyNodeType("authority", "主管机构", ("name",)),
    OntologyNodeType("regulation", "法规", ("name", "status")),
    OntologyNodeType("standard", "标准", ("name", "status")),
    OntologyNodeType("certification", "认证", ("name", "status")),
    OntologyNodeType("requirement", "要求", ("name",)),
    OntologyNodeType("test_item", "检测项目", ("name",)),
    OntologyNodeType("version", "版本", ("version", "effective_status")),
    OntologyNodeType("evidence", "证据", ("document_id", "chunk_id")),
)

RELATIONS = (
    OntologyRelation("IS_A", "属于", ("product", "product_class"), ("product_class",)),
    OntologyRelation("APPLIES_TO", "适用于", ("regulation", "standard", "certification", "requirement"), ("product", "product_class")),
    OntologyRelation("IN_REGION", "适用地区", ("regulation", "standard", "certification", "requirement", "authority"), ("region",)),
    OntologyRelation("ISSUED_BY", "发布/管理机构", ("regulation", "standard", "certification"), ("authority",)),
    OntologyRelation("REQUIRES", "要求", ("regulation", "standard", "certification"), ("requirement", "test_item", "certification")),
    OntologyRelation("REFERENCES", "引用", ("regulation", "standard", "requirement"), ("regulation", "standard")),
    OntologyRelation("HAS_VERSION", "具有版本", ("regulation", "standard", "certification"), ("version",)),
    OntologyRelation("SUPPORTED_BY", "依据证据", ("regulation", "standard", "certification", "requirement", "test_item", "authority"), ("evidence",)),
    OntologyRelation("REPLACED_BY", "被替代", ("version",), ("version",)),
)


def ontology_schema() -> dict:
    return {
        "version": "2026.09-governed-v1",
        "principle": "固定本体 -> 文档证据 -> 受约束抽取 -> 人工审核 -> 正式知识目录 -> 图谱/智能体消费。未经审核的候选不得参与正式法规回答。",
        "lifecycle": ["document_ingested", "candidate_extracted", "human_reviewed", "approved_catalog_record", "graph_projected", "answer_with_evidence"],
        "node_types": [item.to_dict() for item in NODE_TYPES],
        "relations": [item.to_dict() for item in RELATIONS],
    }
