"""Доменная модель рабочей памяти.

Содержит данные текущей задачи (один абзац-описание, словарь контекстных
ключей, список заметок) и правила формирования блока для system prompt.
Файловая персистентность и ANSI-вывод — забота `infra/` и `cli/`.
"""
from __future__ import annotations

from typing import Optional


class WorkingMemory:
    """Рабочая память: задача, контекст, заметки для текущего сеанса работы."""

    def __init__(self,
                 task: Optional[str] = None,
                 context: Optional[dict] = None,
                 notes: Optional[list] = None,
                 created_at: Optional[str] = None,
                 updated_at: Optional[str] = None):
        self.task: Optional[str]       = task
        self.context: dict             = context if context is not None else {}
        self.notes: list               = notes if notes is not None else []
        self.created_at: Optional[str] = created_at
        self.updated_at: Optional[str] = updated_at

    def is_empty(self) -> bool:
        return not self.task and not self.context and not self.notes

    def to_prompt(self) -> str:
        """Формирует блок для system prompt."""
        if self.is_empty():
            return ""
        lines = ["[РАБОЧАЯ ПАМЯТЬ]"]
        if self.task:
            lines.append(f"Текущая задача: {self.task}")
        if self.context:
            lines.append("Контекст:")
            for k, v in self.context.items():
                lines.append(f"  {k}: {v}")
        if self.notes:
            lines.append("Заметки:")
            for note in self.notes:
                lines.append(f"  • {note}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "task":       self.task,
            "context":    self.context,
            "notes":      self.notes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "WorkingMemory":
        return cls(
            task       = d.get("task"),
            context    = d.get("context") or {},
            notes      = d.get("notes") or [],
            created_at = d.get("created_at"),
            updated_at = d.get("updated_at"),
        )
