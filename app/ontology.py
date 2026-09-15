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
    OntologyRelation("IN_REGION", "适用地区", ("regulation", "standard", "certification", "requirement"), ("region",)),
    OntologyRelation("REQUIRES", "要求", ("regulation", "standard", "certification"), ("requirement", "test_item", "certification")),
    OntologyRelation("REFERENCES", "引用", ("regulation", "standard", "requirement"), ("regulation", "standard")),
    OntologyRelation("HAS_VERSION", "具有版本", ("regulation", "standard", "certification"), ("version",)),
    OntologyRelation("SUPPORTED_BY", "依据证据", ("regulation", "standard", "certification", "requirement", "test_item"), ("evidence",)),
    OntologyRelation("REPLACED_BY", "被替代", ("version",), ("version",)),
)


def ontology_schema() -> dict:
    return {
        "version": "2026.09",
        "principle": "先固定领域本体和证据模型，再让模型做受约束的实体/关系抽取；法规有效性由结构化版本状态控制。",
        "node_types": [item.to_dict() for item in NODE_TYPES],
        "relations": [item.to_dict() for item in RELATIONS],
    }
