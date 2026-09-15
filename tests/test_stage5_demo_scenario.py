from __future__ import annotations

import tempfile
from pathlib import Path

from app.demo_scenario import DemoScenarioService


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        service = DemoScenarioService(Path(tmp) / "demo.db")
        snapshot = service.seed()

        assert snapshot["demo_only"] is True
        assert "虚构" in snapshot["warning"]
        assert snapshot["stats"]["documents"] == 2
        assert snapshot["stats"]["compliance_records"] == 6
        assert snapshot["graph"]["summary"]["nodes"] == 5
        assert snapshot["graph"]["summary"]["edges"] == 4
        assert snapshot["paths"]["status"] == "ready"
        assert snapshot["paths"]["summary"]["paths"] >= 3
        assert snapshot["access"]["status"] == "evidence_ready"
        assert snapshot["impact"]["summary"]["downstream_count"] >= 3

        answer = service.answer("演示智能家电出口欧盟需要核对哪些法规、认证和检测路径？")
        assert answer["demo_only"] is True
        assert answer["verification"]["grounded"] is True
        assert answer["graph_trace"]["status"] == "governed_graph_connected"
        assert any("approved_graph" in item.get("channels", []) for item in answer["citations"])
        assert answer["graph_trace"]["edge_count"] == 4

        second = service.snapshot()
        assert second["stats"]["documents"] == 2
        assert second["stats"]["compliance_records"] == 6
        assert service.graph.summary()["approved"] == 5

    print("OK: stage5 isolated customer demo workflow passed")


if __name__ == "__main__":
    main()
