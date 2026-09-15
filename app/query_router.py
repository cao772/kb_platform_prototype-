from __future__ import annotations

from dataclasses import asdict, dataclass


REGION_ALIASES = {
    "欧盟": "EU",
    "欧洲联盟": "EU",
    "美国": "US",
    "中国": "CN",
    "英国": "UK",
    "日本": "JP",
    "韩国": "KR",
    "加拿大": "CA",
    "澳大利亚": "AU",
    "印度": "IN",
}


@dataclass(frozen=True)
class QueryPlan:
    route: str
    reason: str
    needs_graph: bool
    needs_global_summary: bool
    retrieval_channels: tuple[str, ...]
    regions: tuple[str, ...]
    confidence: float

    def to_dict(self) -> dict:
        data = asdict(self)
        data["retrieval_channels"] = list(self.retrieval_channels)
        data["regions"] = list(self.regions)
        return data


class QueryRouter:
    """A deterministic first-stage router.

    The router intentionally avoids free-form agent decisions. Production systems can
    replace or augment it with a classifier/LLM, but a rules-first baseline keeps the
    behavior auditable and easy to test.
    """

    compliance_terms = (
        "法规",
        "认证",
        "合规",
        "准入",
        "出口",
        "GMA",
        "标准",
        "检测",
        "证书",
        "要求",
        "适用",
    )
    global_terms = (
        "整体",
        "全局",
        "主要主题",
        "趋势",
        "汇总",
        "概览",
        "有哪些类别",
        "总体",
    )

    def plan(self, question: str) -> QueryPlan:
        text = (question or "").strip()
        regions = tuple(code for name, code in REGION_ALIASES.items() if name in text)

        compliance_hits = [term for term in self.compliance_terms if term.lower() in text.lower()]
        global_hits = [term for term in self.global_terms if term in text]

        if global_hits:
            return QueryPlan(
                route="global_overview",
                reason=f"命中全局/汇总意图：{', '.join(global_hits[:3])}",
                needs_graph=True,
                needs_global_summary=True,
                retrieval_channels=("keyword", "semantic", "graph"),
                regions=regions,
                confidence=0.9,
            )

        if compliance_hits:
            return QueryPlan(
                route="compliance_path",
                reason=f"命中法规/认证/准入意图：{', '.join(compliance_hits[:4])}",
                needs_graph=True,
                needs_global_summary=False,
                retrieval_channels=("keyword", "semantic", "graph"),
                regions=regions,
                confidence=0.92,
            )

        return QueryPlan(
            route="evidence_qa",
            reason="普通知识问答，优先证据检索与来源引用",
            needs_graph=False,
            needs_global_summary=False,
            retrieval_channels=("keyword", "semantic"),
            regions=regions,
            confidence=0.82,
        )
