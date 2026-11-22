from __future__ import annotations

from pathlib import Path
from app.document_profiles import extract_profile

from app.parsers import parse_file
from app.store import KnowledgeStore
from app.text_processing import chunk_blocks, infer_knowledge_type, infer_tags

SUPPORTED_SUFFIXES = {
    ".docx", ".pdf", ".txt", ".md", ".csv", ".xlsx", ".pptx", ".json", ".xml", ".html", ".htm",
    ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp",
}


def ingest_file(store: KnowledgeStore, path: str | Path, *, document_family: str = "", content_digest: str = "") -> int:
    file_path = Path(path)
    filename = file_path.name
    if document_family:
        filename = f"{file_path.stem[:80]}-{document_family}-{content_digest}{file_path.suffix}"
        with store.lock:
            known = store.conn.execute("SELECT id FROM documents WHERE filename=? AND json_extract(metadata,'$.source_digest')=?",
                                       (filename, content_digest)).fetchone()
        if known:
            return known[0]
    parsed = parse_file(file_path, local_only=True) if document_family else parse_file(file_path)
    if document_family and parsed.parser == "image-metadata":
        raise ValueError("当前图片没有取得可读正文，请检查本机 OCR 配置和原图质量。")
    knowledge_type = "法规知识" if document_family else infer_knowledge_type(file_path.name, parsed.full_text)
    tags = infer_tags(parsed.full_text)
    chunks = [
        {
            "chunk_index": index,
            "text": chunk["text"],
            "metadata": {
                "source": file_path.name,
                "title": parsed.title,
                "knowledge_type": knowledge_type,
                "tags": tags,
                **chunk["metadata"],
            },
        }
        for index, chunk in enumerate(chunk_blocks(parsed.blocks), 1)
    ]
    if not chunks:
        raise ValueError("document contains no indexable text")
    if document_family:
        parsed.metadata.update(document_family=document_family, source_digest=content_digest, original_filename=file_path.name,
                               profile_fields=extract_profile(document_family, chunks))
    return store.upsert_document(
        filename=filename,
        title=parsed.title,
        knowledge_type=knowledge_type,
        tags=tags,
        source_path=str(file_path.resolve()),
        full_text=parsed.full_text,
        mime_type=parsed.mime_type,
        parser=parsed.parser,
        metadata=parsed.metadata,
        chunks=chunks,
    )


def ingest_directory(store: KnowledgeStore, directory: str | Path) -> list[int]:
    ids: list[int] = []
    for path in sorted(Path(directory).iterdir()):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        try:
            ids.append(ingest_file(store, path))
        except Exception as exc:
            store.record_ingestion_failure(
                filename=path.name,
                source_path=str(path.resolve()),
                message=str(exc),
            )
    return ids
