from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class KnowledgeDomain:
    key: str
    label: str
    purpose: str
    record_types: tuple[str, ...]
    derived: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["record_types"] = list(self.record_types)
        return data


@dataclass(frozen=True)
class OntologyNodeType:
    key: str
    label: str
    description: str
    required_properties: tuple[str, ...] = ()
    recommended_properties: tuple[str, ...] = ()
    identity_properties: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("required_properties", "recommended_properties", "identity_properties"):
            data[key] = list(data[key])
        return data


@dataclass(frozen=True)
class OntologyRelation:
    key: str
    label: str
    source_types: tuple[str, ...]
    target_types: tuple[str, ...]
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["source_types"] = list(self.source_types)
        data["target_types"] = list(self.target_types)
        return data


@dataclass(frozen=True)
class OntologyShape:
    record_type: str
    label: str
    required: tuple[str, ...]
    recommended: tuple[str, ...]
    business_key: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("required", "recommended", "business_key"):
            data[key] = list(data[key])
        return data


KNOWLEDGE_DOMAINS = (
    KnowledgeDomain(
        "regulation",
        "法规知识库",
        "管理法规、指令、条例、实施细则及其版本、效力和适用范围。",
        ("regulation", "requirement"),
    ),
    KnowledgeDomain(
        "standard",
        "标准知识库",
        "管理标准编号、版本、适用产品、引用关系及相关检测要求。",
        ("standard", "test_item"),
    ),
    KnowledgeDomain(
        "certification",
        "认证知识库",
        "管理认证事项、认证要求、检测项目、主管机构以及关联标准。",
        ("certification", "requirement", "test_item"),
    ),
    KnowledgeDomain(
        "gma",
        "GMA知识库",
        "以产品和目标市场为入口，复用法规、标准、认证和要求，组织形成市场准入路径。",
        ("regulation", "standard", "certification", "requirement", "test_item"),
        derived=True,
    ),
)


NODE_TYPES = (
    OntologyNodeType("product", "产品", "具体产品或产品系列。", ("name",), ("model", "brand"), ("name", "model")),
    OntologyNodeType("product_class", "产品分类", "统一产品分类，用于跨国家知识映射。", ("name",), ("code",), ("name",)),
    OntologyNodeType("region", "国家/地区", "业务方确认的目标国家、地区或区域市场。", ("name", "code"), (), ("code",)),
    OntologyNodeType("authority", "主管机构", "法规发布、标准管理或认证主管机构。", ("name",), ("region_code",), ("name", "region_code")),
    OntologyNodeType("regulation", "法规", "法规、指令、条例和实施细则。", ("name", "status"), ("code", "version", "region_code", "product_class", "effective_from"), ("code", "name", "region_code", "version")),
    OntologyNodeType("standard", "标准", "国际、区域、国家或行业标准。", ("name", "status"), ("code", "version", "region_code", "product_class"), ("code", "name", "version")),
    OntologyNodeType("certification", "认证", "认证制度、认证事项和准入认证要求。", ("name", "status"), ("code", "region_code", "product_class", "authority"), ("code", "name", "region_code")),
    OntologyNodeType("requirement", "技术要求", "法规、标准或认证提出的具体技术与资料要求。", ("name",), ("region_code", "product_class"), ("name", "region_code", "product_class")),
    OntologyNodeType("test_item", "检测项目", "为满足标准或认证要求需要执行的检测项目。", ("name",), ("product_class",), ("name", "product_class")),
    OntologyNodeType("version", "版本", "法规、标准或认证的版本及时间有效性。", ("version", "effective_status"), ("effective_from", "effective_to"), ("version",)),
    OntologyNodeType("applicability_rule", "适用条件", "适用产品、地区、范围和判定条件。", ("condition",), (), ("condition",)),
    OntologyNodeType("exception", "例外条件", "不适用、豁免、过渡期等例外情况。", ("description",), (), ("description",)),
    OntologyNodeType("evidence", "依据", "原始文件和原文片段，是正式知识与关系的可追溯依据。", ("document_id", "chunk_id"), ("source_url",), ("document_id", "chunk_id")),
)


