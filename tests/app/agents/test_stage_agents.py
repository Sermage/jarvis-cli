"""Юнит-тесты стадийных агентов на фейковом GigaChatClient."""
from __future__ import annotations

from typing import Optional

from app.agents import AgentContext
from app.agents.stages import (
    ExecutorAgent,
    IntakeAgent,
    PlannerAgent,
    ValidatorAgent,
    build_default_agents,
)
from domain.task import Task, TaskState
from domain.working_memory import WorkingMemory


class _FakeKnowledgeRepo:
    def __init__(self, text: str = ""):
        self._text = text
    def all_as_prompt(self) -> str: return self._text
    def list_names(self): return []
    def load(self, name): return None
    def save(self, entry): pass


class _FakeClient:
    def __init__(self, reply="ok", raises: Optional[Exception] = None):
        # reply может быть строкой (один и тот же ответ на каждый вызов) или
        # списком — тогда возвращаем элементы по очереди.
        self._replies = list(reply) if isinstance(reply, list) else None
        self.reply    = reply if self._replies is None else None
        self.raises   = raises
        self.calls: list[dict] = []
    def chat(self, messages, params, system_prompt=None):
        self.calls.append({
            "messages": list(messages),
            "params":   dict(params),
            "system_prompt": system_prompt,
        })
        if self.raises:
            raise self.raises
        if self._replies is not None:
            i = min(len(self.calls) - 1, len(self._replies) - 1)
            return self._replies[i]
        return self.reply


def _ctx(client) -> AgentContext:
    return AgentContext(
        params         = {"model": "GigaChat", "temperature": None, "max_tokens": None},
        profile_text   = None,
        wm             = WorkingMemory(),
        client         = client,
        knowledge_repo = _FakeKnowledgeRepo(),
    )


# ── IntakeAgent ─────────────────────────────────────────────────────────────


def test_intake_clean_reply_requests_transition_to_planning():
    task = Task.new("сделать API")
    reply = "Задача: сделать API.\n[INTAKE READY]"
    client = _FakeClient(reply)
    res = IntakeAgent().run(task, "сделать API", _ctx(client))
    assert res.questions == []
    assert res.auto_transition_to == TaskState.PLANNING
    assert not res.awaits_plan_approval
    assert len(client.calls) == 1  # якорь на месте — без перегенерации


def test_intake_with_questions_returns_them_and_no_transition():
    task = Task.new("x")
    reply = "[QUESTION] Стек?\n[QUESTION] Платформа?"
    res = IntakeAgent().run(task, "x", _ctx(_FakeClient(reply)))
    assert res.questions == ["Стек?", "Платформа?"]
    assert res.auto_transition_to is None


def test_intake_retries_when_anchor_missing():
    """Без [INTAKE READY] ответ считается невалидным — один retry."""
    task = Task.new("x")
    bad  = "Понял, делаю!"
    good = "Итоговая задача: x.\n[INTAKE READY]"
    client = _FakeClient([bad, good])
    res = IntakeAgent().run(task, "x", _ctx(client))
    assert len(client.calls) == 2
    assert "[INTAKE READY]" in res.reply
    assert res.auto_transition_to == TaskState.PLANNING
    second_user_msgs = [m for m in client.calls[1]["messages"] if m["role"] == "user"]
    assert second_user_msgs and "[INTAKE READY]" in second_user_msgs[0]["content"]


def test_intake_retry_questions_returned_to_user():
    """Если на ретрае модель задала уточняющие — отдаём их пользователю."""
    task = Task.new("x")
    bad = "сразу к реализации"
    qs  = "[QUESTION] какой стек?"
    client = _FakeClient([bad, qs])
    res = IntakeAgent().run(task, "x", _ctx(client))
    assert len(client.calls) == 2
    assert res.questions == ["какой стек?"]
    assert res.auto_transition_to is None


# ── PlannerAgent ────────────────────────────────────────────────────────────


def test_planner_clean_reply_awaits_plan_approval():
    task = Task.new("x")
    task.transition(TaskState.PLANNING)
    plan = "вот план: 1)...\n\nУтвердить план? [y/n]"
    client = _FakeClient(plan)
    res = PlannerAgent().run(task, "", _ctx(client))
    assert res.questions == []
    assert res.awaits_plan_approval
    assert res.auto_transition_to is None
    assert len(client.calls) == 1  # без перегенерации, якорь на месте


def test_planner_with_questions_rolls_back_to_intake():
    task = Task.new("x")
    task.transition(TaskState.PLANNING)
    res = PlannerAgent().run(task, "", _ctx(_FakeClient("[QUESTION] деплой куда?")))
    assert res.questions == ["деплой куда?"]
    assert not res.awaits_plan_approval
    assert res.rollback_to == TaskState.INTAKE
    assert res.transition_reason


