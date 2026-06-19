"""Юнит-тесты use cases с фейковыми портами (без сети и файловой системы)."""
from __future__ import annotations

from typing import Optional

import pytest

from app.task_driver import (
    PLAN_APPROVAL_APPROVED,
    PLAN_APPROVAL_REJECTED,
    PLAN_APPROVAL_RETRY,
    advance_task,
    handle_plan_approval,
    handle_plan_revision,
)
from domain.task import StageResult, StageStatus, Task, TaskState
from domain.working_memory import WorkingMemory


# ── фейки ───────────────────────────────────────────────────────────────────


class FakeTaskRepo:
    def __init__(self):
        self.saved: list[Task] = []
        self.transitions: list[tuple[str, str, str]] = []

    def save(self, task: Task) -> None:
        self.saved.append(task)

    def transition(self, task: Task, new_state: str, reason: str = "") -> None:
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


class FakeKnowledgeRepo:
    def __init__(self, text: str = ""):
        self._text = text
    def all_as_prompt(self) -> str:
        return self._text
    def list_names(self): return []
    def load(self, name): return None
    def save(self, entry): pass


class FakeClient:
    """Фейковый GigaChat-клиент — возвращает заранее заданный ответ."""
    def __init__(self, reply: str = "ok", raises: Optional[Exception] = None):
        self.reply  = reply
        self.raises = raises
        self.calls: list[dict] = []

    def chat(self, messages, params, system_prompt=None):
        self.calls.append({
            "messages": list(messages),
            "params":   dict(params),
            "system_prompt": system_prompt,
        })
        if self.raises:
            raise self.raises
        return self.reply


def _wm() -> WorkingMemory:
    return WorkingMemory()


def _params() -> dict:
    return {"model": "GigaChat", "temperature": None, "max_tokens": None}


# ── advance_task: предусловия ───────────────────────────────────────────────


def test_advance_rejects_terminal_task():
    task = Task.new("x")
    task.state = TaskState.DONE
    with pytest.raises(RuntimeError, match="терминальном"):
        advance_task(task, "", _params(), None, _wm(),
                     FakeClient(), FakeTaskRepo(), FakeKnowledgeRepo())


def test_advance_rejects_when_awaiting_non_clarification():
    task = Task.new("x")
    task.awaiting = "plan_approval"
    with pytest.raises(RuntimeError, match="ожидает"):
        advance_task(task, "", _params(), None, _wm(),
                     FakeClient(), FakeTaskRepo(), FakeKnowledgeRepo())


# ── advance_task: основной поток ────────────────────────────────────────────


def test_advance_intake_clean_reply_auto_transitions_to_planning():
    task = Task.new("сделать API")
    repo = FakeTaskRepo()
    advance_task(task, "сделать API", _params(), None, _wm(),
                 FakeClient("Хорошо, всё понятно."), repo, FakeKnowledgeRepo(),
                 now=lambda: "2026-06-19 10:00:00")
    assert task.state == TaskState.PLANNING
    # должен быть как минимум один transition INTAKE → PLANNING
    states = [(p, n) for p, n, _ in repo.transitions]
    assert (TaskState.INTAKE, TaskState.PLANNING) in states


def test_advance_with_questions_sets_clarification_and_holds_stage():
    task = Task.new("x")
    reply = "[QUESTION] Какой стек?\n[QUESTION] Какая платформа?"
    advance_task(task, "x", _params(), None, _wm(),
                 FakeClient(reply), FakeTaskRepo(), FakeKnowledgeRepo())
    assert task.state == TaskState.INTAKE  # не уехало
    assert task.awaiting == "clarification"
    assert task.pending_questions == ["Какой стек?", "Какая платформа?"]
    assert task.stages[TaskState.INTAKE].status == StageStatus.AWAITING_USER


