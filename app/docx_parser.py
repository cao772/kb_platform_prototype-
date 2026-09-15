from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

WORD_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


@dataclass
class ParsedDocument:
    title: str
    paragraphs: list[str]
    tables: list[list[list[str]]]

    @property
    def full_text(self) -> str:
        parts = list(self.paragraphs)
        for table in self.tables:
            parts.append("[表格]")
            parts.extend(" | ".join(cell for cell in row if cell) for row in table)
        return "\n".join(part for part in parts if part.strip())


def _text(element: ET.Element) -> str:
    return "".join(node.text or "" for node in element.iter(f"{{{WORD_NS['w']}}}t")).strip()


def parse_docx(path: str | Path) -> ParsedDocument:
    docx_path = Path(path)
    with ZipFile(docx_path) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    body = root.find("w:body", WORD_NS)
    paragraphs: list[str] = []
    tables: list[list[list[str]]] = []
    if body is not None:
        for child in body:
            if child.tag == f"{{{WORD_NS['w']}}}p":
                text = _text(child)
                if text:
                    paragraphs.append(text)
            elif child.tag == f"{{{WORD_NS['w']}}}tbl":
                rows: list[list[str]] = []
                for tr in child.findall("w:tr", WORD_NS):
                    cells = [" / ".join(_text(p) for p in tc.findall("w:p", WORD_NS) if _text(p)) for tc in tr.findall("w:tc", WORD_NS)]
                    if any(cells):
                        rows.append(cells)
                if rows:
                    tables.append(rows)
    return ParsedDocument(title=paragraphs[0] if paragraphs else docx_path.stem, paragraphs=paragraphs, tables=tables)
