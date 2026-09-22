from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def load_parts(root: Path) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for path in sorted(root.glob("batch-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        sources.extend(payload.get("sources") or [])
    sources.sort(key=lambda x: int(x.get("order") or 999))
    return sources


def markdown_report(sources: list[dict[str, Any]]) -> str:
    lines = [
        "# 首批50站全量深采集验收",
        "",
        "口径：每个站均执行深采集；失败站会继续尝试浏览器化请求、动态渲染、已知官方API/备用网址和已授权登录会话。"
        "robots 禁止、验证码、付费许可或必须人工注册/授权的情况不会绕过，单独标记为受限并给出处理建议。",
        "",
        "| # | 来源 | 结果 | 页数 | 详情 | 附件 | 相关 | 原因/下一步 |",
        "|---:|---|---|---:|---:|---:|---:|---|",
    ]
    for item in sources:
        attempts = item.get("attempts") or []
        best = {}
        rank = {"passed": 4, "partial": 3, "restricted": 2, "failed": 1}
        best_score = -1
        for attempt in attempts:
            assessment = attempt.get("assessment") or {}
            score = rank.get(assessment.get("verdict"), 0)
            if score > best_score:
                best = assessment
                best_score = score
        reason = item.get("restriction_reason") or item.get("recommendation") or ""
        if item.get("final_verdict") == "partial" and not reason:
            reason = item.get("recommendation") or "已有内容但深度/详情链仍需继续优化"
        lines.append(
            f"| {int(item.get('order') or 0)} | {item.get('source_key','')} | "
            f"{item.get('final_verdict','')} | {int(best.get('pages') or 0)} | "
            f"{int(best.get('details') or 0)} | {int(best.get('attachments') or 0)} | "
            f"{int(best.get('relevant') or 0)} | {str(reason).replace('|','/')} |"
        )
    summary = {
        "passed": sum(1 for x in sources if x.get("final_verdict") == "passed"),
        "partial": sum(1 for x in sources if x.get("final_verdict") == "partial"),
        "restricted": sum(1 for x in sources if x.get("final_verdict") == "restricted"),
        "failed": sum(1 for x in sources if x.get("final_verdict") == "failed"),
    }
    lines.extend([
        "",
        "## 汇总",
        "",
        f"- 通过：{summary['passed']}",
        f"- 部分通过：{summary['partial']}",
        f"- 受限：{summary['restricted']}",
        f"- 失败：{summary['failed']}",
        "",
        "## 失败/受限处置原则",
        "",
        "- 403/Request Access：优先查找同机构官方API、XML、RSS、数据下载或备用入口。",
        "- 动态壳：使用真实浏览器渲染后再抽取DOM。",
        "- 需要账号：走官方注册/登录；将授权Cookie/请求头作为受控Secret注入，禁止把凭证写进仓库。",
        "- CAPTCHA/反自动化：不绕过验证；人工完成合法登录或改用官方API/导出。",
        "- robots 禁止：不绕过，改走官方数据服务或人工授权渠道。",
        "- 标准全文许可：保持 metadata_only，全文只在取得授权后处理。",
        "- 超时/限流：缩小单次范围、降低并发、退避重试，优先结构化批量接口。",
    ])
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    sources = load_parts(input_dir)
    if len(sources) != 50:
        raise SystemExit(f"expected 50 source results, got {len(sources)}")
    keys = [str(x.get("source_key") or "") for x in sources]
    if len(set(keys)) != 50:
        raise SystemExit("source results contain duplicates")

    summary = {
        "sources": sources,
        "summary": {
            "total": 50,
            "passed": sum(1 for x in sources if x.get("final_verdict") == "passed"),
            "partial": sum(1 for x in sources if x.get("final_verdict") == "partial"),
            "restricted": sum(1 for x in sources if x.get("final_verdict") == "restricted"),
            "failed": sum(1 for x in sources if x.get("final_verdict") == "failed"),
            "with_remediation_attempt": sum(1 for x in sources if len(x.get("attempts") or []) > 1),
            "with_auth": sum(1 for x in sources if x.get("used_auth")),
        },
    }
    (output_dir / "first50-deep-validation.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "first50-deep-validation.md").write_text(
        markdown_report(sources),
        encoding="utf-8",
    )
    print(json.dumps(summary["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
