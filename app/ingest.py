from __future__ import annotations

from pathlib import Path

from app.parsers import parse_file
from app.store import KnowledgeStore
from app.text_processing import chunk_blocks, infer_knowledge_type, infer_tags

SUPPORTED_SUFFIXES = {".docx", ".pdf", ".txt", ".md", ".csv", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}


def ingest_file(store: KnowledgeStore, path: str | Path) -> int:
    file_path = Path(path)
    parsed = parse_file(file_path)
    knowledge_type = infer_knowledge_type(file_path.name, parsed.full_text)
    tags = infer_tags(parsed.full_text)
    chunks = [
        {"chunk_index": index, "text": chunk["text"], "metadata": {
            "source": file_path.name,
            "title": parsed.title,
            "knowledge_type": knowledge_type,
            "tags": tags,
            **chunk["metadata"],
        }}
        for index, chunk in enumerate(chunk_blocks(parsed.blocks), 1)
    ]
    return store.upsert_document(
        filename=file_path.name,
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
            store.record_ingestion_failure(filename=path.name, source_path=str(path.resolve()), message=str(exc))
    return ids
