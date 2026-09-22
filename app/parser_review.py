from __future__ import annotations

from pathlib import Path
from typing import Any

import fitz

from app.pdf_structure import extract_pdf_structure


class ParserReviewService:
    def __init__(self, store):
        self.store = store

    def report(self, document_id: int) -> dict[str, Any]:
        document = self.store.document_detail(document_id)
        metadata = document.get("metadata") or {}
        parse_report = metadata.get("parse_report") or {"summary": {}, "pages": []}
        if not parse_report.get("pages"):
            parse_report = self._backfill_pdf_report(document, metadata) or parse_report
        return {
            "document": {
                "id": document["id"],
                "filename": document["filename"],
                "title": document["title"],
                "mime_type": document["mime_type"],
                "parser": document["parser"],
                "knowledge_type": document["knowledge_type"],
                "created_at": document["created_at"],
            },
            "summary": parse_report.get("summary") or metadata.get("parse_summary") or {},
            "pages": parse_report.get("pages") or [],
        }

    def _backfill_pdf_report(self, document: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any] | None:
        """Add Stage33 page diagnostics to legacy PDFs without re-indexing their content."""
        source = Path(str(document.get("source_path") or ""))
        if document.get("mime_type") != "application/pdf" or not source.exists():
            return None
        _, parse_report = extract_pdf_structure(source, ocr_enabled=False)
        metadata["parse_summary"] = dict(parse_report.get("summary") or {})
        metadata["parse_report"] = parse_report
        self.store.update_document_metadata(int(document["id"]), metadata)
        return parse_report

    def page_image(self, document_id: int, page_no: int, *, scale: float = 1.35) -> bytes:
        document = self.store.document_detail(document_id)
        source = Path(str(document.get("source_path") or ""))
        if document.get("mime_type") != "application/pdf" and source.suffix.lower() != ".pdf":
            raise ValueError("page image is only available for PDF documents")
        if not source.exists():
            raise ValueError("source file is not available")
        doc = fitz.open(str(source))
        try:
            if page_no < 1 or page_no > doc.page_count:
                raise ValueError("page out of range")
            page = doc.load_page(page_no - 1)
            zoom = max(0.7, min(float(scale), 2.4))
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            return pix.tobytes("png")
        finally:
            doc.close()
