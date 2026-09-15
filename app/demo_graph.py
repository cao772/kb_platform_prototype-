from __future__ import annotations

from app.graph_engine import KnowledgeGraph


def build_world_certification_demo_graph() -> KnowledgeGraph:
    """Pure demo data for UI/graph traversal tests; never used as legal evidence."""
    graph = KnowledgeGraph()
    graph.add_node("product:demo_appliance", "示例家用电器", "product", demo=True)
    graph.add_node("class:demo_appliance", "示例产品分类", "product_class", demo=True)
    graph.add_node("region:eu", "欧盟", "region", code="EU", demo=True)
    graph.add_node("reg:demo_eu", "示例法规要求A（演示数据）", "regulation", status="demo_only", demo=True)
    graph.add_node("cert:demo_eu", "示例认证路径A（演示数据）", "certification", status="demo_only", demo=True)
    graph.add_node("req:demo_eu", "示例测试/标识要求A（演示数据）", "requirement", status="demo_only", demo=True)

    graph.add_edge("product:demo_appliance", "is_a", "class:demo_appliance", 1.0)
    graph.add_edge("class:demo_appliance", "in_region", "region:eu", 1.0)
    graph.add_edge("class:demo_appliance", "must_consider", "reg:demo_eu", 0.95)
    graph.add_edge("reg:demo_eu", "requires", "cert:demo_eu", 0.9)
    graph.add_edge("cert:demo_eu", "contains_requirement", "req:demo_eu", 0.88)
    return graph
