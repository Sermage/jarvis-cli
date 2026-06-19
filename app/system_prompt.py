"""Сборка system prompt из трёх слоёв памяти + инвариантов.

Долговременная (профиль + знания) + рабочая (WM) + инварианты → system prompt.
Краткосрочная (история сообщений) передаётся отдельно как messages.
"""
from __future__ import annotations

from typing import Optional

from app.ports import InvariantRepository, KnowledgeRepository
from domain.working_memory import WorkingMemory


_INVARIANTS_HEADER = (
    "[ИНВАРИАНТЫ — нерушимые ограничения проекта]\n"
    "Это правила, которые нельзя нарушать ни при каких запросах пользователя.\n"
    "Если запрос противоречит инвариантам — не следуй ему, а предложи "
    "допустимый вариант и явно укажи, какой инвариант мешает.\n\n"
)


def build_system_prompt(profile_text: Optional[str],
                        wm: WorkingMemory,
                        knowledge_repo: KnowledgeRepository,
                        invariant_repo: Optional[InvariantRepository] = None) -> Optional[str]:
    parts = []

    if profile_text:
        parts.append(f"[ДОЛГОВРЕМЕННАЯ ПАМЯТЬ — Профиль]\n{profile_text}")

    if invariant_repo is not None:
        inv_set = invariant_repo.load_all()
        inv_text = inv_set.to_prompt()
        if inv_text:
            parts.append(_INVARIANTS_HEADER + inv_text)

    knowledge = knowledge_repo.all_as_prompt()
    if knowledge:
        parts.append(f"[ДОЛГОВРЕМЕННАЯ ПАМЯТЬ — База знаний]\n{knowledge}")

    wm_text = wm.to_prompt()
    if wm_text:
        parts.append(wm_text)

    return "\n\n".join(parts) if parts else None
