from __future__ import annotations

from pathlib import Path


def main() -> None:
    html = Path("static/index.html").read_text(encoding="utf-8")
    runtime = Path("app/server.py").read_text(encoding="utf-8")

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

    # Stage19 GMA is a business organization layer over governed knowledge.
    assert "GMA 市场准入" in html
    assert "市场准入业务链路" in html
    assert "已确认准入路径" in html
    assert "需要复核的内容" in html
    assert "/api/gma/access" in html
    assert '"/api/gma/access"' in runtime
    assert "不另建一套重复事实数据" in html
    assert "applyBusinessRoute" in html
    assert "new URLSearchParams(location.search)" in html
    assert "tab==='access'" in html

    # Stage21 regulation Q&A exposes business traceability, not raw execution JSON.
    for label in ("查询范围", "采用依据", "关系核对", "结论形成", "依据不足项"):
        assert label in html
    assert 'id="qaSummary"' in html
    assert 'id="queryScope"' in html
    assert 'id="evidenceGaps"' in html
    assert "查看详细记录" not in html
    assert "trackRaw" not in html
    assert '<pre id="trackRaw"' not in html
    assert "pathCount=Number(v.graph_path_count??((gr.paths||[]).length)||0)" not in html

    # Stage22 management dashboard uses one governed aggregate endpoint.
    for label in ("四类知识库建设概览", "21国市场建设进度", "近30天变化趋势", "产品覆盖情况"):
        assert label in html
    assert "/api/business/dashboard" in html
    assert '"/api/business/dashboard"' in runtime
    assert "准入链完整市场" in html
    assert "GMA知识库" not in html or "GMA 市场准入" in html

    print("OK: business navigation through Stage22 management dashboard is wired")


if __name__ == "__main__":
    main()
