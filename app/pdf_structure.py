from __future__ import annotations

import math
import re
import statistics
from pathlib import Path
from typing import Any, Callable

import fitz


HEADING_PATTERNS = (
    (1, re.compile(r"^(annex|appendix|chapter|part|title|bab)\b", re.I)),
    (2, re.compile(r"^(article|section|pasal)\b", re.I)),
    (2, re.compile(r"^\d+(?:\.\d+){0,4}\s+\S")),
)


def _bbox(value: Any) -> list[float]:
    if not value:
        return []
    return [round(float(x), 2) for x in value]


def _point_inside(box: tuple[float, float, float, float], rect: tuple[float, float, float, float]) -> bool:
    x0, y0, x1, y1 = box
    cx = (x0 + x1) / 2
    cy = (y0 + y1) / 2
    rx0, ry0, rx1, ry1 = rect
    return rx0 <= cx <= rx1 and ry0 <= cy <= ry1


def _image_area_ratio(page: fitz.Page) -> float:
    area = float(page.rect.width * page.rect.height) or 1.0
    total = 0.0
    for image in page.get_images(full=True):
        try:
            for rect in page.get_image_rects(image[0]):
                total += max(0.0, float(rect.width * rect.height))
        except Exception:
            continue
    return min(1.0, total / area)


def _heading_level(text: str, max_font: float, median_font: float) -> int | None:
    clean = " ".join(text.split()).strip()
    if not clean or len(clean) > 180:
        return None
    for level, pattern in HEADING_PATTERNS:
        if pattern.search(clean):
            return level
    letters = [ch for ch in clean if ch.isalpha()]
    if letters and len(clean) <= 90 and sum(ch.isupper() for ch in letters) / len(letters) >= 0.85:
        return 1
    if median_font > 0 and max_font >= median_font * 1.28 and len(clean) <= 140:
        return 2
    return None


def _structured_text_blocks(page: fitz.Page, table_rects: list[tuple[float, float, float, float]]) -> list[dict[str, Any]]:
    payload = page.get_text("dict", sort=True)
    font_sizes: list[float] = []
    for block in payload.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                size = float(span.get("size") or 0)
                if size > 0:
                    font_sizes.append(size)
    median_font = statistics.median(font_sizes) if font_sizes else 0.0

    output: list[dict[str, Any]] = []
    for block in payload.get("blocks", []):
        if block.get("type") != 0:
            continue
        box = tuple(float(x) for x in block.get("bbox", (0, 0, 0, 0)))
        if any(_point_inside(box, rect) for rect in table_rects):
            continue
        lines: list[str] = []
        max_font = 0.0
        for line in block.get("lines", []):
            parts: list[str] = []
            for span in line.get("spans", []):
                text = str(span.get("text") or "")
                if text.strip():
                    parts.append(text)
                max_font = max(max_font, float(span.get("size") or 0))
            joined = "".join(parts).strip()
            if joined:
                lines.append(joined)
        text = "\n".join(lines).strip()
        if not text:
            continue
        heading_level = _heading_level(text, max_font, median_font)
        output.append({
            "type": "heading" if heading_level else "paragraph",
            "text": text,
            "bbox": _bbox(box),
            "heading_level": heading_level,
            "font_size": round(max_font, 2),
        })
    return output


def _extract_tables(page: fitz.Page) -> tuple[list[dict[str, Any]], list[tuple[float, float, float, float]]]:
    tables: list[dict[str, Any]] = []
    rects: list[tuple[float, float, float, float]] = []
    try:
        finder = page.find_tables()
    except Exception:
        return tables, rects
    for index, table in enumerate(finder.tables, 1):
        try:
            rows = table.extract()
        except Exception:
            rows = []
        normalized: list[list[str]] = []
        for row in rows or []:
            normalized.append([str(cell or "").strip() for cell in row])
        if not any(any(cell for cell in row) for row in normalized):
            continue
        bbox = tuple(float(x) for x in table.bbox)
        rects.append(bbox)
        text = "\n".join(" | ".join(row) for row in normalized)
        tables.append({
            "type": "table",
            "text": text,
            "bbox": _bbox(bbox),
            "rows": normalized,
            "table_index": index,
        })
    return tables, rects


