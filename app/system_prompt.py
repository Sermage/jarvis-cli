"""Сборка system prompt из трёх слоёв памяти + инвариантов.

Долговременная (профиль + знания) + рабочая (WM) + инварианты → system prompt.
Краткосрочная (история сообщений) передаётся отдельно как messages.
"""
from __future__ import annotations

from typing import Optional

from app.ports import InvariantRepository, KnowledgeRepository, RetrievalEngine
from domain.retrieval import RetrievedChunk
from domain.working_memory import WorkingMemory


_INVARIANTS_HEADER = (
    "[ИНВАРИАНТЫ — нерушимые ограничения проекта]\n"
    "Это правила, которые нельзя нарушать ни при каких запросах пользователя.\n"
    "Если запрос противоречит инвариантам — не следуй ему, а предложи "
    "допустимый вариант и явно укажи, какой инвариант мешает.\n\n"
)

_RAG_HEADER = (
    "[КОНТЕКСТ ИЗ БАЗЫ ДОКУМЕНТОВ (RAG)]\n"
    "Ниже — фрагменты, найденные в базе по текущему вопросу. "
    "Отвечай, опираясь в первую очередь на этот контекст, и ссылайся на "
    "источники (в скобках после факта). Если ответа в контексте нет — "
    "прямо скажи об этом, не выдумывай.\n\n"
)


def format_rag_block(chunks: list) -> str:
    """Собрать нумерованный блок найденных фрагментов с источниками."""
    lines = []
    for i, ch in enumerate(chunks, 1):
        loc = ch.location() if isinstance(ch, RetrievedChunk) else ""
        header = f"[{i}] {loc}" if loc else f"[{i}]"
        lines.append(f"{header}\n{ch.text}")
    return _RAG_HEADER + "\n\n".join(lines)


def build_system_prompt(profile_text: Optional[str],
                        wm: WorkingMemory,
                        knowledge_repo: KnowledgeRepository,
                        invariant_repo: Optional[InvariantRepository] = None,
                        retrieval_engine: Optional[RetrievalEngine] = None,
                        user_query: Optional[str] = None,
                        top_k: int = 5) -> Optional[str]:
    parts = []

    if profile_text:
        parts.append(f"[ДОЛГОВРЕМЕННАЯ ПАМЯТЬ — Профиль]\n{profile_text}")

    if invariant_repo is not None:
        inv_set = invariant_repo.load_all()
        inv_text = inv_set.to_prompt()
        if inv_text:
            parts.append(_INVARIANTS_HEADER + inv_text)

    # RAG: подмешиваем найденный по вопросу контекст (если режим включён).
    if retrieval_engine is not None and user_query and retrieval_engine.is_ready():
        chunks = retrieval_engine.retrieve(user_query, top_k=top_k)
        if chunks:
            parts.append(format_rag_block(chunks))

    knowledge = knowledge_repo.all_as_prompt()
    if knowledge:
        parts.append(f"[ДОЛГОВРЕМЕННАЯ ПАМЯТЬ — База знаний]\n{knowledge}")

    wm_text = wm.to_prompt()
    if wm_text:
        parts.append(wm_text)

    return "\n\n".join(parts) if parts else None
