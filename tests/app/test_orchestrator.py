"""Юнит-тесты Orchestrator: применение AgentResult к Task через TaskRepository.

Здесь LLM не вызывается — мы подменяем сам агент детерминированной заглушкой
и проверяем, что оркестратор правильно меняет статус стадии, awaiting,
сохраняет в repo и крутит машину состояний.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from app.agents import AgentContext
from app.agents.base import AgentResult
from app.invariant_guard import GuardedResult
from app.orchestrator import Orchestrator
from app.stage_prompts import STAGE_PROMPTS
from domain.task import StageStatus, Task, TaskState
from domain.working_memory import WorkingMemory


# ── фейки ───────────────────────────────────────────────────────────────────


class _FakeTaskRepo:
    def __init__(self):
        self.saved: list[Task] = []
        self.transitions: list[tuple[str, str, str]] = []

    def save(self, task): self.saved.append(task)

    def transition(self, task, new_state, reason=""):
        prev = task.state
        task.transition(new_state, reason=reason)
        self.transitions.append((prev, new_state, reason))

    # неиспользуемые методы порта
    def load(self, task_id): return None
    def list_all(self): return []
    def delete(self, task): pass
    def set_active(self, task): pass
    def get_active_id(self): return None
    def get_active(self): return None
    def clear_active(self): pass


@dataclass
class _RecordingAgent:
    """Агент-заглушка: фиксирует входной followup и возвращает заданный результат."""
    stage: str
    result: AgentResult
    seen_followup: Optional[str] = None
    seen_task_id: Optional[str] = None

    def run(self, task, followup_message, ctx):
        self.seen_followup = followup_message
        self.seen_task_id  = task.id
        return self.result


def _ctx() -> AgentContext:
    return AgentContext(
        params         = {"model": "GigaChat"},
        profile_text   = None,
        wm             = WorkingMemory(),
        client         = None,
        knowledge_repo = None,
    )


def _clean(reply: str = "ok") -> GuardedResult:
    return GuardedResult(reply=reply)


# ── предусловия ─────────────────────────────────────────────────────────────


def test_step_rejects_terminal_task():
    task = Task.new("x")
    task.state = TaskState.DONE
    orch = Orchestrator({}, _FakeTaskRepo())
    with pytest.raises(RuntimeError, match="терминальном"):
        orch.step(task, "", _ctx())


def test_step_rejects_when_awaiting_non_clarification():
    task = Task.new("x")
    task.awaiting = "plan_approval"
    orch = Orchestrator({TaskState.INTAKE: _RecordingAgent(TaskState.INTAKE,
                          AgentResult(reply="r", guarded=_clean()))},
                       _FakeTaskRepo())
    with pytest.raises(RuntimeError, match="ожидает"):
        orch.step(task, "", _ctx())


def test_step_rejects_when_no_agent_for_stage():
    task = Task.new("x")
    # нужная стадия есть в STAGE_PROMPTS, но агент не зарегистрирован
    assert TaskState.INTAKE in STAGE_PROMPTS
    orch = Orchestrator({}, _FakeTaskRepo())
    with pytest.raises(RuntimeError, match="Нет агента"):
        orch.step(task, "", _ctx())


# ── базовый ход + транзишены ───────────────────────────────────────────────


def test_step_with_clean_reply_closes_stage_and_transitions():
    task = Task.new("x")
    agent = _RecordingAgent(TaskState.INTAKE, AgentResult(
        reply="готово", guarded=_clean("готово"),
        auto_transition_to=TaskState.PLANNING,
        transition_reason="r",
    ))
    repo = _FakeTaskRepo()
    orch = Orchestrator({TaskState.INTAKE: agent}, repo,
                        now=lambda: "2026-06-19 10:00:00")

    reply = orch.step(task, "сделай", _ctx())
    assert reply == "готово"
    assert task.state == TaskState.PLANNING
    assert task.stages[TaskState.INTAKE].status == StageStatus.DONE
    assert task.stages[TaskState.INTAKE].finished_at == "2026-06-19 10:00:00"
    assert (TaskState.INTAKE, TaskState.PLANNING, "r") in repo.transitions


def test_step_with_questions_holds_stage_awaiting_clarification():
    task = Task.new("x")
    agent = _RecordingAgent(TaskState.INTAKE, AgentResult(
        reply="[QUESTION] что?", guarded=_clean(), questions=["что?"],
    ))
    orch = Orchestrator({TaskState.INTAKE: agent}, _FakeTaskRepo())

    orch.step(task, "x", _ctx())
    assert task.state == TaskState.INTAKE
    assert task.awaiting == "clarification"
    assert task.pending_questions == ["что?"]
    assert task.stages[TaskState.INTAKE].status == StageStatus.AWAITING_USER


def test_step_with_plan_approval_awaits():
    task = Task.new("x")
    task.transition(TaskState.PLANNING)
    agent = _RecordingAgent(TaskState.PLANNING, AgentResult(
        reply="план", guarded=_clean(), awaits_plan_approval=True,
    ))
    orch = Orchestrator({TaskState.PLANNING: agent}, _FakeTaskRepo())

    orch.step(task, "", _ctx())
    assert task.state == TaskState.PLANNING
    assert task.awaiting == "plan_approval"
    assert task.stages[TaskState.PLANNING].status == StageStatus.AWAITING_USER


# ── впитывание ответа на уточнения ──────────────────────────────────────────


def test_step_absorbs_clarification_answer_and_does_not_pass_it_as_message():
    task = Task.new("x")
    task.pending_questions = ["q?"]
    task.awaiting = "clarification"
    agent = _RecordingAgent(TaskState.INTAKE, AgentResult(
        reply="готово", guarded=_clean(),
        auto_transition_to=TaskState.PLANNING,
    ))
    orch = Orchestrator({TaskState.INTAKE: agent}, _FakeTaskRepo(),
                        now=lambda: "2026-06-19 10:00:00")

    orch.step(task, "мой ответ", _ctx())
    # ответ ушёл в answers, в агент followup_message пустой
    assert task.pending_questions == []
    assert task.answers[-1]["a"] == "мой ответ"
    assert task.answers[-1]["kind"] == "clarification"
    assert agent.seen_followup == ""


# ── ошибка агента ───────────────────────────────────────────────────────────


def test_step_marks_stage_failed_and_reraises_on_agent_exception():
    task = Task.new("x")

    class _Boom:
        stage = TaskState.INTAKE
        def run(self, task, followup, ctx):
            raise RuntimeError("сеть")

    repo = _FakeTaskRepo()
    orch = Orchestrator({TaskState.INTAKE: _Boom()}, repo)
    with pytest.raises(RuntimeError, match="сеть"):
        orch.step(task, "x", _ctx())
    assert task.stages[TaskState.INTAKE].status == StageStatus.FAILED


# ── нарушения инвариантов оседают в artifacts ───────────────────────────────


def test_step_records_violations_in_artifacts():
    from domain.invariant import InvariantSeverity, Violation
    task = Task.new("x")
    v = Violation(invariant_id="i1", title="t", severity=InvariantSeverity.WARN,
                  reason="плохо")
    agent = _RecordingAgent(TaskState.INTAKE, AgentResult(
        reply="ok",
        guarded=GuardedResult(reply="ok", violations=(v,), retries_used=0, blocked=False),
        auto_transition_to=TaskState.PLANNING,
    ))
    orch = Orchestrator({TaskState.INTAKE: agent}, _FakeTaskRepo())

    orch.step(task, "x", _ctx())
    entries = task.stages[TaskState.INTAKE].artifacts.get("invariant_violations")
    assert entries and entries[0]["violations"][0]["id"] == "i1"