def _text_quality(text: str) -> dict[str, Any]:
    clean = text.strip()
    chars = len(clean)
    if not clean:
        return {
            "text_chars": 0,
            "replacement_ratio": 0.0,
            "single_char_line_ratio": 0.0,
            "average_line_length": 0.0,
        }
    replacement = sum(clean.count(ch) for ch in ("�", "\ufffd"))
    lines = [line.strip() for line in clean.splitlines() if line.strip()]
    single = sum(1 for line in lines if len(line) <= 2)
    return {
        "text_chars": chars,
        "replacement_ratio": round(replacement / max(chars, 1), 4),
        "single_char_line_ratio": round(single / max(len(lines), 1), 4),
        "average_line_length": round(sum(len(line) for line in lines) / max(len(lines), 1), 2),
    }


def _quality_score(
    *,
    text_chars: int,
    replacement_ratio: float,
    single_char_line_ratio: float,
    page_type: str,
    table_count: int,
    structured_block_count: int,
) -> int:
    if text_chars <= 0:
        return 20
    score = 55
    if text_chars >= 500:
        score += 24
    elif text_chars >= 180:
        score += 17
    elif text_chars >= 60:
        score += 8
    if replacement_ratio <= 0.005:
        score += 10
    elif replacement_ratio > 0.03:
        score -= 18
    if single_char_line_ratio <= 0.12:
        score += 6
    elif single_char_line_ratio > 0.35:
        score -= 12
    if structured_block_count >= 3:
        score += 4
    if table_count:
        score += 2
    if page_type in {"scan", "unreadable"}:
        score -= 30
    elif page_type == "mixed":
        score -= 4
    return max(0, min(100, int(round(score))))


def _quality_band(score: int) -> str:
    if score >= 85:
        return "high"
    if score >= 65:
        return "medium"
    return "low"


def _detect_complex_layout(blocks: list[dict[str, Any]], page_width: float) -> bool:
    text_blocks = [b for b in blocks if b.get("type") in {"heading", "paragraph"} and b.get("bbox")]
    if len(text_blocks) < 8 or page_width <= 0:
        return False
    lefts = sorted(float(b["bbox"][0]) for b in text_blocks)
    normalized = {round(x / page_width, 1) for x in lefts}
    return len(normalized) >= 4


def _page_type(*, text_chars: int, image_ratio: float, table_count: int, complex_layout: bool) -> str:
    if text_chars < 40 and image_ratio >= 0.30:
        return "scan"
    if table_count:
        return "table_rich"
    if complex_layout:
        return "complex_layout"
    if text_chars >= 40 and image_ratio >= 0.12:
        return "mixed"
    if text_chars < 180:
        return "sparse_text"
    return "native_text"


def _hierarchy_apply(blocks: list[dict[str, Any]], stack: list[str]) -> None:
    for block in blocks:
        if block.get("type") == "heading":
            level = int(block.get("heading_level") or 1)
            while len(stack) >= level:
                stack.pop()
            stack.append(" ".join(str(block.get("text") or "").split()))
            block["hierarchy"] = list(stack)
            block["section_title"] = stack[-1] if stack else ""
        else:
            block["hierarchy"] = list(stack)
            block["section_title"] = stack[-1] if stack else ""


