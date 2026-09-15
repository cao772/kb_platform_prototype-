from __future__ import annotations

from app.demo_graph import build_world_certification_demo_graph


def main() -> None:
    graph = build_world_certification_demo_graph()
    results = graph.reachable_requirements("product:demo_appliance", max_depth=5)
    labels = {item["target_label"] for item in results}
    assert any("法规" in label for label in labels)
    assert any("认证" in label for label in labels)
    assert any("要求" in label for label in labels)
    print("OK: demo graph traversal passed")


if __name__ == "__main__":
    main()
