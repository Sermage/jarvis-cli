"""Сборка system prompt из трёх слоёв памяти.

Долговременная (профиль + знания) + рабочая (WM) → system prompt.
Краткосрочная (история сообщений) передаётся отдельно как messages.
"""
from __future__ import annotations

from typing import Optional

from app.ports import KnowledgeRepository
from domain.working_memory import WorkingMemory


def build_system_prompt(profile_text: Optional[str],
                        wm: WorkingMemory,
                        knowledge_repo: KnowledgeRepository) -> Optional[str]:
    parts = []

    if profile_text:
        parts.append(f"[ДОЛГОВРЕМЕННАЯ ПАМЯТЬ — Профиль]\n{profile_text}")

    knowledge = knowledge_repo.all_as_prompt()
    if knowledge:
        parts.append(f"[ДОЛГОВРЕМЕННАЯ ПАМЯТЬ — База знаний]\n{knowledge}")

    wm_text = wm.to_prompt()
    if wm_text:
        parts.append(wm_text)

    return "\n\n".join(parts) if parts else None
