"""Подзадача стадии EXECUTION — единица параллельной работы роя.

Чистая доменная модель: данные, статусы, сериализация. Никаких I/O,
никаких вызовов LLM. Парсинг, диспатч на воркеров и слияние результатов —
в `app/agents/swarm.py`.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Optional


class SubtaskStatus:
    PENDING     = "pending"
    IN_PROGRESS = "in_progress"
    DONE        = "done"
    FAILED      = "failed"


# Известные роли воркеров. Любое другое значение допустимо — будет обработано
# дефолтным generic-воркером.
class WorkerRole:
    CODER      = "coder"
    RESEARCHER = "researcher"
    WRITER     = "writer"
    TESTER     = "tester"
    GENERIC    = "generic"

    ALL = [CODER, RESEARCHER, WRITER, TESTER, GENERIC]


@dataclass
class Subtask:
    """Одна независимая подзадача из плана EXECUTION."""
    id:          str
    role:        str
    description: str
    status:      str = SubtaskStatus.PENDING
    result:      Optional[str] = None
    error:       Optional[str] = None

    @classmethod
    def new(cls, role: str, description: str) -> "Subtask":
        return cls(
            id          = uuid.uuid4().hex[:6],
            role        = role,
            description = description.strip(),
        )

    def to_dict(self) -> dict:
        return {
            "id":          self.id,
            "role":        self.role,
            "description": self.description,
            "status":      self.status,
            "result":      self.result,
            "error":       self.error,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Subtask":
        return cls(
            id          = d["id"],
            role        = d.get("role", WorkerRole.GENERIC),
            description = d.get("description", ""),
            status      = d.get("status", SubtaskStatus.PENDING),
            result      = d.get("result"),
            error       = d.get("error"),
        )
