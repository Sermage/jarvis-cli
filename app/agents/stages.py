"""Конкретные агенты-стадии задачи.

Каждый агент решает только одно: что делать с ответом модели, если он
не содержит [QUESTION]. Общая часть (вызов LLM + guard + парсинг вопросов)
лежит в `_BaseStageAgent`.
"""
from __future__ import annotations

from app.agents.base import AgentContext, AgentResult, _BaseStageAgent
from app.invariant_guard import GuardedResult
from app.parsers import (
    looks_like_intake_summary,
    looks_like_plan,
    parse_questions,
    parse_validation_verdict,
)
from domain.task import Task, TaskState


_PLAN_REDO_FEEDBACK = (
    "Твой предыдущий ответ — НЕ план: в нём нет обязательного якоря "
    "«Утвердить план? [y/n]» в конце. На стадии PLANNING нельзя выполнять "
    "задачу: ни кода, ни готовых артефактов, ни «сразу к реализации», даже "
    "если пользователь об этом просил. Перепиши ответ как пошаговый план: "
    "пронумерованные пункты, у каждого — что именно будет сделано и какой "
    "ожидаемый результат. В самом конце дословно: «Утвердить план? [y/n]»."
)

_INTAKE_REDO_FEEDBACK = (
    "Твой предыдущий ответ не закрывает стадию INTAKE: в нём нет обязательной "
    "метки [INTAKE READY] в самой последней строке. На INTAKE нельзя ни "
    "писать план, ни выполнять задачу, даже если пользователь просит "
    "«пропусти уточнения, сразу делай». Сформулируй итоговую задачу одним "
    "абзацем и в самой последней строке поставь ровно: [INTAKE READY]."
)

_VALIDATION_REDO_FEEDBACK = (
    "Твой предыдущий ответ не закрывает стадию VALIDATION: в нём нет ни "
    "[VALIDATION OK], ни [VALIDATION ISSUES] на отдельной строке. Без метки "
    "система не понимает, переходить к DONE или возвращаться к EXECUTION. "
    "Перепиши ответ и в самом конце поставь ровно одну метку на отдельной "
    "строке: [VALIDATION OK] — если проблем нет; [VALIDATION ISSUES] — если "
    "проблемы перечислены выше."
)


class IntakeAgent(_BaseStageAgent):
    """INTAKE: чистый ответ означает, что уточнения собраны → planning.

    Чтобы модель не «съезжала» из INTAKE в план/реализацию (особенно когда
    пользователь сам просит «не уточняй, сразу делай»), требуется обязательный
    якорь [INTAKE READY]. Без него ответ переделываем один раз с фидбеком —
    если и тогда якоря нет, всё равно отдаём ответ, но переход в PLANNING
    делаем как обычно (промпт уже отработал, а вторая защита не помогла).
    """
    stage = TaskState.INTAKE

    def run(self, task: Task, followup_message: str,
            ctx: AgentContext) -> AgentResult:
        from app.agents._prompting import build_full_prompt, call_llm

        system_prompt = build_full_prompt(task, ctx)
        guarded       = call_llm(ctx, system_prompt, user_message=followup_message)
        reply         = guarded.reply

        questions = parse_questions(reply)
        if questions:
            return AgentResult(reply=reply, guarded=guarded, questions=questions)

        if not looks_like_intake_summary(reply):
            retry   = call_llm(ctx, system_prompt, user_message=_INTAKE_REDO_FEEDBACK)
            reply   = retry.reply
            guarded = retry
            questions = parse_questions(reply)
            if questions:
                return AgentResult(reply=reply, guarded=guarded, questions=questions)

        return self._on_clean_reply(task, reply, guarded)

    def _on_clean_reply(self, task: Task, reply: str,
                        guarded: GuardedResult) -> AgentResult:
        return AgentResult(
            reply=reply, guarded=guarded,
            auto_transition_to=TaskState.PLANNING,
            transition_reason="уточнения собраны, переходим к плану",
        )


class PlannerAgent(_BaseStageAgent):
    """PLANNING: вопросы запрещены — либо план, либо откат на INTAKE.

    На стадии планирования промпт запрещает [QUESTION]: фаза уточнений уже
    отработала в INTAKE. Если модель всё же задала вопросы, это сигнал, что
    INTAKE не добрал контекст — возвращаем задачу в INTAKE, пусть уточнения
    собираются там. Чистый ответ означает готовый план → ждём утверждения.
    """
    stage = TaskState.PLANNING

    def run(self, task: Task, followup_message: str,
            ctx: AgentContext) -> AgentResult:
        from app.agents._prompting import build_full_prompt, call_llm

        system_prompt = build_full_prompt(task, ctx)
        guarded       = call_llm(ctx, system_prompt, user_message=followup_message)
        reply         = guarded.reply

        questions = parse_questions(reply)
        if questions:
            return AgentResult(
                reply=reply, guarded=guarded,
                questions=questions,
                rollback_to=TaskState.INTAKE,
                transition_reason="на PLANNING всплыли уточняющие вопросы — откат на INTAKE",
            )

        # Пост-проверка: модель иногда игнорирует промпт и на PLANNING выдаёт
        # сразу реализацию. Косвенный признак — отсутствие обязательного якоря
        # «Утвердить план? [y/n]». Просим переделать один раз.
        if not looks_like_plan(reply):
            retry    = call_llm(ctx, system_prompt, user_message=_PLAN_REDO_FEEDBACK)
            reply    = retry.reply
            guarded  = retry
            questions = parse_questions(reply)
            if questions:
                return AgentResult(
                    reply=reply, guarded=guarded,
                    questions=questions,
                    rollback_to=TaskState.INTAKE,
                    transition_reason="на PLANNING всплыли уточняющие вопросы — откат на INTAKE",
                )

        return self._on_clean_reply(task, reply, guarded)

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
    """VALIDATION: вердикт модели определяет автоматический переход.

    Если модель не поставила ни [VALIDATION OK], ни [VALIDATION ISSUES] —
    стадия молча зависает и переход не происходит. Чтобы такого не было,
    делаем один retry с явным фидбеком: «обязательно поставь метку».
    """
    stage = TaskState.VALIDATION

    def run(self, task: Task, followup_message: str,
            ctx: AgentContext) -> AgentResult:
        from app.agents._prompting import build_full_prompt, call_llm

        system_prompt = build_full_prompt(task, ctx)
        guarded       = call_llm(ctx, system_prompt, user_message=followup_message)
        reply         = guarded.reply

        questions = parse_questions(reply)
        if questions:
            return AgentResult(reply=reply, guarded=guarded, questions=questions)

        if parse_validation_verdict(reply) is None:
            retry   = call_llm(ctx, system_prompt, user_message=_VALIDATION_REDO_FEEDBACK)
            reply   = retry.reply
            guarded = retry
            questions = parse_questions(reply)
            if questions:
                return AgentResult(reply=reply, guarded=guarded, questions=questions)

        return self._on_clean_reply(task, reply, guarded)

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
