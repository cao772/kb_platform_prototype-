from __future__ import annotations

import io
import tempfile
from pathlib import Path

import fitz
from PIL import Image, ImageDraw

from app.ingest import ingest_file
from app.parser_review import ParserReviewService
from app.pdf_structure import extract_pdf_structure
from app.store import KnowledgeStore


def _make_pdf(path: Path) -> None:
    doc = fitz.open()

    page = doc.new_page(width=595, height=842)
    page.insert_text((60, 60), "CHAPTER I", fontsize=16)
    page.insert_text((60, 88), "Article 1", fontsize=14)
    page.insert_text((60, 116), "This regulation applies to electrical equipment placed on the market.", fontsize=11)
    page.insert_text((60, 142), "1. Manufacturers shall retain technical documentation and evidence.", fontsize=11)

    table_page = doc.new_page(width=595, height=842)
    table_page.insert_text((60, 55), "ANNEX I", fontsize=16)
    xs = [55, 215, 365, 540]
    ys = [120, 158, 196, 234, 272]
    shape = table_page.new_shape()
    for x in xs:
        shape.draw_line((x, ys[0]), (x, ys[-1]))
    for y in ys:
        shape.draw_line((xs[0], y), (xs[-1], y))
    shape.finish(width=0.8)
    shape.commit()
    rows = [
        ["Equipment type", "Risk Level", "Comments"],
        ["Iron", "3", "High risk"],
        ["Lamp", "2", "Medium risk"],
        ["Cable", "1", "Low risk"],
    ]
    for r, row in enumerate(rows):
        for col, value in enumerate(row):
            table_page.insert_text((xs[col] + 5, ys[r] + 24), value, fontsize=9)

    scan_page = doc.new_page(width=595, height=842)
    image = Image.new("RGB", (900, 1200), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 40, 860, 1160), outline="black", width=4)
    draw.text((80, 100), "SCANNED REGULATION PAGE", fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    scan_page.insert_image(scan_page.rect, stream=buffer.getvalue())

    doc.save(path)
    doc.close()


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pdf = root / "poc-regulation.pdf"
        _make_pdf(pdf)

        blocks, report = extract_pdf_structure(pdf, ocr_enabled=False)
        summary = report["summary"]
        pages = report["pages"]

        assert summary["page_count"] == 3
        assert summary["table_pages"] >= 1
        assert summary["scan_pages"] >= 1
        assert summary["unreadable_pages"] >= 1
        assert summary["heading_count"] >= 2
        assert pages[0]["page_type"] in {"native_text", "sparse_text"}
        assert any(block["type"] == "heading" and "Article 1" in block["text"] for block in pages[0]["blocks"])
        assert any(block["type"] == "table" for block in pages[1]["blocks"])
        table = next(block for block in pages[1]["blocks"] if block["type"] == "table")
        assert table["rows"][0][:3] == ["Equipment type", "Risk Level", "Comments"]
        assert pages[2]["page_type"] == "scan"
        assert "ocr_required" in pages[2]["issues"]
        assert pages[2]["strategy"] == "ocr_required"
        assert all("page_no" in block for block in blocks)
        assert any(block.get("hierarchy") for block in blocks)

        store = KnowledgeStore(root / "knowledge.db")
        document_id = ingest_file(store, pdf)
        detail = store.document_detail(document_id)
        metadata = detail["metadata"]
        assert detail["parser"].startswith("pymupdf-layout")
        assert metadata["parse_summary"]["page_count"] == 3
        assert metadata["parse_report"]["pages"][1]["table_count"] >= 1

        review = ParserReviewService(store)
        payload = review.report(document_id)
        assert payload["summary"]["page_count"] == 3
        assert len(payload["pages"]) == 3
        image_bytes = review.page_image(document_id, 2)
        assert image_bytes.startswith(b"\x89PNG")
        assert len(image_bytes) > 1000

    page = Path("static/parse_review.html").read_text(encoding="utf-8")
    admin = Path("static/admin.html").read_text(encoding="utf-8")
    server = Path("app/server.py").read_text(encoding="utf-8")
    benchmark = Path("scripts/poc_parser_benchmark.py").read_text(encoding="utf-8")
    cases = Path("data/poc_parser_cases.json").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/quality.yml").read_text(encoding="utf-8")

    for label in ("原始 PDF 页面", "解析结果", "逐页质量明细", "表格页", "OCR页", "异常页"):
        assert label in page
    assert "文档解析复核" in admin
    assert "/api/parser/report" in server
    assert "/api/parser/page-image" in server
    assert "parser_poc_report.csv" in benchmark
    assert "Directive 2014-35-EU.pdf" in cases
    assert "EESS-Inscope-Equipment-Definitions-and-Risk-Levels-v4.3-Approved.pdf" in cases
    assert "Stage33 POC document parsing tests" in workflow

    print("OK: stage33 page-aware parsing, table recovery, scan routing and parser review passed")


if __name__ == "__main__":
    main()
