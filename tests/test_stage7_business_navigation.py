from __future__ import annotations

from pathlib import Path


def main() -> None:
    html = Path("static/index.html").read_text(encoding="utf-8")

    assert "知识本体" in html
    assert "知识图谱" in html
    assert "执行轨迹" in html
    assert 'data-tab="ontology"' in html
    assert 'data-tab="graph"' in html
    assert 'id="track"' in html
    assert 'href="/graph"' in html

    assert "技术链路" not in html
    assert 'data-tab="architecture"' not in html
    assert "/api/architecture" not in html

    # Business UI should still expose the operations needed for real use.
    assert "资料及模型配置" in html
    assert "/api/processing/start" in html
    assert "/api/answer-v2" in html
    assert "/api/ontology" in html
    assert "/api/graph/project" in html

    # Stage18 business dashboard: the first screen should lead with business work,
    # not implementation terminology or a pure upload console.
    assert "近期业务变化" in html
    assert "重点市场" in html
    assert "目标市场覆盖" in html
    assert "产品准入" in html
    assert "正式知识目录" in html
    assert "变化待办" in html
    assert "法规认证地图" in html
    assert 'href="/catalog"' in html
    assert 'href="/changes"' in html
    assert 'href="/map"' in html
    assert "loadBusinessDashboard" in html
    assert "/api/map/overview" in html
    assert "/api/change-watch/tasks" in html

    print("OK: business navigation and Stage18 dashboard expose governed business workflows")


if __name__ == "__main__":
    main()
