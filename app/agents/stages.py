"""Конкретные агенты-стадии задачи.

Каждый агент решает только одно: что делать с ответом модели, если он
не содержит [QUESTION]. Общая часть (вызов LLM + guard + парсинг вопросов)
лежит в `_BaseStageAgent`.
"""
from __future__ import annotations

from app.agents.base import AgentResult, _BaseStageAgent
from app.invariant_guard import GuardedResult
from app.parsers import parse_validation_verdict
from domain.task import Task, TaskState


class IntakeAgent(_BaseStageAgent):
    """INTAKE: чистый ответ означает, что уточнения собраны → planning."""
    stage = TaskState.INTAKE

    def _on_clean_reply(self, task: Task, reply: str,
                        guarded: GuardedResult) -> AgentResult:
        return AgentResult(
            reply=reply, guarded=guarded,
            auto_transition_to=TaskState.PLANNING,
            transition_reason="уточнения собраны, переходим к плану",
        )


class PlannerAgent(_BaseStageAgent):
    """PLANNING: план готов — переходим в режим ожидания утверждения."""
    stage = TaskState.PLANNING

    def _on_clean_reply(self, task: Task, reply: str,
                        guarded: GuardedResult) -> AgentResult:
        return AgentResult(
            reply=reply, guarded=guarded,
            awaits_plan_approval=True,
        )


class ExecutorAgent(_BaseStageAgent):
    """EXECUTION: чистый ответ закрывает стадию; переход решает пользователь."""
    stage = TaskState.EXECUTION


class ValidatorAgent(_BaseStageAgent):
    """VALIDATION: вердикт модели определяет автоматический переход."""
    stage = TaskState.VALIDATION

    def _on_clean_reply(self, task: Task, reply: str,
                        guarded: GuardedResult) -> AgentResult:
        verdict = parse_validation_verdict(reply)
        if verdict == "ok":
            return AgentResult(
                reply=reply, guarded=guarded,
                auto_transition_to=TaskState.DONE,
                transition_reason="валидация пройдена",
            )
        if verdict == "issues":
            return AgentResult(
                reply=reply, guarded=guarded,
                auto_transition_to=TaskState.EXECUTION,
                transition_reason="валидация выявила проблемы",
            )
        return AgentResult(reply=reply, guarded=guarded)


def build_default_agents() -> dict:
    """Стандартный набор: по агенту на каждую нетерминальную стадию.

    На EXECUTION стоит роевой агент (`SwarmExecutorAgent`) с декомпозицией
    плана и параллельными воркерами. Простой одношаговый `ExecutorAgent`
    остаётся в модуле и используется в тестах и как fallback при необходимости.
    """
    from app.agents.swarm import SwarmExecutorAgent
    return {
        TaskState.INTAKE:     IntakeAgent(),
        TaskState.PLANNING:   PlannerAgent(),
        TaskState.EXECUTION:  SwarmExecutorAgent(),
        TaskState.VALIDATION: ValidatorAgent(),
    }
