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
    "Ниже — пронумерованные фрагменты, найденные в базе по текущему вопросу. "
    "Отвечай, опираясь ТОЛЬКО на этот контекст. Ставь ссылки [i] по тексту "
    "после каждого факта. Если ответа в контексте нет — прямо скажи об этом, "
    "не выдумывай.\n\n"
    "Ответ ОБЯЗАН строго следовать этому формату (три части):\n\n"
    "<связный ответ по существу со ссылками [i] после фактов>\n\n"
    "Источники:\n"
    "- [i] <source · раздел (chunk_id)> — копируй из заголовка фрагмента как есть\n\n"
    "Цитаты:\n"
    "- [i] «дословный фрагмент из чанка [i], подтверждающий факт»\n\n"
    "Правила: перечисляй ТОЛЬКО реально использованные фрагменты; цитата — "
    "дословная выдержка из соответствующего чанка (не пересказ); номера [i] в "
    "ответе, источниках и цитатах должны совпадать.\n\n"
)

_RAG_WEAK_HEADER = (
    "[КОНТЕКСТ ИЗ БАЗЫ ДОКУМЕНТОВ (RAG) — РЕЛЕВАНТНОГО КОНТЕКСТА НЕ НАЙДЕНО]\n"
    "По текущему вопросу в базе не нашлось фрагментов выше порога релевантности. "
    "Это значит, что достоверного контекста для ответа нет.\n"
    "Ты ОБЯЗАН ответить ровно так: честно скажи «Не знаю — в базе нет "
    "релевантной информации по этому вопросу» и попроси пользователя уточнить "
    "или переформулировать запрос. НЕ выдумывай ответ, НЕ приводи источники и "
    "НЕ цитируй — их нет.\n\n"
)


def _chunk_header(i: int, ch) -> str:
    """Заголовок фрагмента для RAG-блока: `[i] location (chunk_id)`."""
    loc = ch.location() if isinstance(ch, RetrievedChunk) else ""
    cid = getattr(ch, "chunk_id", "") or ""
    label = loc
    if cid and cid not in label:
        label = f"{loc} ({cid})" if loc else f"({cid})"
    return f"[{i}] {label}" if label else f"[{i}]"


def format_rag_block(chunks: list) -> str:
    """Собрать нумерованный блок найденных фрагментов с источниками."""
    lines = []
    for i, ch in enumerate(chunks, 1):
        lines.append(f"{_chunk_header(i, ch)}\n{ch.text}")
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
    # Если поиск с порогом ничего не вернул — вместо тихого «отвечу из общих
    # знаний» вставляем блок-инструкцию «не знаю»: ассистент обязан отказаться,
    # а не выдумывать (усиление против галлюцинаций на слабом контексте).
    if retrieval_engine is not None and user_query and retrieval_engine.is_ready():
        chunks = retrieval_engine.retrieve(user_query, top_k=top_k)
        if chunks:
            parts.append(format_rag_block(chunks))
        else:
            parts.append(_RAG_WEAK_HEADER)

    knowledge = knowledge_repo.all_as_prompt()
    if knowledge:
        parts.append(f"[ДОЛГОВРЕМЕННАЯ ПАМЯТЬ — База знаний]\n{knowledge}")

    wm_text = wm.to_prompt()
    if wm_text:
        parts.append(wm_text)

    return "\n\n".join(parts) if parts else None
