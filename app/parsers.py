from __future__ import annotations

import io
import json
import os
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from app.docx_parser import parse_docx
from app.model_gateway import call_vision_ocr, current_model_config
from app.runtime_settings import current_parser_settings
from app.vendor_path import activate_vendor

activate_vendor()


@dataclass
class StandardBlock:
    block_type: str
    text: str
    page_no: int | None = None
    section_title: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class StandardDocument:
    source_path: str
    filename: str
    title: str
    mime_type: str
    parser: str
    blocks: list[StandardBlock]
    metadata: dict = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        return "\n".join(block.text for block in self.blocks if block.text.strip())


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)


def _parser_backend() -> str:
    return (os.getenv("KB_PARSER_BACKEND", "") or current_parser_settings().get("backend") or "lightweight").lower()


def _ocr_enabled() -> bool:
    mode = str(current_parser_settings().get("ocr_mode") or "auto").lower()
    if mode == "off":
        return False
    return current_model_config("vision").configured


def parse_file(path: str | Path) -> StandardDocument:
    file_path = Path(path)
    if _parser_backend() == "docling":
        try:
            return parse_docling_standard(file_path)
        except Exception:
            pass
    suffix = file_path.suffix.lower()
    if suffix == ".docx":
        return parse_docx_standard(file_path)
    if suffix == ".pdf":
        return parse_pdf_standard(file_path)
    if suffix == ".xlsx":
        return parse_xlsx_standard(file_path)
    if suffix == ".pptx":
        return parse_pptx_standard(file_path)
    if suffix in {".txt", ".md", ".csv"}:
        return parse_text_standard(file_path)
    if suffix == ".json":
        return parse_json_standard(file_path)
    if suffix in {".html", ".htm"}:
        return parse_html_standard(file_path)
    if suffix in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}:
        return parse_image_standard(file_path)
    raise ValueError(f"Unsupported file type: {file_path.suffix}")


def parse_docx_standard(path: Path) -> StandardDocument:
    parsed = parse_docx(path)
    blocks = [StandardBlock("paragraph", text) for text in parsed.paragraphs]
    for index, table in enumerate(parsed.tables, 1):
        blocks.append(StandardBlock(
            "table",
            "\n".join(" | ".join(cell for cell in row if cell) for row in table),
            metadata={"table_index": index},
        ))
    return StandardDocument(
        str(path.resolve()), path.name, parsed.title,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "docx-xml", blocks,
    )


def _render_pdf_page(path: Path, page_index: int) -> bytes | None:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return None
    doc = fitz.open(str(path))
    try:
        page = doc.load_page(page_index)
        pix = page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), alpha=False)
        return pix.tobytes("png")
    finally:
        doc.close()


def parse_pdf_standard(path: Path) -> StandardDocument:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    blocks: list[StandardBlock] = []
    ocr_pages = 0
    empty_pages = 0
    max_vision_pages = int(current_parser_settings().get("vision_max_pages") or 6)
    for i, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or "").strip()
        if text:
            blocks.append(StandardBlock("page_text", text, page_no=i, metadata={"source": "text_layer"}))
            continue
        empty_pages += 1
        if _ocr_enabled() and ocr_pages < max_vision_pages:
            rendered = _render_pdf_page(path, i - 1)
            if rendered:
                recognized, trace = call_vision_ocr(
                    rendered,
                    "image/png",
                    instruction=(
                        f"这是PDF第{i}页。请识别页面中的正文、条款编号、表格文字、日期、法规标准编号和认证要求。"
                        "尽量保持阅读顺序；无法确认的字符不要猜测。"
                    ),
                )
                if recognized:
                    ocr_pages += 1
                    blocks.append(StandardBlock("page_ocr", recognized, page_no=i, metadata={"source": "vision_model", "trace": trace}))
                    continue
        blocks.append(StandardBlock("page_unreadable", f"[第{i}页未识别到可提取文字]", page_no=i, metadata={"source": "unreadable"}))

    parser_name = "pypdf+vision" if ocr_pages else "pypdf"
    return StandardDocument(
        str(path.resolve()), path.name, path.stem, "application/pdf", parser_name,
        blocks,
        {
            "page_count": len(reader.pages),
            "empty_text_pages": empty_pages,
            "vision_ocr_pages": ocr_pages,
            "ocr_available": _ocr_enabled(),
        },
    )


