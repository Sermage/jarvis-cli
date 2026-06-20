"""Тесты роевого исполнителя: парсинг, диспатч, параллельное выполнение, слияние."""
from __future__ import annotations

import threading
import time
from typing import Optional

from app.agents import AgentContext
from app.agents.swarm import (
    Decomposer,
    GenericWorker,
    SwarmExecutorAgent,
    merge_subtask_results,
    parse_subtasks,
)
from domain.subtask import Subtask, SubtaskStatus, WorkerRole
from domain.task import Task, TaskState
from domain.working_memory import WorkingMemory


# ── фейки ───────────────────────────────────────────────────────────────────


class _FakeKnowledgeRepo:
    def all_as_prompt(self) -> str: return ""
    def list_names(self): return []
    def load(self, name): return None
    def save(self, entry): pass


class _ScriptedClient:
    """Возвращает заранее заданные ответы по очереди.

    Если ответ — callable, вызывает его с (messages, params, system_prompt)
    — удобно для имитации задержки/проверки промпта внутри потока.
    Когда очередь пуста, повторяется последний ответ.
    """
    def __init__(self, replies):
        self._replies = list(replies)
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def chat(self, messages, params, system_prompt=None):
        with self._lock:
            self.calls.append({
                "messages":      list(messages),
                "params":        dict(params),
                "system_prompt": system_prompt,
            })
            if not self._replies:
                raise AssertionError("scripted client: больше ответов нет")
            r = self._replies.pop(0) if len(self._replies) > 1 else self._replies[0]
        if callable(r):
            return r(messages, params, system_prompt)
        return r


def _ctx(client) -> AgentContext:
    return AgentContext(
        params         = {"model": "GigaChat"},
        profile_text   = None,
        wm             = WorkingMemory(),
        client         = client,
        knowledge_repo = _FakeKnowledgeRepo(),
    )


def _exec_task() -> Task:
    t = Task.new("сделать фичу X")
    t.transition(TaskState.PLANNING)
    t.transition(TaskState.EXECUTION)
    return t


# ── parse_subtasks ──────────────────────────────────────────────────────────


def test_parse_subtasks_extracts_role_and_description():
    text = (
        "[SUBTASK role=coder] Реализовать foo\n"
        "[SUBTASK role=tester] Покрыть foo тестами\n"
        "[SUBTASK role=writer] Описать в README\n"
    )
    subs = parse_subtasks(text)
    assert [s.role for s in subs] == ["coder", "tester", "writer"]
    assert subs[0].description == "Реализовать foo"


def test_parse_subtasks_normalizes_unknown_role_to_generic():
    subs = parse_subtasks("[SUBTASK role=ninja] что-то\n")
    assert subs[0].role == WorkerRole.GENERIC


def test_parse_subtasks_ignores_garbage_lines():
    text = (
        "Вот разбивка:\n"
        "[SUBTASK role=coder] Реализовать foo\n"
        "просто текст между маркерами\n"
        "[SUBTASK role=tester] Тесты\n"
    )
    subs = parse_subtasks(text)
    assert len(subs) == 2


def test_parse_subtasks_skips_empty_descriptions():
    # description должен быть непустым, иначе пропускаем
    text = "[SUBTASK role=coder]\n[SUBTASK role=coder] нормально\n"
    subs = parse_subtasks(text)
    assert len(subs) == 1
    assert subs[0].description == "нормально"


# ── Decomposer ──────────────────────────────────────────────────────────────


def test_decomposer_returns_subtasks_when_model_lists_them():
    reply = "[SUBTASK role=coder] A\n[SUBTASK role=writer] B"
    client = _ScriptedClient([reply])
    res = Decomposer().decompose(_exec_task(), "", _ctx(client))
    assert [s.role for s in res.subtasks] == ["coder", "writer"]
    assert res.questions == []


def test_decomposer_returns_questions_and_no_subtasks():
    client = _ScriptedClient(["[QUESTION] на какой платформе?"])
    res = Decomposer().decompose(_exec_task(), "", _ctx(client))
    assert res.questions == ["на какой платформе?"]
    assert res.subtasks == []


def test_decomposer_appends_instruction_to_system_prompt():
    client = _ScriptedClient(["[SUBTASK role=coder] X"])
    Decomposer().decompose(_exec_task(), "", _ctx(client))
    sp = client.calls[0]["system_prompt"]
    assert "РЕЖИМ ДЕКОМПОЗИЦИИ" in sp


# ── Worker ──────────────────────────────────────────────────────────────────


def test_worker_records_result_on_success():
    client = _ScriptedClient(["готовый код foo()"])
    st = Subtask.new(WorkerRole.GENERIC, "сделай foo")
    out = GenericWorker().execute(st, _exec_task(), _ctx(client))
    assert out.status == SubtaskStatus.DONE
    assert out.result == "готовый код foo()"


