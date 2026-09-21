from __future__ import annotations

import argparse
import csv
import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from app.parsers import parse_file


ROOT = Path(__file__).resolve().parents[1]
CASE_MANIFEST = ROOT / "data" / "poc_parser_cases.json"


def _find_files(root: Path) -> list[Path]:
    supported = {".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".md", ".json", ".xml", ".html"}
    return sorted(
        path for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in supported
        and "__MACOSX" not in path.parts
        and not path.name.startswith("._")
    )


def _case_for(path: Path, cases: list[dict[str, Any]]) -> dict[str, Any] | None:
    name = path.name.lower()
    for case in cases:
        hint = str(case.get("filename_hint") or "").lower()
        if hint and hint in name:
            return case
    return None


def _summary_for(path: Path) -> dict[str, Any]:
    parsed = parse_file(path)
    meta = parsed.metadata or {}
    summary = dict(meta.get("parse_summary") or {})
    return {
        "filename": path.name,
        "suffix": path.suffix.lower(),
        "parser": parsed.parser,
        "page_count": int(summary.get("page_count") or meta.get("page_count") or 0),
        "average_quality": summary.get("average_quality"),
        "high_quality_pages": int(summary.get("high_quality_pages") or 0),
        "medium_quality_pages": int(summary.get("medium_quality_pages") or 0),
        "low_quality_pages": int(summary.get("low_quality_pages") or 0),
        "scan_pages": int(summary.get("scan_pages") or 0),
        "table_pages": int(summary.get("table_pages") or 0),
        "ocr_pages": int(summary.get("ocr_pages") or 0),
        "unreadable_pages": int(summary.get("unreadable_pages") or 0),
        "issue_pages": summary.get("issue_pages") or [],
        "heading_count": int(summary.get("heading_count") or 0),
        "table_count": int(summary.get("table_count") or 0),
        "text_chars": len(parsed.full_text),
        "block_count": len(parsed.blocks),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = [
        "case_id", "category", "filename", "parser", "page_count", "average_quality",
        "high_quality_pages", "medium_quality_pages", "low_quality_pages", "scan_pages",
        "table_pages", "ocr_pages", "unreadable_pages", "heading_count", "table_count",
        "text_chars", "block_count", "issue_pages", "focus",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            item = {key: row.get(key, "") for key in columns}
            item["issue_pages"] = ",".join(str(v) for v in row.get("issue_pages") or [])
            item["focus"] = "；".join(row.get("focus") or [])
            writer.writerow(item)


def _write_markdown(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = [
        "# 文档解析POC回归报告",
        "",
        "本报告只评价文档解析质量；法规字段是否能抽取到属于下一阶段的结构化抽取能力，不与解析分数混算。",
        "",
        "|样本|类型|页数|平均质量|扫描页|表格页|OCR页|异常页|标题/条款|",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"|{row.get('filename','')}|{row.get('category','')}|{row.get('page_count',0)}|"
            f"{row.get('average_quality','-')}|{row.get('scan_pages',0)}|{row.get('table_pages',0)}|"
            f"{row.get('ocr_pages',0)}|{len(row.get('issue_pages') or [])}|{row.get('heading_count',0)}|"
        )
    lines.extend(["", "## 样本考察点", ""])
    for row in rows:
        lines.append(f"### {row.get('filename','')}")
        lines.append(f"- 类型：{row.get('category','未归类')}")
        lines.append(f"- 重点：{'、'.join(row.get('focus') or []) or '未配置'}")
        lines.append(f"- 解析器：{row.get('parser','')}")
        issues = row.get("issue_pages") or []
        lines.append(f"- 异常页：{','.join(map(str,issues)) if issues else '无'}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run POC document parser regression and export reports.")
    parser.add_argument("--input", required=True, help="Directory or ZIP containing POC documents")
    parser.add_argument("--output-dir", default=str(ROOT / "data" / "poc_parser_reports"))
    args = parser.parse_args()

    input_path = Path(args.input).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    cases = json.loads(CASE_MANIFEST.read_text(encoding="utf-8"))

    tmp: tempfile.TemporaryDirectory[str] | None = None
    if input_path.suffix.lower() == ".zip":
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        with zipfile.ZipFile(input_path) as zf:
            for info in zf.infolist():
                if info.is_dir() or info.filename.startswith("__MACOSX/") or "/._" in info.filename:
                    continue
                try:
                    zf.extract(info, root)
                except Exception:
                    continue
    else:
        root = input_path

    rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for path in _find_files(root):
        case = _case_for(path, cases)
        if not case and path.suffix.lower() != ".pdf":
            continue
        try:
            row = _summary_for(path)
            row.update({
                "case_id": (case or {}).get("case_id", ""),
                "category": (case or {}).get("category", "其他PDF"),
                "focus": (case or {}).get("focus", []),
                "expected_route": (case or {}).get("expected_route", ""),
            })
            rows.append(row)
            print(
                f"{path.name}: parser={row['parser']} pages={row['page_count']} "
                f"quality={row['average_quality']} tables={row['table_pages']} "
                f"scan={row['scan_pages']} issues={len(row['issue_pages'])}"
            )
        except Exception as exc:
            failures.append({"filename": path.name, "error": f"{type(exc).__name__}: {exc}"})
            print(f"FAILED {path.name}: {exc}")

    payload = {
        "input": str(input_path),
        "cases": rows,
        "failures": failures,
        "summary": {
            "files": len(rows),
            "failures": len(failures),
            "pdf_files": sum(1 for row in rows if row["suffix"] == ".pdf"),
            "total_pages": sum(int(row.get("page_count") or 0) for row in rows),
            "total_table_pages": sum(int(row.get("table_pages") or 0) for row in rows),
            "total_scan_pages": sum(int(row.get("scan_pages") or 0) for row in rows),
            "total_ocr_pages": sum(int(row.get("ocr_pages") or 0) for row in rows),
        },
    }
    (output_dir / "parser_poc_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_csv(output_dir / "parser_poc_report.csv", rows)
    _write_markdown(output_dir / "parser_poc_report.md", rows)
    if tmp is not None:
        tmp.cleanup()
    print(f"Reports written to {output_dir}")


if __name__ == "__main__":
    main()