def parse_xlsx_standard(path: Path) -> StandardDocument:
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    blocks: list[StandardBlock] = []
    total_rows = 0
    for sheet in wb.worksheets:
        lines = []
        for row_index, row in enumerate(sheet.iter_rows(values_only=True), 1):
            if row_index > 2000:
                lines.append("[工作表内容过长，已截断]")
                break
            cells = [str(value).strip() for value in row[:100] if value is not None and str(value).strip()]
            if cells:
                lines.append(" | ".join(cells))
                total_rows += 1
        if lines:
            blocks.append(StandardBlock(
                "sheet",
                "\n".join(lines),
                section_title=sheet.title,
                metadata={"sheet_name": sheet.title, "non_empty_rows": len(lines)},
            ))
    return StandardDocument(
        str(path.resolve()), path.name, path.stem,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "openpyxl", blocks, {"sheet_count": len(wb.sheetnames), "non_empty_rows": total_rows},
    )


def parse_pptx_standard(path: Path) -> StandardDocument:
    from pptx import Presentation
    deck = Presentation(str(path))
    blocks: list[StandardBlock] = []
    for index, slide in enumerate(deck.slides, 1):
        texts: list[str] = []
        for shape in slide.shapes:
            text = getattr(shape, "text", "")
            if text and text.strip():
                texts.append(text.strip())
        if texts:
            blocks.append(StandardBlock(
                "slide",
                "\n".join(texts),
                page_no=index,
                metadata={"slide_index": index},
            ))
    return StandardDocument(
        str(path.resolve()), path.name, path.stem,
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "python-pptx", blocks, {"slide_count": len(deck.slides)},
    )


def parse_text_standard(path: Path) -> StandardDocument:
    text = path.read_text(encoding="utf-8", errors="ignore")
    return StandardDocument(
        str(path.resolve()), path.name, path.stem, "text/plain", "plain-text",
        [StandardBlock("text", text)],
    )


def parse_json_standard(path: Path) -> StandardDocument:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    try:
        text = json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        text = raw
    return StandardDocument(
        str(path.resolve()), path.name, path.stem, "application/json", "json",
        [StandardBlock("json", text)],
    )


def parse_html_standard(path: Path) -> StandardDocument:
    parser = _HTMLTextExtractor()
    parser.feed(path.read_text(encoding="utf-8", errors="ignore"))
    return StandardDocument(
        str(path.resolve()), path.name, path.stem, "text/html", "html-parser",
        [StandardBlock("html_text", "\n".join(parser.parts))],
    )


def parse_image_standard(path: Path) -> StandardDocument:
    from PIL import Image

    image = Image.open(path)
    mime_type = f"image/{'jpeg' if path.suffix.lower() in {'.jpg', '.jpeg'} else path.suffix.lower().lstrip('.')}"
    metadata = {
        "width": image.width,
        "height": image.height,
        "mode": image.mode,
    }
    if _ocr_enabled():
        raw = path.read_bytes()
        recognized, trace = call_vision_ocr(raw, mime_type)
        if recognized:
            metadata.update({"ocr_status": "completed", "ocr_trace": trace})
            return StandardDocument(
                str(path.resolve()), path.name, path.stem, mime_type, "vision-model",
                [StandardBlock("image_ocr", recognized, metadata=metadata)], metadata,
            )
        metadata.update({"ocr_status": "failed", "ocr_trace": trace})
    else:
        metadata["ocr_status"] = "model_not_configured"
    text = f"[图片待识别] {path.name} {image.width}x{image.height}"
    return StandardDocument(
        str(path.resolve()), path.name, path.stem, mime_type, "image-metadata",
        [StandardBlock("image", text, metadata=metadata)], metadata,
    )


def parse_docling_standard(path: Path) -> StandardDocument:
    from docling.document_converter import DocumentConverter
    result = DocumentConverter().convert(str(path))
    markdown = result.document.export_to_markdown()
    return StandardDocument(
        str(path.resolve()), path.name, path.stem, "application/octet-stream", "docling",
        [StandardBlock("docling_document", markdown, metadata={"structure_aware": True})],
        {"docling": True},
    )