RELATIONS = (
    OntologyRelation("IS_A", "属于", ("product", "product_class"), ("product_class",), "产品归属统一产品分类。"),
    OntologyRelation("APPLIES_TO", "适用于", ("regulation", "standard", "certification", "requirement"), ("product", "product_class"), "知识对象与适用产品建立关系。"),
    OntologyRelation("IN_REGION", "适用地区", ("regulation", "standard", "certification", "requirement", "authority"), ("region",), "明确知识对象适用的国家或地区。"),
    OntologyRelation("ISSUED_BY", "发布/管理机构", ("regulation", "standard", "certification"), ("authority",), "关联发布、管理或认证主管机构。"),
    OntologyRelation("REQUIRES", "要求", ("regulation", "standard", "certification"), ("requirement", "test_item", "certification"), "表达法规、标准、认证之间的要求链。"),
    OntologyRelation("REFERENCES", "引用", ("regulation", "standard", "requirement"), ("regulation", "standard"), "表达显式引用或采用关系。"),
    OntologyRelation("HAS_VERSION", "具有版本", ("regulation", "standard", "certification"), ("version",), "挂接版本与时间有效性。"),
    OntologyRelation("HAS_APPLICABILITY", "具有适用规则", ("regulation", "standard", "certification", "requirement"), ("applicability_rule",), "挂接适用范围和判定条件。"),
    OntologyRelation("HAS_EXCEPTION", "具有例外条件", ("regulation", "standard", "certification", "requirement", "applicability_rule"), ("exception",), "挂接豁免和例外条件。"),
    OntologyRelation("SUPPORTED_BY", "依据证据", ("regulation", "standard", "certification", "requirement", "test_item", "authority", "applicability_rule", "exception"), ("evidence",), "所有正式知识和关键关系均可追溯到原始依据。"),
    OntologyRelation("REPLACED_BY", "被替代", ("regulation", "standard", "certification", "version"), ("regulation", "standard", "certification", "version"), "表达新旧版本或对象的替代关系。"),
)


SHAPES = (
    OntologyShape("regulation", "法规", ("name",), ("code", "region_code", "product_class", "status", "version", "effective_from", "source_document_id", "source_chunk_id"), ("code", "name", "region_code", "product_class", "version")),
    OntologyShape("standard", "标准", ("name",), ("code", "region_code", "product_class", "status", "version", "source_document_id", "source_chunk_id"), ("code", "name", "version", "product_class")),
    OntologyShape("certification", "认证", ("name",), ("code", "region_code", "product_class", "status", "source_document_id", "source_chunk_id"), ("code", "name", "region_code", "product_class")),
    OntologyShape("requirement", "技术要求", ("name",), ("region_code", "product_class", "source_document_id", "source_chunk_id"), ("name", "region_code", "product_class")),
    OntologyShape("test_item", "检测项目", ("name",), ("product_class", "source_document_id", "source_chunk_id"), ("name", "product_class")),
)


MARKET_ACCESS_PATH = (
    "product_class",
    "region",
    "regulation",
    "standard",
    "certification",
    "requirement",
    "test_item",
    "evidence",
)


def shape_for(record_type: str) -> OntologyShape | None:
    return next((shape for shape in SHAPES if shape.record_type == record_type), None)


def domains_for_record_type(record_type: str) -> list[str]:
    return [domain.key for domain in KNOWLEDGE_DOMAINS if record_type in domain.record_types]


def ontology_schema() -> dict[str, Any]:
    return {
        "version": "2026.09-business-ontology-v3",
        "principle": (
            "公共本体统一产品、国家/地区、机构、版本、证据等基础对象；"
            "法规知识库、标准知识库、认证知识库按领域扩展；"
            "GMA知识库复用前三类知识并组织市场准入路径，不重复维护同一份事实。"
        ),
        "governance_principle": (
            "来源文件 -> 解析分片 -> 候选知识 -> 本体映射 -> 人工校核 -> 正式知识目录 -> "
            "已确认关系 -> 知识图谱及业务应用。候选内容和候选关系不得直接作为正式结论。"
        ),
        "domains": [item.to_dict() for item in KNOWLEDGE_DOMAINS],
        "market_access_path": list(MARKET_ACCESS_PATH),
        "node_types": [item.to_dict() for item in NODE_TYPES],
        "relations": [item.to_dict() for item in RELATIONS],
        "shapes": [item.to_dict() for item in SHAPES],
        "lifecycle": [
            "document_uploaded",
            "evidence_chunked",
            "candidate_extracted",
            "ontology_mapped",
            "human_edited_and_reviewed",
            "version_applicability_checked",
            "approved_catalog_record",
            "relation_reviewed",
            "graph_projected",
            "business_application",
        ],
    }
