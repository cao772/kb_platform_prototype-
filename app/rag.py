from __future__ import annotations

from app.agent import KnowledgeAgent
from app.store import KnowledgeStore


def answer_question(store: KnowledgeStore, question: str, *, knowledge_type: str | None = None) -> dict:
    return KnowledgeAgent(store).answer(question, knowledge_type=knowledge_type)
