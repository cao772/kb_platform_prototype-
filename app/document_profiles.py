"""Evidence fields shared by documents of the same structure. Keep source wording and locators."""

from __future__ import annotations

import re

FAMILIES = {"regulation": "法规文件", "standard": "标准", "certification": "认证规则"}
FIELDS = {
    "regulation": {
        "文件编号": r"(?:Regulation|Directive|Decision|Permen(?:perin)?|条例|指令)\s*(?:\([A-Z]+\)\s*)?(?:No\.?\s*)?\d[\w./ –-]{1,40}",
        "施行与过渡": r"施行|生效|过渡期|enter(?:s)? into force|date of application|berlaku",
        "主管机构": r"发布机关|主管部门|competent authorit(?:y|ies)|issuing authority|menteri",
    },
    "standard": {
        "标准编号": r"\b(?:GB\s*/\s*T|GB|IEC|ISO|EN|AS\s*/?\s*NZS|SNI|KS)\s*[-A-Z]*\s*\d[\d.:/–-]*",
        "规范性引用": r"规范性引用|normative references|rujukan normatif",
        "试验与判定": r"试验|判定|shall be tested|test method|acceptance criteria|metode uji",
    },
    "certification": {
        "规则编号": r"(?:CVC|CNCA|CQC|GSO|SASO)[\s:./-]*[A-Z0-9][A-Z0-9.:/-]*",
        "认证模式": r"认证模式|合格评定模式|certification (?:scheme|model)|conformity assessment procedure",
        "证书有效期": r"证书有效期|有效期限|certificate validity|validity of (?:the )?certificate",
        "监督与检查": r"监督检查|工厂检查|监督周期|surveillance|factory inspection",
    },
}
PATTERNS = {
    kind: {label: re.compile(expr, re.I) for label, expr in values.items()} for kind, values in FIELDS.items()
}


def extract_profile(family, chunks):
    """Index short literal matches by field; full text remains in the original chunks."""
    fields = {key: [] for key in FIELDS[family]}
    for chunk in chunks:
        text = chunk["text"]
        for label, pattern in PATTERNS[family].items():
            if len(fields[label]) >= 8:
                continue
            m = pattern.search(text)
            if m:
                fields[label].append(
                    {
                        "value": m[0],
                        "excerpt": text[max(0, m.start() - 45) : m.end() + 160],
                        "page": chunk["metadata"].get("page_no"),
                        "chunk": chunk["chunk_index"],
                    }
                )
    return fields
