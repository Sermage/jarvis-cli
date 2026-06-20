"""Оркестратор роя агентов.

Заменяет тело `app.task_driver.advance_task`: валидирует предусловия,
впитывает ответ пользователя на уточняющие вопросы, делегирует ход модели
агенту текущей стадии и применяет его `AgentResult` к задаче (status,
artifacts, awaiting, переходы машины состояний).

Сами агенты живут в `app.agents`. Чистый ход модели — в `_BaseStageAgent`.
"""
from __future__ import annotations

import time
from typing import Callable, Mapping, Optional

from app.agents.base import AgentContext, AgentResult, StageAgent
from app.invariant_guard import GuardedResult
from app.ports import TaskRepository
from app.stage_prompts import STAGE_PROMPTS
from domain.task import StageResult, StageStatus, Task


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


class Orchestrator:
    """Координирует прохождение задачи по стадиям через рой агентов.

    Один прогон стадии = `step()`. Машина состояний остаётся в `Task`; здесь
    мы только решаем, когда её крутить.
    """

    def __init__(self,
                 agents: Mapping[str, StageAgent],
                 task_repo: TaskRepository,
                 now: Callable[[], str] = _now):
        self._agents    = dict(agents)
        self._task_repo = task_repo
        self._now       = now

    def step(self,
             task: Task,
             user_input: str,
             ctx: AgentContext) -> str:
        """Один прогон текущей стадии через рой.

        Если у задачи есть `pending_questions`, `user_input` трактуется как
        ответ на них: пишется в `task.answers`, очищается список вопросов,
        и только после этого зовётся агент. Если агент возвращает новые
        вопросы — стадия остаётся `awaiting_user`; иначе закрывается и
        (если задано) делается автоматический переход.
        """
        if task.is_terminal():
            raise RuntimeError(f"Задача в терминальном состоянии: {task.state}")
        if task.state not in STAGE_PROMPTS:
            raise RuntimeError(f"Для стадии {task.state} нет промпта")
        if task.state not in self._agents:
            raise RuntimeError(f"Нет агента для стадии {task.state}")

        # Если уже ждём чего-то отличного от уточнения (например, plan_approval) —
        # на это есть отдельный обработчик; этот код такой ввод не трогает.
        if task.awaiting and task.awaiting != "clarification":
            raise RuntimeError(f"Задача ожидает {task.awaiting}, а не свободного ответа")

        followup_message = self._absorb_clarification(task, user_input)

        stage_obj = task.stages.get(task.state) or StageResult()
        if stage_obj.started_at is None:
            stage_obj.started_at = self._now()
        stage_obj.status = StageStatus.IN_PROGRESS
        task.stages[task.state] = stage_obj
        self._task_repo.save(task)

        agent = self._agents[task.state]
        try:
            result = agent.run(task, followup_message, ctx)
        except Exception:
            stage_obj.status = StageStatus.FAILED
            self._task_repo.save(task)
            raise

        _record_violations(stage_obj, result.guarded, now=self._now())

        # Структурные артефакты от агента (например, подзадачи у роевого
        # исполнителя). Сливаем поверх — агент решает, перезаписывать или нет.
        if result.extra_artifacts:
            stage_obj.artifacts.update(result.extra_artifacts)

        # Накапливаем вывод стадии (важно при многошаговых итерациях).
        stage_obj.output = (stage_obj.output + "\n\n" + result.reply).strip() \
            if stage_obj.output else result.reply

        self._apply_result(task, stage_obj, result)
        return result.reply

    # ── helpers ────────────────────────────────────────────────────────────

    def _absorb_clarification(self, task: Task, user_input: str) -> str:
        """Если ждали ответ на [QUESTION] — забираем его в answers."""
        if task.pending_questions and user_input:
            task.answers.append({
                "kind":  "clarification",
                "stage": task.state,
                "q":     "\n".join(task.pending_questions),
                "a":     user_input,
                "at":    self._now(),
            })
            task.pending_questions = []
            task.awaiting = None
            self._task_repo.save(task)
            # user_input уже впитан в answers и попадёт в task_block — не дублируем
            # его как отдельное user-message, чтобы модель не отвечала на ответ как
            # на новый вопрос.
            return ""
        return user_input

    def _apply_result(self,
                      task: Task,
                      stage_obj: StageResult,
                      result: AgentResult) -> None:
        # Агент задал вопросы — стадия зависает в awaiting_user.
        if result.questions:
            task.pending_questions = result.questions
            task.awaiting = "clarification"
            # Откат: уточнения собираются не здесь, а на более ранней стадии
            # (например, на PLANNING всплыли вопросы — возвращаемся в INTAKE).
            # Текущая стадия сбрасывается до pending и пустого output, чтобы
            # после ответа пользователя её можно было пройти заново с чистого листа.
            if result.rollback_to:
                stage_obj.output      = ""
                stage_obj.status      = StageStatus.PENDING
                stage_obj.started_at  = None
                stage_obj.finished_at = None
                task.stages[task.state] = stage_obj
                self._task_repo.transition(
                    task,
                    result.rollback_to,
                    reason=result.transition_reason or "откат для сбора уточнений",
                )
                new_stage = task.stages.get(task.state) or StageResult()
                new_stage.status = StageStatus.AWAITING_USER
                task.stages[task.state] = new_stage
                self._task_repo.save(task)
                return
            stage_obj.status = StageStatus.AWAITING_USER
            task.stages[task.state] = stage_obj
            self._task_repo.save(task)
            return

        # План готов — ждём явного y/n.
        if result.awaits_plan_approval:
            task.pending_questions = []
            task.awaiting = "plan_approval"
            stage_obj.status = StageStatus.AWAITING_USER
            task.stages[task.state] = stage_obj
            self._task_repo.save(task)
            return

        # Стадия отработала без вопросов — закрываем её.
        task.pending_questions = []
        task.awaiting = None
        stage_obj.status = StageStatus.DONE
        stage_obj.finished_at = self._now()
        task.stages[task.state] = stage_obj
        self._task_repo.save(task)

        if result.auto_transition_to:
            self._task_repo.transition(
                task,
                result.auto_transition_to,
                reason=result.transition_reason,
            )


def build_default_orchestrator(task_repo: TaskRepository,
                               now: Callable[[], str] = _now) -> Orchestrator:
    """Сборка оркестратора со стандартным набором стадийных агентов."""
    from app.agents import build_default_agents
    return Orchestrator(build_default_agents(), task_repo, now=now)
