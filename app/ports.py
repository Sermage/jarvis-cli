"""Порты слоя приложения.

Use cases в `app/` оперируют этими абстракциями, а не конкретными
реализациями `infra/`. Это позволяет подменять хранилища в тестах
фейками без `unittest.mock.patch` глобальных имён.
"""
from __future__ import annotations

from typing import Protocol

from domain.working_memory import WorkingMemory


class WorkingMemoryRepository(Protocol):
    """Хранилище рабочей памяти текущего сеанса."""

    def load(self) -> WorkingMemory: ...
    def save(self, wm: WorkingMemory) -> None: ...
    def clear(self) -> None: ...
