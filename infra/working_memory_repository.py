"""Файловое хранилище рабочей памяти."""
from __future__ import annotations

import json
import os
import time
from typing import Callable, Optional

from domain.working_memory import WorkingMemory


class FileWorkingMemoryRepository:
    """Хранит WorkingMemory в одном JSON-файле.

    Часы инжектятся, чтобы тесты могли проверять поля `created_at` / `updated_at`
    без зависимости от системного времени.
    """

    def __init__(self,
                 file_path: str,
                 now: Optional[Callable[[], str]] = None):
        self._path = file_path
        self._now  = now or (lambda: time.strftime("%Y-%m-%d %H:%M"))

    def load(self) -> WorkingMemory:
        if not os.path.exists(self._path):
            return WorkingMemory()
        try:
            with open(self._path, encoding="utf-8") as f:
                return WorkingMemory.from_dict(json.load(f))
        except Exception:
            return WorkingMemory()

    def save(self, wm: WorkingMemory) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        now = self._now()
        if not wm.created_at:
            wm.created_at = now
        wm.updated_at = now
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(wm.to_dict(), f, ensure_ascii=False, indent=2)

    def clear(self) -> None:
        if os.path.exists(self._path):
            os.remove(self._path)
