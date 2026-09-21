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
    assert "来源台账" in html
    assert 'href="/catalog"' in html
    assert 'href="/changes"' in html
    assert 'href="/map"' in html
    assert 'href="/sources"' in html
    assert "loadBusinessDashboard" in html
    assert "/api/business/dashboard" in html

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

    # Stage23 customer-acceptance sweep: formal pages use one naming and market scope.
    formal_pages = [
        Path("static/index.html"),
        Path("static/catalog.html"),
        Path("static/changes.html"),
        Path("static/map.html"),
        Path("static/graph.html"),
        Path("static/ontology.html"),
        Path("static/admin.html"),
        Path("static/sources.html"),
        Path("static/collection.html"),
    ]
    formal_text = "\n".join(path.read_text(encoding="utf-8") for path in formal_pages)
    for stale in (
        "资料与模型管理",
        "法规变化与待办中心",
        "返回业务平台",
        "样板来源",
        "中国 CN",
        "印度 IN",
        "英国 UK",
        "世界认证地图",
        "正式关系图谱",
        "技术链路",
        "GraphRAG",
        "Neo4j",
        'href="/demo',
        'href="/business_demo',
    ):
        assert stale not in formal_text, stale

    graph_page = Path("static/graph.html").read_text(encoding="utf-8")
    assert "<title>知识图谱 · 法规认证知识平台</title>" in graph_page
    assert "欧盟共享范围 EU" in graph_page
    for code in (
        "DE", "FR", "IT", "ES", "NL", "BE", "SE", "DK", "FI", "AT", "IE",
        "NO", "CH", "GB", "US", "CA", "JP", "KR", "AU", "NZ", "SG",
    ):
        assert f'value="{code}"' in graph_page
    assert "<title>资料及模型配置 · 法规认证知识平台</title>" in formal_text
    assert "<title>变化待办 · 法规认证知识平台</title>" in formal_text
    assert "<h1>来源采集执行</h1>" in formal_text

    print("OK: business navigation through Stage23 customer acceptance sweep is wired")


if __name__ == "__main__":
    main()
