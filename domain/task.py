"""Чистая доменная модель задачи: данные, машина состояний, сериализация.

Без I/O, без сети, без глобального состояния. Файловая персистентность,
указатель активной задачи и связь с LLM-клиентом живут в `infra/`.
"""
from __future__ import annotations

import time
import uuid
from typing import Optional


class TaskState:
    INTAKE     = "intake"
    PLANNING   = "planning"
    EXECUTION  = "execution"
    VALIDATION = "validation"
    DONE       = "done"
    ABORTED    = "aborted"

    ALL = [INTAKE, PLANNING, EXECUTION, VALIDATION, DONE, ABORTED]
    TERMINAL = {DONE, ABORTED}


# Откат validation → planning умышленно запрещён: если на этапе валидации
# выяснилось, что план плох, сначала идём в execution, а оттуда уже в planning.
# Это сохраняет инвариант «после planning всегда был хотя бы один заход в execution».
_ALLOWED_TRANSITIONS = {
    TaskState.INTAKE     : {TaskState.PLANNING,  TaskState.ABORTED},
    TaskState.PLANNING   : {TaskState.EXECUTION, TaskState.INTAKE,    TaskState.ABORTED},
    TaskState.EXECUTION  : {TaskState.VALIDATION, TaskState.PLANNING, TaskState.ABORTED},
    TaskState.VALIDATION : {TaskState.EXECUTION, TaskState.DONE,      TaskState.ABORTED},
    TaskState.DONE       : set(),
    TaskState.ABORTED    : set(),
}


class TaskTransitionError(Exception):
    pass


class StageStatus:
    PENDING       = "pending"
    IN_PROGRESS   = "in_progress"
    AWAITING_USER = "awaiting_user"
    DONE          = "done"
    FAILED        = "failed"


class StageResult:
    """Результат одной стадии задачи."""

    def __init__(self,
                 status: str = StageStatus.PENDING,
                 output: str = "",
                 artifacts: Optional[dict] = None,
                 started_at: Optional[str] = None,
                 finished_at: Optional[str] = None):
        self.status      = status
        self.output      = output
        self.artifacts   = artifacts if artifacts is not None else {}
        self.started_at  = started_at
        self.finished_at = finished_at

    def to_dict(self) -> dict:
        return {
            "status":      self.status,
            "output":      self.output,
            "artifacts":   self.artifacts,
            "started_at":  self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StageResult":
        return cls(
            status      = d.get("status", StageStatus.PENDING),
            output      = d.get("output", ""),
            artifacts   = d.get("artifacts") or {},
            started_at  = d.get("started_at"),
            finished_at = d.get("finished_at"),
        )


class Task:
    """Задача с явной машиной состояний.

    Чистая модель: переходы валидируются, история фиксируется. Сохранение в
    хранилище — забота инфраструктурного слоя.
    """

    def __init__(self,
                 id: str,
                 title: str,
                 request: str,
                 state: str = TaskState.INTAKE,
                 stages: Optional[dict] = None,
                 context: Optional[dict] = None,
                 pending_questions: Optional[list] = None,
                 answers: Optional[list] = None,
                 awaiting: Optional[str] = None,
                 profile_snapshot: Optional[str] = None,
                 model_snapshot: Optional[str] = None,
                 created_at: Optional[str] = None,
                 updated_at: Optional[str] = None,
                 transitions: Optional[list] = None):
        self.id                = id
        self.title             = title
        self.request           = request
        self.state             = state
        self.stages            = stages if stages is not None else {}
        self.context           = context if context is not None else {}
        self.pending_questions = pending_questions if pending_questions is not None else []
        self.answers           = answers if answers is not None else []
        self.awaiting          = awaiting
        self.profile_snapshot  = profile_snapshot
        self.model_snapshot    = model_snapshot
        self.created_at        = created_at
        self.updated_at        = updated_at
        self.transitions       = transitions if transitions is not None else []

    # ── factory ──────────────────────────────────────────────────────────────

    @classmethod
    def new(cls,
            request: str,
            profile: Optional[str] = None,
            model: Optional[str] = None,
            now: Optional[str] = None) -> "Task":
        when  = now or time.strftime("%Y-%m-%d %H:%M")
        title = request.strip().split("\n", 1)[0][:60] or "—"
        return cls(
            id               = uuid.uuid4().hex[:8],
            title            = title,
            request          = request,
            state            = TaskState.INTAKE,
            profile_snapshot = profile,
            model_snapshot   = model,
            created_at       = when,
            updated_at       = when,
        )

    # ── state machine ────────────────────────────────────────────────────────

    def can_transition(self, new_state: str) -> bool:
        return new_state in _ALLOWED_TRANSITIONS.get(self.state, set())

    def transition(self, new_state: str, reason: str = "", at: Optional[str] = None) -> None:
        """Перевести задачу в новое состояние и записать переход в историю.

        I/O намеренно отсутствует — слой инфраструктуры решает, когда сохранять.
        """
        if new_state not in TaskState.ALL:
            raise TaskTransitionError(f"Неизвестное состояние: {new_state!r}")
        if not self.can_transition(new_state):
            allowed = sorted(_ALLOWED_TRANSITIONS.get(self.state, set()))
            raise TaskTransitionError(
                f"Запрещённый переход: {self.state} → {new_state}. "
                f"Разрешено из {self.state}: {allowed or 'ничего (терминальное состояние)'}"
            )
        self.transitions.append({
            "from":   self.state,
            "to":     new_state,
            "at":     at or time.strftime("%Y-%m-%d %H:%M:%S"),
            "reason": reason,
        })
        self.state = new_state

    def is_terminal(self) -> bool:
        return self.state in TaskState.TERMINAL

    # ── serialization ────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "id":                self.id,
            "title":             self.title,
            "request":           self.request,
            "state":             self.state,
            "stages":            {k: v.to_dict() for k, v in self.stages.items()},
            "context":           self.context,
            "pending_questions": self.pending_questions,
            "answers":           self.answers,
            "awaiting":          self.awaiting,
            "profile_snapshot":  self.profile_snapshot,
            "model_snapshot":    self.model_snapshot,
            "created_at":        self.created_at,
            "updated_at":        self.updated_at,
            "transitions":       self.transitions,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        raw_stages = d.get("stages") or {}
        stages = {k: StageResult.from_dict(v) for k, v in raw_stages.items()}
        return cls(
            id                = d["id"],
            title             = d.get("title", "—"),
            request           = d.get("request", ""),
            state             = d.get("state", TaskState.INTAKE),
            stages            = stages,
            context           = d.get("context") or {},
            pending_questions = d.get("pending_questions") or [],
            answers           = d.get("answers") or [],
            awaiting          = d.get("awaiting"),
            profile_snapshot  = d.get("profile_snapshot"),
            model_snapshot    = d.get("model_snapshot"),
            created_at        = d.get("created_at"),
            updated_at        = d.get("updated_at"),
            transitions       = d.get("transitions") or [],
        )
