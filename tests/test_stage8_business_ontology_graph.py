from __future__ import annotations

from pathlib import Path


def main() -> None:
    index = Path("static/index.html").read_text(encoding="utf-8")
    graph = Path("static/graph.html").read_text(encoding="utf-8")

    # Ontology stays visible and becomes a business relationship diagram.
    assert "知识本体" in index
    assert 'id="ontologySvg"' in index
    assert "产品分类" in index
    assert "国家/地区" in index
    assert "主管机构" in index
    assert "技术要求" in index
    assert "检测项目" in index
    assert "适用条件" in index
    assert "例外条件" in index
    assert "依据" in index

    # Knowledge graph remains a first-class business view.
    assert "知识图谱" in index
    assert 'id="graphSvg"' in index
    assert "已确认关系" in index
    assert 'href="/graph"' in index
    assert "法规认证关系图" in graph
    assert "认证与合规路径" in graph
    assert "变化影响" in graph
    assert "关联关系校核" in graph
    assert "识别关联关系" in graph

    # Trace remains visible while the technical-pipeline menu remains removed.
    assert "执行轨迹" in index
    assert 'id="track"' in index
    assert "技术链路" not in index
    assert 'data-tab="architecture"' not in index
    assert "/api/architecture" not in index

    print("OK: stage8 business ontology and graph visualization passed")


if __name__ == "__main__":
    main()