def test_planner_retries_when_reply_is_not_a_plan():
    """Если модель выдала «реализацию» вместо плана (нет якоря) — переделать."""
    task = Task.new("x")
    task.transition(TaskState.PLANNING)
    bad  = "Понял, приступаю к реализации. **Стадия: EXECUTION** ..."
    good = "План:\n1) сделать A\n2) сделать B\n\nУтвердить план? [y/n]"
    client = _FakeClient([bad, good])
    res = PlannerAgent().run(task, "", _ctx(client))
    assert len(client.calls) == 2
    assert "Утвердить план?" in res.reply
    assert res.awaits_plan_approval
    assert res.questions == []
    # Во втором вызове в user-сообщении должна уйти фидбек-инструкция.
    second_user_msgs = [m for m in client.calls[1]["messages"] if m["role"] == "user"]
    assert second_user_msgs and "НЕ план" in second_user_msgs[0]["content"]


def test_planner_retry_questions_roll_back_to_intake():
    """Если на ретрае модель внезапно задала [QUESTION] — откат в INTAKE."""
    task = Task.new("x")
    task.transition(TaskState.PLANNING)
    bad  = "сразу к реализации"
    qs   = "[QUESTION] какой стек?"
    client = _FakeClient([bad, qs])
    res = PlannerAgent().run(task, "", _ctx(client))
    assert len(client.calls) == 2
    assert res.questions == ["какой стек?"]
    assert res.rollback_to == TaskState.INTAKE


# ── ExecutorAgent ───────────────────────────────────────────────────────────


def test_executor_clean_reply_closes_stage_without_transition():
    task = Task.new("x")
    task.transition(TaskState.PLANNING)
    task.transition(TaskState.EXECUTION)
    res = ExecutorAgent().run(task, "", _ctx(_FakeClient("сделал шаг 1")))
    assert res.questions == []
    assert res.auto_transition_to is None
    assert not res.awaits_plan_approval


# ── ValidatorAgent ──────────────────────────────────────────────────────────


def test_validator_ok_transitions_to_done():
    task = Task.new("x")
    task.transition(TaskState.PLANNING)
    task.transition(TaskState.EXECUTION)
    task.transition(TaskState.VALIDATION)
    res = ValidatorAgent().run(task, "", _ctx(_FakeClient("всё ок\n[VALIDATION OK]")))
    assert res.auto_transition_to == TaskState.DONE


def test_validator_issues_transitions_to_execution():
    task = Task.new("x")
    task.transition(TaskState.PLANNING)
    task.transition(TaskState.EXECUTION)
    task.transition(TaskState.VALIDATION)
    res = ValidatorAgent().run(task, "", _ctx(_FakeClient("есть проблема\n[VALIDATION ISSUES]")))
    assert res.auto_transition_to == TaskState.EXECUTION


def test_validator_silent_then_verdict_on_retry():
    """Если модель забыла метку — один retry; финальный вердикт пройдёт."""
    task = Task.new("x")
    task.transition(TaskState.PLANNING)
    task.transition(TaskState.EXECUTION)
    task.transition(TaskState.VALIDATION)
    bad  = "без меток"
    good = "Всё проверил.\n[VALIDATION OK]"
    client = _FakeClient([bad, good])
    res = ValidatorAgent().run(task, "", _ctx(client))
    assert len(client.calls) == 2
    assert res.auto_transition_to == TaskState.DONE


def test_validator_silent_twice_no_transition():
    """Если и retry без метки — переход не делаем."""
    task = Task.new("x")
    task.transition(TaskState.PLANNING)
    task.transition(TaskState.EXECUTION)
    task.transition(TaskState.VALIDATION)
    client = _FakeClient(["без меток", "снова без меток"])
    res = ValidatorAgent().run(task, "", _ctx(client))
    assert len(client.calls) == 2
    assert res.auto_transition_to is None


# ── build_default_agents ────────────────────────────────────────────────────


def test_default_agents_cover_all_nonterminal_stages():
    agents = build_default_agents()
    assert set(agents.keys()) == {
        TaskState.INTAKE, TaskState.PLANNING,
        TaskState.EXECUTION, TaskState.VALIDATION,
    }


# ── общий ход: system prompt получает task block ────────────────────────────


def test_agent_run_sends_system_prompt_with_task_block():
    task = Task.new("моя задача")
    client = _FakeClient("Хорошо.")
    IntakeAgent().run(task, "моя задача", _ctx(client))
    sp = client.calls[0]["system_prompt"]
    assert sp is not None
    assert f"[ЗАДАЧА #{task.id}" in sp
    assert "Исходный запрос" in sp


def test_agent_run_suppresses_user_message_when_empty_followup():
    task = Task.new("x")
    client = _FakeClient("ok")
    IntakeAgent().run(task, "", _ctx(client))
    assert client.calls[0]["messages"] == []
