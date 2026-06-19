"""Доменная модель долговременной базы знаний.

Запись базы знаний — markdown-документ с временной меткой сохранения.
Здесь — только данные и форматирование одного блока. Файловое хранилище
и склейка всех записей для system prompt — в `infra/` и `app/`.
"""
from __future__ import annotations

from typing import Optional


def sanitize_knowledge_name(raw: str) -> str:
    """Привести имя записи к виду, безопасному для файловой системы."""
    return raw.strip().replace(" ", "-").replace("/", "-")


class KnowledgeEntry:
    """Одна запись базы знаний."""

    def __init__(self, name: str, content: str, saved_at: Optional[str] = None):
        self.name     = name
        self.content  = content
        self.saved_at = saved_at

    def to_file_text(self) -> str:
        """Сформировать содержимое .md-файла с заголовком-меткой."""
        if self.saved_at:
            return f"<!-- сохранено: {self.saved_at} -->\n{self.content}"
        return self.content

    def to_prompt_block(self) -> str:
        """Сформировать блок для склейки в долговременную память."""
        return f"### {self.name}\n{self.content}"