def extract_pdf_structure(
    path: str | Path,
    *,
    ocr_enabled: bool = False,
    max_vision_pages: int = 6,
    ocr_callback: Callable[[bytes, int], tuple[str, dict[str, Any]]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    pdf_path = Path(path)
    doc = fitz.open(str(pdf_path))
    output_blocks: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    hierarchy_stack: list[str] = []
    ocr_pages = 0

    try:
        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            page_no = page_index + 1
            native_text = (page.get_text("text", sort=True) or "").strip()
            quality = _text_quality(native_text)
            image_ratio = _image_area_ratio(page)
            tables, table_rects = _extract_tables(page)
            structured = _structured_text_blocks(page, table_rects)
            complex_layout = _detect_complex_layout(structured, float(page.rect.width))
            page_type = _page_type(
                text_chars=int(quality["text_chars"]),
                image_ratio=image_ratio,
                table_count=len(tables),
                complex_layout=complex_layout,
            )
            score = _quality_score(
                text_chars=int(quality["text_chars"]),
                replacement_ratio=float(quality["replacement_ratio"]),
                single_char_line_ratio=float(quality["single_char_line_ratio"]),
                page_type=page_type,
                table_count=len(tables),
                structured_block_count=len(structured),
            )

            issues: list[str] = []
            strategy = "layout_text"
            if page_type == "scan":
                issues.append("scanned_page")
            if page_type == "table_rich":
                issues.append("table_detected")
                strategy = "layout_text+table"
            if page_type == "complex_layout":
                issues.append("complex_layout")
            if quality["replacement_ratio"] > 0.03:
                issues.append("text_layer_noise")
            if quality["single_char_line_ratio"] > 0.35:
                issues.append("fragmented_lines")
            if int(quality["text_chars"]) < 60:
                issues.append("sparse_text")

            should_ocr = (
                page_type == "scan"
                or (
                    page_type in {"mixed", "sparse_text"}
                    and score < 55
                    and image_ratio >= 0.12
                )
            )
            page_blocks = structured + tables
            ocr_text = ""
            ocr_trace: dict[str, Any] = {}

            if should_ocr and ocr_enabled and ocr_callback and ocr_pages < max_vision_pages:
                pix = page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), alpha=False)
                ocr_text, ocr_trace = ocr_callback(pix.tobytes("png"), page_no)
                ocr_text = str(ocr_text or "").strip()
                if ocr_text:
                    ocr_pages += 1
                    strategy = "vision_ocr" if page_type == "scan" else "layout_text+vision_review"
                    if ocr_trace.get("mode") == "local_ocr":
                        strategy = "local_ocr" if page_type == "scan" else "layout_text+local_ocr"
                    if page_type == "scan":
                        page_blocks = [{
                            "type": "ocr_text",
                            "text": ocr_text,
                            "bbox": [0.0, 0.0, round(float(page.rect.width), 2), round(float(page.rect.height), 2)],
                            "heading_level": None,
                        }]
                    else:
                        page_blocks.append({
                            "type": "ocr_review",
                            "text": ocr_text,
                            "bbox": [],
                            "heading_level": None,
                        })
                    score = max(score, 76)
                    issues = [item for item in issues if item not in {"scanned_page", "sparse_text"}]
                else:
                    issues.append("ocr_failed")
            elif should_ocr:
                strategy = "ocr_required"
                issues.append("ocr_required")

            _hierarchy_apply(page_blocks, hierarchy_stack)
            for block in page_blocks:
                block["page_no"] = page_no
                output_blocks.append(block)

            preview = "\n".join(str(block.get("text") or "") for block in page_blocks).strip()
            page_report = {
                "page_no": page_no,
                "page_type": page_type,
                "strategy": strategy,
                "quality_score": score,
                "quality_band": _quality_band(score),
                "text_chars": int(quality["text_chars"]),
                "word_count": len(page.get_text("words")),
                "image_count": len(page.get_images(full=True)),
                "image_area_ratio": round(image_ratio, 3),
                "table_count": len(tables),
                "heading_count": sum(1 for block in page_blocks if block.get("type") == "heading"),
                "block_count": len(page_blocks),
                "issues": sorted(set(issues)),
                "parsed_text": preview,
                "blocks": [
                    {
                        "type": block.get("type"),
                        "text": block.get("text"),
                        "bbox": block.get("bbox", []),
                        "heading_level": block.get("heading_level"),
                        "section_title": block.get("section_title", ""),
                        "hierarchy": block.get("hierarchy", []),
                        "rows": block.get("rows", []) if block.get("type") == "table" else [],
                    }
                    for block in page_blocks
                ],
            }
            if ocr_trace:
                page_report["ocr_trace"] = ocr_trace
            pages.append(page_report)
    finally:
        doc.close()

    summary = {
        "page_count": len(pages),
        "native_text_pages": sum(1 for page in pages if page["page_type"] not in {"scan"}),
        "scan_pages": sum(1 for page in pages if page["page_type"] == "scan"),
        "table_pages": sum(1 for page in pages if page["table_count"] > 0),
        "complex_layout_pages": sum(1 for page in pages if page["page_type"] == "complex_layout"),
        "ocr_pages": ocr_pages,
        "unreadable_pages": sum(1 for page in pages if any(issue in page["issues"] for issue in ("ocr_required", "ocr_failed"))),
        "high_quality_pages": sum(1 for page in pages if page["quality_band"] == "high"),
        "medium_quality_pages": sum(1 for page in pages if page["quality_band"] == "medium"),
        "low_quality_pages": sum(1 for page in pages if page["quality_band"] == "low"),
        "average_quality": round(
            statistics.mean(page["quality_score"] for page in pages) if pages else 0.0,
            1,
        ),
        "issue_pages": [page["page_no"] for page in pages if page["issues"]],
        "heading_count": sum(page["heading_count"] for page in pages),
        "table_count": sum(page["table_count"] for page in pages),
    }
    return output_blocks, {"summary": summary, "pages": pages}