def test_advance_records_user_answer_and_clears_questions():
    task = Task.new("x")
    task.pending_questions = ["q1"]
    task.awaiting = "clarification"
    advance_task(task, "мой ответ", _params(), None, _wm(),
                 FakeClient("готово"), FakeTaskRepo(), FakeKnowledgeRepo(),
                 now=lambda: "2026-06-19 10:00:00")
    # ответ записан в answers, очередь вопросов очищена
    assert task.pending_questions == []
    assert task.answers[-1]["a"] == "мой ответ"
    assert task.answers[-1]["kind"] == "clarification"


def test_advance_planning_awaits_plan_approval_without_questions():
    task = Task.new("x")
    task.transition(TaskState.PLANNING)
    advance_task(task, "", _params(), None, _wm(),
                 FakeClient("вот план: 1) ...\nУтвердить план? [y/n]"),
                 FakeTaskRepo(), FakeKnowledgeRepo())
    assert task.state == TaskState.PLANNING  # не перешло в EXECUTION само
    assert task.awaiting == "plan_approval"
    assert task.stages[TaskState.PLANNING].status == StageStatus.AWAITING_USER


def test_advance_validation_ok_transitions_to_done():
    task = Task.new("x")
    task.transition(TaskState.PLANNING)
    task.transition(TaskState.EXECUTION)
    task.transition(TaskState.VALIDATION)
    advance_task(task, "", _params(), None, _wm(),
                 FakeClient("проверил, всё ок\n[VALIDATION OK]"),
                 FakeTaskRepo(), FakeKnowledgeRepo())
    assert task.state == TaskState.DONE
    assert task.is_terminal()


def test_advance_validation_issues_transitions_to_execution():
    task = Task.new("x")
    task.transition(TaskState.PLANNING)
    task.transition(TaskState.EXECUTION)
    task.transition(TaskState.VALIDATION)
    advance_task(task, "", _params(), None, _wm(),
                 FakeClient("есть проблемы\n[VALIDATION ISSUES]"),
                 FakeTaskRepo(), FakeKnowledgeRepo())
    assert task.state == TaskState.EXECUTION


def test_advance_validation_silent_stays_in_validation():
    task = Task.new("x")
    task.transition(TaskState.PLANNING)
    task.transition(TaskState.EXECUTION)
    task.transition(TaskState.VALIDATION)
    advance_task(task, "", _params(), None, _wm(),
                 FakeClient("без меток"),
                 FakeTaskRepo(), FakeKnowledgeRepo())
    # стадия закрыта как done, но переход не делается — пользователь решит руками
    assert task.state == TaskState.VALIDATION
    assert task.stages[TaskState.VALIDATION].status == StageStatus.DONE


def test_advance_chat_failure_marks_stage_failed_and_reraises():
    task = Task.new("x")
    err = RuntimeError("сеть")
    repo = FakeTaskRepo()
    with pytest.raises(RuntimeError, match="сеть"):
        advance_task(task, "x", _params(), None, _wm(),
                     FakeClient(raises=err), repo, FakeKnowledgeRepo())
    assert task.stages[TaskState.INTAKE].status == StageStatus.FAILED


def test_advance_passes_system_prompt_with_profile_and_task_block():
    task = Task.new("задача такая", now="2026-06-19 10:00")
    client = FakeClient("Хорошо.")
    advance_task(task, "задача такая", _params(),
                 "ты — Jarvis", _wm(),
                 client, FakeTaskRepo(),
                 FakeKnowledgeRepo("### a\nданные"))
    sp = client.calls[0]["system_prompt"]
    assert "Профиль" in sp
    assert "ты — Jarvis" in sp
    assert "База знаний" in sp
    assert "данные" in sp
    assert f"[ЗАДАЧА #{task.id}" in sp


def test_advance_does_not_send_user_message_when_answering_questions():
    """Ответ пользователя на [QUESTION] уходит в task block, а не как новое сообщение."""
    task = Task.new("x")
    task.pending_questions = ["q?"]
    task.awaiting = "clarification"
    client = FakeClient("готово")
    advance_task(task, "мой ответ", _params(), None, _wm(),
                 client, FakeTaskRepo(), FakeKnowledgeRepo())
    # messages пуст — followup_message подавлен
    assert client.calls[0]["messages"] == []


