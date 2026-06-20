"""Use cases работы с задачей: продвижение стадии, одобрение/правка плана.

Прогоном стадии теперь занимается рой агентов под управлением
`app.orchestrator.Orchestrator`. Эта функция — тонкая обёртка, оставлена
для совместимости с CLI и существующими тестами.
"""
from __future__ import annotations

import time
from typing import Callable, Optional

from app.agents import AgentContext
from app.orchestrator import Orchestrator, build_default_orchestrator
from app.ports import (
    InvariantRepository,
    KnowledgeRepository,
    LLMClient,
    TaskRepository,
)
from domain.task import StageStatus, Task, TaskState
from domain.working_memory import WorkingMemory


# Результаты handle_plan_approval — простые строковые константы.
PLAN_APPROVAL_APPROVED = "approved"
PLAN_APPROVAL_REJECTED = "rejected"
PLAN_APPROVAL_RETRY    = "retry"

_YES = {"y", "yes", "да", "д"}
_NO  = {"n", "no", "нет", "н"}


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def advance_task(task: Task,
                 user_input: str,
                 params: dict,
                 profile_text: Optional[str],
                 wm: WorkingMemory,
                 client: LLMClient,
                 task_repo: TaskRepository,
                 knowledge_repo: KnowledgeRepository,
                 invariant_repo: Optional[InvariantRepository] = None,
                 restoration_hint: bool = False,
                 now: Callable[[], str] = _now,
                 orchestrator: Optional[Orchestrator] = None) -> str:
    """Один прогон текущей стадии через рой агентов.

    Если оркестратор не передан — собирается дефолтный набор агентов на лету
    (удобно для тестов и существующих callsites). В CLI оркестратор
    создаётся один раз в composition root и переиспользуется.
    """
    orch = orchestrator or build_default_orchestrator(task_repo, now=now)
    ctx  = AgentContext(
        params           = params,
        profile_text     = profile_text,
        wm               = wm,
        client           = client,
        knowledge_repo   = knowledge_repo,
        invariant_repo   = invariant_repo,
        restoration_hint = restoration_hint,
    )
    return orch.step(task, user_input, ctx)


def handle_plan_approval(task: Task,
                         user_input: str,
                         task_repo: TaskRepository,
                         now: Callable[[], str] = _now) -> str:
    """Обработать y/n на запрос утверждения плана.

    Только мутирует task. UI и запуск execution делает вызывающий код,
    чтобы здесь не было зависимости от Spinner/print.
    """
    if task.awaiting != "plan_approval":
        raise RuntimeError(f"Задача не ждёт plan_approval (awaiting={task.awaiting!r})")
    ans = user_input.strip().lower()
    if ans in _YES:
        st = task.stages.get(TaskState.PLANNING)
        if st is not None:
            st.status      = StageStatus.DONE
            st.finished_at = now()
            task.stages[TaskState.PLANNING] = st
        task.awaiting = None
        task_repo.transition(task, TaskState.EXECUTION, reason="план утверждён пользователем")
        return PLAN_APPROVAL_APPROVED
    if ans in _NO:
        task.awaiting = "plan_revision_input"
        task_repo.save(task)
        return PLAN_APPROVAL_REJECTED
    return PLAN_APPROVAL_RETRY


def handle_plan_revision(task: Task,
                         user_input: str,
                         task_repo: TaskRepository,
                         now: Callable[[], str] = _now) -> None:
    """Зафиксировать правки от пользователя и подготовить planning к перегенерации.

    Старый план уходит в stages[planning].artifacts["revisions"], output чистится,
    статус сбрасывается в pending — следующий advance_task стартует «с нуля» и
    напишет новый план целиком. Пожелания пользователя сохраняются в answers
    с kind="plan_revision" и попадают в task_block следующего вызова.
    """
    if task.awaiting != "plan_revision_input":
        raise RuntimeError(f"Задача не ждёт plan_revision_input (awaiting={task.awaiting!r})")
    if not user_input.strip():
        raise RuntimeError("Пустой ответ — нечего править")

    moment = now()
    st  = task.stages.get(TaskState.PLANNING)
    if st is not None and st.output:
        revisions = st.artifacts.setdefault("revisions", [])
        revisions.append({"output": st.output, "at": moment})
        st.output       = ""
        st.status       = StageStatus.PENDING
        st.started_at   = None
        st.finished_at  = None
        task.stages[TaskState.PLANNING] = st

    task.answers.append({
        "kind":  "plan_revision",
        "stage": TaskState.PLANNING,
        "q":     "Что нужно поправить в плане?",
        "a":     user_input,
        "at":    moment,
    })
    task.awaiting = None
    task_repo.save(task)
