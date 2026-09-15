from __future__ import annotations

import math
import re
from collections import Counter
from hashlib import blake2b

from app.vendor_path import activate_vendor

activate_vendor()

try:
    import jieba
except Exception:
    jieba = None

TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_.+-]+|[\u4e00-\u9fff]+")


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def tokenize(text: str) -> list[str]:
    if jieba is not None:
        words = [word.strip().lower() for word in jieba.cut(text) if word.strip()]
        enriched: list[str] = []
        for word in words:
            if re.fullmatch(r"[\u4e00-\u9fff]+", word) and len(word) > 1:
                enriched.append(word)
                enriched.extend(word[index:index + 2] for index in range(len(word) - 1))
            else:
                enriched.append(word)
        return enriched
    tokens: list[str] = []
    for token in TOKEN_PATTERN.findall(text):
        token = token.lower()
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            tokens.extend(token)
            tokens.extend(token[index:index + 2] for index in range(max(0, len(token) - 1)))
            if len(token) > 2:
                tokens.append(token)
        else:
            tokens.append(token)
    return tokens


def chunk_blocks(blocks: list, *, max_chars: int = 650, overlap: int = 90) -> list[dict]:
    chunks: list[dict] = []
    current = ""
    current_meta: dict = {}
    for block in blocks:
        text = normalize_text(block.text)
        if not text:
            continue
        block_meta = {
            "block_type": block.block_type,
            "page_no": block.page_no,
            "section_title": block.section_title,
            **(block.metadata or {}),
        }
        if len(current) + len(text) + 1 <= max_chars:
            current = f"{current}\n{text}".strip()
            current_meta = current_meta or block_meta
            continue
        if current:
            chunks.append({"text": current, "metadata": current_meta})
        if len(text) <= max_chars:
            current = text
            current_meta = block_meta
            continue
        start = 0
        while start < len(text):
            chunks.append({"text": text[start:start + max_chars], "metadata": block_meta})
            start += max_chars - overlap
        current = ""
        current_meta = {}
    if current:
        chunks.append({"text": current, "metadata": current_meta})
    return chunks


def embed_text(text: str, *, dimensions: int = 128) -> list[float]:
    vector = [0.0] * dimensions
    counts = Counter(tokenize(text))
    for token, count in counts.items():
        digest = blake2b(token.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign * (1.0 + math.log(count))
    norm = math.sqrt(sum(value * value for value in vector))
    return [round(value / norm, 6) for value in vector] if norm else vector


def vector_cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


def cosine_score(query_tokens: list[str], text: str) -> float:
    if not query_tokens:
        return 0.0
    query_counter = Counter(query_tokens)
    doc_counter = Counter(tokenize(text))
    if not doc_counter:
        return 0.0
    dot = sum(query_counter[token] * doc_counter.get(token, 0) for token in query_counter)
    query_norm = math.sqrt(sum(value * value for value in query_counter.values()))
    doc_norm = math.sqrt(sum(value * value for value in doc_counter.values()))
    return dot / (query_norm * doc_norm) if query_norm and doc_norm else 0.0


def infer_knowledge_type(filename: str, text: str) -> str:
    combined = f"{filename} {text[:800]}"
    if "法规" in combined or "GMA" in combined or "认证" in combined:
        return "法规知识"
    if "调整申请" in combined or "管理创新" in combined:
        return "管理创新资料"
    return "通用知识"


def infer_tags(text: str) -> list[str]:
    candidates = ["法规", "GMA", "认证", "合规", "知识图谱", "智能问答", "权限", "数据治理", "低代码", "预算", "项目计划", "专利", "管理方法"]
    return [tag for tag in candidates if tag in text][:8]