# ── handle_plan_approval ────────────────────────────────────────────────────


def test_plan_approval_yes_transitions_to_execution_and_closes_planning():
    task = Task.new("x")
    task.transition(TaskState.PLANNING)
    task.stages[TaskState.PLANNING] = StageResult(
        status=StageStatus.AWAITING_USER, output="план готов",
    )
    task.awaiting = "plan_approval"
    repo = FakeTaskRepo()

    result = handle_plan_approval(task, "y", repo,
                                  now=lambda: "2026-06-19 10:00:00")
    assert result == PLAN_APPROVAL_APPROVED
    assert task.state == TaskState.EXECUTION
    assert task.awaiting is None
    assert task.stages[TaskState.PLANNING].status == StageStatus.DONE
    assert task.stages[TaskState.PLANNING].finished_at == "2026-06-19 10:00:00"


def test_plan_approval_no_arms_revision_input():
    task = Task.new("x")
    task.transition(TaskState.PLANNING)
    task.awaiting = "plan_approval"
    result = handle_plan_approval(task, "n", FakeTaskRepo())
    assert result == PLAN_APPROVAL_REJECTED
    assert task.awaiting == "plan_revision_input"


def test_plan_approval_unrelated_input_returns_retry():
    task = Task.new("x")
    task.transition(TaskState.PLANNING)
    task.awaiting = "plan_approval"
    assert handle_plan_approval(task, "может быть", FakeTaskRepo()) == PLAN_APPROVAL_RETRY
    assert task.awaiting == "plan_approval"  # не изменилось


def test_plan_approval_accepts_russian_yes_no():
    task = Task.new("x")
    task.transition(TaskState.PLANNING)
    task.awaiting = "plan_approval"
    assert handle_plan_approval(task, "да", FakeTaskRepo()) == PLAN_APPROVAL_APPROVED

    task2 = Task.new("y")
    task2.transition(TaskState.PLANNING)
    task2.awaiting = "plan_approval"
    assert handle_plan_approval(task2, "нет", FakeTaskRepo()) == PLAN_APPROVAL_REJECTED


def test_plan_approval_raises_when_not_awaiting():
    task = Task.new("x")
    # task.awaiting is None
    with pytest.raises(RuntimeError, match="plan_approval"):
        handle_plan_approval(task, "y", FakeTaskRepo())


# ── handle_plan_revision ────────────────────────────────────────────────────


def test_plan_revision_archives_old_plan_and_records_feedback():
    task = Task.new("x")
    task.transition(TaskState.PLANNING)
    task.stages[TaskState.PLANNING] = StageResult(
        status=StageStatus.AWAITING_USER,
        output="старый план",
        started_at="2026-06-19 09:00:00",
        finished_at=None,
    )
    task.awaiting = "plan_revision_input"

    handle_plan_revision(task, "сделай попроще", FakeTaskRepo(),
                         now=lambda: "2026-06-19 10:00:00")

    st = task.stages[TaskState.PLANNING]
    assert st.output == ""
    assert st.status == StageStatus.PENDING
    assert st.started_at is None
    revisions = st.artifacts["revisions"]
    assert revisions == [{"output": "старый план", "at": "2026-06-19 10:00:00"}]

    assert task.answers[-1]["kind"] == "plan_revision"
    assert task.answers[-1]["a"] == "сделай попроще"
    assert task.awaiting is None


def test_plan_revision_raises_when_not_awaiting():
    task = Task.new("x")
    with pytest.raises(RuntimeError, match="plan_revision_input"):
        handle_plan_revision(task, "что-то", FakeTaskRepo())


def test_plan_revision_rejects_empty_input():
    task = Task.new("x")
    task.transition(TaskState.PLANNING)
    task.awaiting = "plan_revision_input"
    with pytest.raises(RuntimeError, match="Пустой"):
        handle_plan_revision(task, "   ", FakeTaskRepo())
