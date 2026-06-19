"""Файловое хранилище задач + указатель активной задачи.

Каждая задача — файл `<id>.json` в каталоге; идентификатор активной
задачи — отдельный файл `active`. Часы инжектируются для
тестируемости (поле `updated_at`).
"""
from __future__ import annotations

import json
import os
import time
from typing import Callable, Optional

from domain.task import Task


class FileTaskRepository:
    def __init__(self,
                 dir_path: str,
                 active_file: Optional[str] = None,
                 now: Optional[Callable[[], str]] = None):
        self._dir         = dir_path
        self._active_file = active_file or os.path.join(dir_path, "active")
        self._now         = now or (lambda: time.strftime("%Y-%m-%d %H:%M"))

    # ── persistence ──────────────────────────────────────────────────────────

    def _path(self, task_id: str) -> str:
        return os.path.join(self._dir, f"{task_id}.json")

    def save(self, task: Task) -> None:
        os.makedirs(self._dir, exist_ok=True)
        task.updated_at = self._now()
        with open(self._path(task.id), "w", encoding="utf-8") as f:
            json.dump(task.to_dict(), f, ensure_ascii=False, indent=2)

    def load(self, task_id: str) -> Optional[Task]:
        path = self._path(task_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                return Task.from_dict(json.load(f))
        except Exception:
            return None

    def list_all(self) -> list[Task]:
        if not os.path.isdir(self._dir):
            return []
        tasks: list[Task] = []
        for fname in os.listdir(self._dir):
            if not fname.endswith(".json"):
                continue
            t = self.load(os.path.splitext(fname)[0])
            if t is not None:
                tasks.append(t)
        tasks.sort(key=lambda t: t.updated_at or "", reverse=True)
        return tasks

    def delete(self, task: Task) -> None:
        path = self._path(task.id)
        if os.path.exists(path):
            os.remove(path)
        if self.get_active_id() == task.id:
            self.clear_active()

    # ── active pointer ───────────────────────────────────────────────────────

    def set_active(self, task: Task) -> None:
        os.makedirs(self._dir, exist_ok=True)
        with open(self._active_file, "w", encoding="utf-8") as f:
            f.write(task.id)

    def get_active_id(self) -> Optional[str]:
        if not os.path.exists(self._active_file):
            return None
        try:
            with open(self._active_file, encoding="utf-8") as f:
                tid = f.read().strip()
            return tid or None
        except Exception:
            return None

    def get_active(self) -> Optional[Task]:
        tid = self.get_active_id()
        if not tid:
            return None
        return self.load(tid)

    def clear_active(self) -> None:
        if os.path.exists(self._active_file):
            os.remove(self._active_file)

    # ── state machine ────────────────────────────────────────────────────────

    def transition(self, task: Task, new_state: str, reason: str = "") -> None:
        """Сделать переход и тут же сохранить.

        Чтобы переживать падения между шагами: ошибка в самом переходе
        не дойдёт до диска, успешный переход — фиксируется немедленно.
        """
        task.transition(new_state, reason=reason)
        self.save(task)
