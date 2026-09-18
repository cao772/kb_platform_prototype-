from __future__ import annotations

from typing import Any

from app.ontology import RELATIONS


FORMAL_NODE_TYPES = {"regulation", "standard", "certification", "requirement", "test_item"}


class OntologyConstraintService:
    """Validate formal graph relations against the business ontology.

    Only constraints that can be evaluated with current formal record types are
    enforced here. Candidate/approved relations remain human-governed.
    """

    def __init__(self) -> None:
        self._relations = {item.key: item for item in RELATIONS}

    def validate_relation(
        self,
        relation_type: str,
        source_type: str,
        target_type: str,
    ) -> dict[str, Any]:
        relation = self._relations.get(str(relation_type or "").strip())
        if not relation:
            return {
                "valid": False,
                "relation_type": relation_type,
                "reason": "本体未定义该关系类型",
            }
        source = str(source_type or "").strip()
        target = str(target_type or "").strip()
        if source not in relation.source_types:
            return {
                "valid": False,
                "relation_type": relation.key,
                "reason": f"关系“{relation.label}”不允许由 {source or '未知类型'} 发出",
                "allowed_source_types": list(relation.source_types),
                "allowed_target_types": list(relation.target_types),
            }
        if target not in relation.target_types:
            return {
                "valid": False,
                "relation_type": relation.key,
                "reason": f"关系“{relation.label}”不允许指向 {target or '未知类型'}",
                "allowed_source_types": list(relation.source_types),
                "allowed_target_types": list(relation.target_types),
            }
        return {
            "valid": True,
            "relation_type": relation.key,
            "relation_label": relation.label,
            "source_type": source,
            "target_type": target,
            "reason": "符合当前业务本体关系约束",
        }

    def formal_constraints(self) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        for relation in RELATIONS:
            sources = [item for item in relation.source_types if item in FORMAL_NODE_TYPES]
            targets = [item for item in relation.target_types if item in FORMAL_NODE_TYPES]
            if not sources or not targets:
                continue
            output.append({
                "relation_type": relation.key,
                "relation_label": relation.label,
                "source_types": sources,
                "target_types": targets,
                "description": relation.description,
            })
        return output
