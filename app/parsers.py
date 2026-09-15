from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from app.docx_parser import parse_docx
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


def parse_file(path: str | Path) -> StandardDocument:
    file_path = Path(path)
    if os.getenv("KB_PARSER_BACKEND", "lightweight").lower() == "docling":
        try:
            return parse_docling_standard(file_path)
        except Exception:
            pass
    suffix = file_path.suffix.lower()
    if suffix == ".docx":
        return parse_docx_standard(file_path)
    if suffix == ".pdf":
        return parse_pdf_standard(file_path)
    if suffix in {".txt", ".md", ".csv"}:
        return parse_text_standard(file_path)
    if suffix in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}:
        return parse_image_standard(file_path)
    raise ValueError(f"Unsupported file type: {file_path.suffix}")


def parse_docx_standard(path: Path) -> StandardDocument:
    parsed = parse_docx(path)
    blocks = [StandardBlock("paragraph", text) for text in parsed.paragraphs]
    for index, table in enumerate(parsed.tables, 1):
        blocks.append(StandardBlock("table", "\n".join(" | ".join(cell for cell in row if cell) for row in table), metadata={"table_index": index}))
    return StandardDocument(str(path.resolve()), path.name, parsed.title, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx-xml", blocks)


def parse_pdf_standard(path: Path) -> StandardDocument:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    blocks = [StandardBlock("page_text", (page.extract_text() or "").strip(), page_no=i) for i, page in enumerate(reader.pages, 1) if (page.extract_text() or "").strip()]
    return StandardDocument(str(path.resolve()), path.name, path.stem, "application/pdf", "pypdf", blocks, {"page_count": len(reader.pages)})


def parse_text_standard(path: Path) -> StandardDocument:
    text = path.read_text(encoding="utf-8", errors="ignore")
    return StandardDocument(str(path.resolve()), path.name, path.stem, "text/plain", "plain-text", [StandardBlock("text", text)])


def parse_image_standard(path: Path) -> StandardDocument:
    from PIL import Image
    image = Image.open(path)
    metadata = {"width": image.width, "height": image.height, "mode": image.mode, "ocr_status": "adapter_required"}
    text = f"[图片待OCR] {path.name} {image.width}x{image.height}。生产环境建议接入 Docling/PaddleOCR 或企业 OCR 服务。"
    return StandardDocument(str(path.resolve()), path.name, path.stem, f"image/{path.suffix.lower().lstrip('.')}", "image-metadata", [StandardBlock("image", text, metadata=metadata)], metadata)


def parse_docling_standard(path: Path) -> StandardDocument:
    from docling.document_converter import DocumentConverter
    result = DocumentConverter().convert(str(path))
    markdown = result.document.export_to_markdown()
    return StandardDocument(
        str(path.resolve()), path.name, path.stem, "application/octet-stream", "docling",
        [StandardBlock("docling_document", markdown, metadata={"structure_aware": True})],
        {"docling": True},
    )
