"""Use cases работы с задачей: продвижение стадии, одобрение/правка плана."""
from __future__ import annotations

import time
from typing import Callable, Optional

from app.invariant_guard import GuardedResult, guarded_chat
from app.parsers import parse_questions, parse_validation_verdict
from app.ports import (
    GigaChatClient,
    InvariantRepository,
    KnowledgeRepository,
    TaskRepository,
)
from app.stage_prompts import STAGE_PROMPTS, build_task_block
from app.system_prompt import build_system_prompt
from domain.invariant import InvariantSet
from domain.task import StageResult, StageStatus, Task, TaskState
from domain.working_memory import WorkingMemory


# Результаты handle_plan_approval — простые строковые константы.
PLAN_APPROVAL_APPROVED = "approved"
PLAN_APPROVAL_REJECTED = "rejected"
PLAN_APPROVAL_RETRY    = "retry"

_YES = {"y", "yes", "да", "д"}
_NO  = {"n", "no", "нет", "н"}


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _record_violations(stage_obj: StageResult,
                       guarded: GuardedResult,
                       now: str) -> None:
    """Залогировать нарушения инвариантов в artifacts стадии.

    Сами тексты ответов модели тут не дублируются — они уходят в `output`.
    Это след для отладки: что было нарушено, сколько раз перегенерировали.
    """
    if not guarded.violations and not guarded.blocked:
        return
    entries = stage_obj.artifacts.setdefault("invariant_violations", [])
    entries.append({
        "at":           now,
        "blocked":      guarded.blocked,
        "retries_used": guarded.retries_used,
        "violations": [
            {
                "id":       v.invariant_id,
                "title":    v.title,
                "severity": v.severity.value,
                "reason":   v.reason,
            }
            for v in guarded.violations
        ],
    })


def advance_task(task: Task,
                 user_input: str,
                 params: dict,
                 profile_text: Optional[str],
                 wm: WorkingMemory,
                 client: GigaChatClient,
                 task_repo: TaskRepository,
                 knowledge_repo: KnowledgeRepository,
                 invariant_repo: Optional[InvariantRepository] = None,
                 restoration_hint: bool = False,
                 now: Callable[[], str] = _now) -> str:
    """Один прогон текущей стадии через модель.

    Если у задачи есть незакрытые вопросы (pending_questions), user_input
    трактуется как ответ на них: пишется в task.answers, очищается список
    вопросов, и только после этого зовётся модель. Если модель в новом ответе
    задаёт новые [QUESTION] — стадия остаётся awaiting_user; иначе — done.
    """
    if task.is_terminal():
        raise RuntimeError(f"Задача в терминальном состоянии: {task.state}")
    if task.state not in STAGE_PROMPTS:
        raise RuntimeError(f"Для стадии {task.state} нет промпта")

    # Если уже ждём чего-то отличного от уточнения (например, plan_approval) —
    # на это есть отдельный обработчик; этот код такой ввод не трогает.
    if task.awaiting and task.awaiting != "clarification":
        raise RuntimeError(f"Задача ожидает {task.awaiting}, а не свободного ответа")

    # 1) Если ждали ответа на уточнения — фиксируем его в answers.
    if task.pending_questions and user_input:
        task.answers.append({
            "kind":  "clarification",
            "stage": task.state,
            "q":     "\n".join(task.pending_questions),
            "a":     user_input,
            "at":    now(),
        })
        task.pending_questions = []
        task.awaiting = None
        task_repo.save(task)
        # user_input уже впитан в answers и попадёт в task_block — не дублируем
        # его как отдельное user-message, чтобы модель не отвечала на ответ как
        # на новый вопрос.
        followup_message = ""
    else:
        followup_message = user_input

    stage_obj = task.stages.get(task.state) or StageResult()
    if stage_obj.started_at is None:
        stage_obj.started_at = now()
    stage_obj.status = StageStatus.IN_PROGRESS
    task.stages[task.state] = stage_obj
    task_repo.save(task)

    base = build_system_prompt(profile_text, wm, knowledge_repo, invariant_repo) or ""
    task_block = build_task_block(task, restoration_hint=restoration_hint)
    system_prompt = (base + "\n\n" + task_block) if base else task_block

    stage_messages = []
    if followup_message:
        stage_messages.append({"role": "user", "content": followup_message})

    invariants = invariant_repo.load_all() if invariant_repo is not None else InvariantSet()
    try:
        guarded = guarded_chat(client, stage_messages, params, system_prompt,
                               invariants, max_retries=1)
    except Exception:
        stage_obj.status = StageStatus.FAILED
        task_repo.save(task)
        raise
    reply = guarded.reply
    _record_violations(stage_obj, guarded, now=now())

    # Накапливаем вывод стадии (важно при многошаговых итерациях).
    stage_obj.output = (stage_obj.output + "\n\n" + reply).strip() if stage_obj.output else reply

    # Разбираем reply: если в нём есть [QUESTION] — ставим awaiting_user.
    questions = parse_questions(reply)
    if questions:
        task.pending_questions = questions
        task.awaiting = "clarification"
        stage_obj.status = StageStatus.AWAITING_USER
        task.stages[task.state] = stage_obj
        task_repo.save(task)
        return reply

    if task.state == TaskState.PLANNING:
        # План готов — стадия не закрывается, ждём явного y/n от пользователя.
        # finished_at выставится позже, в handle_plan_approval после "y".
        task.pending_questions = []
        task.awaiting = "plan_approval"
        stage_obj.status = StageStatus.AWAITING_USER
        task.stages[task.state] = stage_obj
        task_repo.save(task)
        return reply

    # Стадия отработала без вопросов — закрываем её.
    task.pending_questions = []
    task.awaiting = None
    stage_obj.status = StageStatus.DONE
    stage_obj.finished_at = now()
    task.stages[task.state] = stage_obj
    task_repo.save(task)

    # Стадия intake: ответ без [QUESTION] означает, что модель готова планировать.
    # Без автоперехода задача залипает в intake и следующее сообщение пользователя
    # снова запускает intake-промпт, который снова просит уточнений — отсюда петля
    # и повторы одних и тех же вопросов.
    if task.state == TaskState.INTAKE:
        task_repo.transition(task, TaskState.PLANNING, reason="уточнения собраны, переходим к плану")

    # Стадия validation: автоматический переход по вердикту модели.
    if task.state == TaskState.VALIDATION:
        verdict = parse_validation_verdict(reply)
        if verdict == "ok":
            task_repo.transition(task, TaskState.DONE, reason="валидация пройдена")
        elif verdict == "issues":
            task_repo.transition(task, TaskState.EXECUTION, reason="валидация выявила проблемы")
        # verdict == None — оставляем validation done, пользователь решит руками.

    return reply


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
