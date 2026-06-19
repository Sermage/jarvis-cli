"""Порты слоя приложения.

Use cases в `app/` оперируют этими абстракциями, а не конкретными
реализациями `infra/`. Это позволяет подменять хранилища в тестах
фейками без `unittest.mock.patch` глобальных имён.
"""
from __future__ import annotations

from typing import Optional, Protocol

from domain.working_memory import WorkingMemory


class WorkingMemoryRepository(Protocol):
    """Хранилище рабочей памяти текущего сеанса."""

    def load(self) -> WorkingMemory: ...
    def save(self, wm: WorkingMemory) -> None: ...
    def clear(self) -> None: ...


class SessionRepository(Protocol):
    """Хранилище краткосрочной памяти (диалогов).

    Идентификатор сессии — строка, совпадающая с именем файла без расширения.
    """

    def save(self,
             session_id: Optional[str],
             messages: list,
             params: dict) -> str:
        """Сохранить сессию. Если session_id is None — создать новый и вернуть его."""
        ...

    def list_all(self) -> list[dict]:
        """Вернуть список сессий, отсортированных от свежей к старой."""
        ...

    def delete(self, session_id: str) -> None: ...

    def path_for(self, session_id: str) -> str:
        """Абсолютный путь к файлу сессии (нужен UI для отображения)."""
        ...