def test_worker_marks_failed_on_exception():
    def boom(*a, **kw): raise RuntimeError("сеть упала")
    client = _ScriptedClient([boom])
    st = Subtask.new(WorkerRole.GENERIC, "сделай foo")
    out = GenericWorker().execute(st, _exec_task(), _ctx(client))
    assert out.status == SubtaskStatus.FAILED
    assert "сеть упала" in (out.error or "")


def test_worker_passes_subtask_into_system_prompt():
    client = _ScriptedClient(["ok"])
    st = Subtask.new(WorkerRole.CODER, "уникальное_описание_xyz")
    GenericWorker().execute(st, _exec_task(), _ctx(client))
    sp = client.calls[0]["system_prompt"]
    assert "уникальное_описание_xyz" in sp
    assert "РЕЖИМ ВОРКЕРА" in sp


# ── SwarmExecutorAgent (полный поток) ───────────────────────────────────────


def test_swarm_executor_decomposes_and_runs_workers_in_parallel():
    decomp_reply = (
        "[SUBTASK role=coder] A\n"
        "[SUBTASK role=tester] B\n"
        "[SUBTASK role=writer] C\n"
    )
    barrier = threading.Barrier(3)

    def worker_reply(messages, params, system_prompt):
        # Если все три воркера действительно стартуют параллельно, они
        # одновременно достигнут барьера и пройдут его. Если бы выполнение
        # было последовательным — тест зависнет на таймауте барьера.
        barrier.wait(timeout=2.0)
        # вернём кусок system_prompt, чтобы и тут проверить роль
        if "(coder)" in system_prompt: return "res-coder"
        if "(tester)" in system_prompt: return "res-tester"
        if "(writer)" in system_prompt: return "res-writer"
        return "res-?"

    client = _ScriptedClient([decomp_reply, worker_reply])
    agent  = SwarmExecutorAgent(max_parallel=3)

    res = agent.run(_exec_task(), "", _ctx(client))
    assert res.questions == []
    assert "разбито на 3 подзадач" in res.reply
    assert "res-coder" in res.reply
    assert "res-tester" in res.reply
    assert "res-writer" in res.reply

    # artifacts уехали
    arts = res.extra_artifacts["subtasks"]
    assert [s["role"] for s in arts] == ["coder", "tester", "writer"]
    assert all(s["status"] == SubtaskStatus.DONE for s in arts)


def test_swarm_executor_propagates_decomposer_questions():
    client = _ScriptedClient(["[QUESTION] какая БД?"])
    res = SwarmExecutorAgent().run(_exec_task(), "", _ctx(client))
    assert res.questions == ["какая БД?"]
    assert res.extra_artifacts == {}


def test_swarm_executor_falls_back_to_single_shot_when_no_subtasks_parsed():
    # Декомпозитор вернул прозу без [SUBTASK] и без [QUESTION] — стадия
    # должна закрыться с этим текстом как обычно.
    client = _ScriptedClient(["просто описание, без меток"])
    res = SwarmExecutorAgent().run(_exec_task(), "", _ctx(client))
    assert res.questions == []
    assert res.reply == "просто описание, без меток"
    assert res.extra_artifacts == {}


def test_swarm_executor_records_failed_subtask_in_artifacts():
    decomp_reply = "[SUBTASK role=coder] A\n[SUBTASK role=tester] B\n"

    def maybe_fail(messages, params, system_prompt):
        if "(tester)" in system_prompt:
            raise RuntimeError("упало")
        return "ok-from-coder"

    client = _ScriptedClient([decomp_reply, maybe_fail])
    res = SwarmExecutorAgent(max_parallel=2).run(_exec_task(), "", _ctx(client))
    arts = res.extra_artifacts["subtasks"]
    statuses = {s["role"]: s["status"] for s in arts}
    assert statuses == {"coder": SubtaskStatus.DONE, "tester": SubtaskStatus.FAILED}
    assert "упало" in res.reply  # отчёт упомянул ошибку


# ── merge_subtask_results ───────────────────────────────────────────────────


def test_merge_keeps_input_order_regardless_of_completion_order():
    a = Subtask.new(WorkerRole.CODER,  "первая")
    b = Subtask.new(WorkerRole.WRITER, "вторая")
    a.result, a.status = "AAA", SubtaskStatus.DONE
    b.result, b.status = "BBB", SubtaskStatus.DONE
    text = merge_subtask_results([a, b])
    # порядок сохранён: первая до второй
    assert text.index("первая") < text.index("вторая")
    assert "AAA" in text and "BBB" in text
