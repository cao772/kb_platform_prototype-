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
    assert "资料与模型管理" in html
    assert "/api/processing/start" in html
    assert "/api/answer-v2" in html
    assert "/api/ontology" in html
    assert "/api/graph/project" in html

    print("OK: stage7 business navigation keeps ontology graph trace and removes technical pipeline")


if __name__ == "__main__":
    main()
