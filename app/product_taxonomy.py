from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.regions import REGION_META, canonical_region_code


TAXONOMY_PATH = Path(__file__).resolve().parent.parent / "data" / "product_taxonomy.json"


class ProductTaxonomyService:
    def __init__(self, path: str | Path = TAXONOMY_PATH):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text(encoding="utf-8"))

    def catalog(self) -> dict[str, Any]:
        return dict(self.data)

    @staticmethod
    def _norm(value: Any) -> str:
        return str(value or "").strip().lower().replace(" ", "").replace("-", "")

    def _resolve_family(self, product: str, product_class: str, attributes: dict[str, Any]) -> dict[str, Any] | None:
        target = self._norm(product + " " + product_class)
        families = list(self.data.get("product_families") or [])
        for family in families:
            terms = [
                family.get("name_zh", ""),
                family.get("name_en", ""),
                *(family.get("aliases") or []),
                *(family.get("formal_match_terms") or []),
            ]
            if any(self._norm(term) and self._norm(term) in target for term in terms):
                return dict(family)
        form = str(attributes.get("product_form") or "")
        if form:
            for family in families:
                if any(str(child.get("id") or "") == form for child in family.get("children") or []):
                    return dict(family)
        return None

    def _resolve_child(self, family: dict[str, Any], product: str, product_class: str, attributes: dict[str, Any]) -> dict[str, Any] | None:
        children = list(family.get("children") or [])
        form = str(attributes.get("product_form") or "").strip()
        if form:
            child = next((item for item in children if item.get("id") == form), None)
            if child:
                return dict(child)
        if bool(attributes.get("wine_storage")):
            child = next((item for item in children if item.get("id") == "wine_storage"), None)
            if child:
                return dict(child)
        if str(attributes.get("use_context") or "") == "commercial":
            child = next((item for item in children if item.get("id") == "commercial_refrigeration"), None)
            if child:
                return dict(child)

        target = self._norm(product + " " + product_class)
        ranked: list[tuple[int, dict[str, Any]]] = []
        for child in children:
            terms = [child.get("name_zh", ""), *(child.get("aliases") or [])]
            score = max((len(self._norm(term)) for term in terms if self._norm(term) and self._norm(term) in target), default=0)
            if score:
                ranked.append((score, dict(child)))
        if ranked:
            ranked.sort(key=lambda item: item[0], reverse=True)
            return ranked[0][1]
        return next((dict(item) for item in children if item.get("id") == "refrigerator"), None)

    def _market_mapping(self, family: dict[str, Any], region_code: str) -> dict[str, Any]:
        code = canonical_region_code(region_code)
        mappings = dict(family.get("market_mappings") or self.data.get("market_mappings") or {})
        if code in set(self.data.get("eu_member_markets") or []):
            base = dict(mappings.get("EU") or {})
            base["inherited_from"] = "EU"
            base["requested_region_code"] = code
            base["requested_region_name"] = str(REGION_META.get(code, {}).get("name") or code)
            base["market_category"] = base.get("market_category", "")
            base["classification_basis"] = (
                str(base.get("classification_basis") or "")
                + f" {base['requested_region_name']}作为欧盟成员市场复用欧盟共享产品规则，并叠加国家执行、语言、市场监管和注册要求。"
            ).strip()
            return base
        mapping = dict(mappings.get(code) or family.get("default_market_mapping") or {})
        mapping["requested_region_code"] = code
        mapping["requested_region_name"] = str(REGION_META.get(code, {}).get("name") or code)
        return mapping

    def resolve(
        self,
        *,
        product: str,
        product_class: str,
        region_code: str,
        attributes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        attrs = dict(attributes or {})
        family = self._resolve_family(product, product_class, attrs)
        if not family:
            return {
                "matched": False,
                "product": product,
                "product_class": product_class,
                "region_code": canonical_region_code(region_code),
                "attributes": attrs,
                "message": "当前标准产品树尚未匹配到该产品，仍按原产品分类查询正式知识。",
                "formal_match_terms": [product_class] if product_class else [],
            }

        child = self._resolve_child(family, product, product_class, attrs)
        mapping = self._market_mapping(family, region_code)
        path = list(family.get("parent_path") or []) + [family.get("name_zh", "")]
        if child:
            path.append(str(child.get("name_zh") or ""))

        missing_attributes: list[str] = []
        attr_defs = {str(item.get("key")): item for item in family.get("decision_attributes") or []}
        for key, item in attr_defs.items():
            if item.get("required") and attrs.get(key) in {None, ""}:
                missing_attributes.append(key)

        applied_dimensions = list(mapping.get("regulatory_dimensions") or [])
        if bool(attrs.get("wireless")) and not any("无线" in item for item in applied_dimensions):
            applied_dimensions.append("无线电/通信功能附加要求")
        conditional_dimensions = dict(family.get("conditional_dimensions") or {})
        if child and child.get("id") in conditional_dimensions:
            for item in conditional_dimensions.get(child.get("id")) or []:
                if item not in applied_dimensions:
                    applied_dimensions.append(item)

        terms = []
        for term in [product_class, product, family.get("name_zh"), *(family.get("formal_match_terms") or [])]:
            value = str(term or "").strip()
            if value and value not in terms:
                terms.append(value)
        if child:
            for term in [child.get("name_zh"), *(child.get("aliases") or [])]:
                value = str(term or "").strip()
                if value and value not in terms:
                    terms.append(value)

        return {
            "matched": True,
            "taxonomy_version": self.data.get("version", ""),
            "family_id": family.get("id"),
            "family_name": family.get("name_zh"),
            "product_type_id": child.get("id") if child else "",
            "product_type_name": child.get("name_zh") if child else "",
            "classification_path": path,
            "attributes": attrs,
            "attribute_definitions": list(family.get("decision_attributes") or []),
            "missing_required_attributes": missing_attributes,
            "formal_match_terms": terms,
            "market_mapping": {
                **mapping,
                "regulatory_dimensions": applied_dimensions,
            },
            "governance_note": (
                "产品分类映射用于缩小各市场适用范围；其中research_seed仅是调研线索，"
                "法规、标准、认证和检测要求仍必须来自已审核正式知识及原始依据。"
            ),
        }
