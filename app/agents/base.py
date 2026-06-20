"""Базовый протокол и общая реализация стадийного агента.

`StageAgent.run()` принимает задачу и опциональное followup-сообщение
пользователя, выполняет один прогон модели через `guarded_chat` и возвращает
`AgentResult`. Применением результата (status, transitions, awaiting) ведает
оркестратор, а не сам агент — так агент остаётся чистой единицей рассуждения.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol

from app.invariant_guard import GuardedResult
from app.parsers import parse_questions
from app.ports import (
    InvariantRepository,
    KnowledgeRepository,
    LLMClient,
)
from domain.task import Task
from domain.working_memory import WorkingMemory


@dataclass
class AgentContext:
    """Зависимости и параметры одного прогона агента.

    Контейнер; не несёт состояния задачи — оно живёт в `Task`.
    """
    params:           dict
    profile_text:     Optional[str]
    wm:               WorkingMemory
    client:           LLMClient
    knowledge_repo:   KnowledgeRepository
    invariant_repo:   Optional[InvariantRepository] = None
    restoration_hint: bool = False


@dataclass
class AgentResult:
    """Результат одного прогона агента.

    `reply` всегда заполнен. Если есть `questions` — стадия не закрывается,
    оркестратор переведёт задачу в `awaiting=clarification`. Если
    `awaits_plan_approval=True` — оркестратор поставит `awaiting=plan_approval`.
    Если задан `auto_transition_to` — после закрытия стадии оркестратор
    выполнит переход машины состояний с указанной причиной.

    `extra_artifacts` — словарь, который оркестратор сольёт поверх
    `stage.artifacts` после прогона. Через него агент сохраняет структурную
    телеметрию (например, список подзадач у роевого исполнителя).

    `rollback_to` используется вместе с `questions`: если агент решил, что
    уточнения должны собираться не здесь, а на более ранней стадии (например,
    PLANNING откатывается в INTAKE) — оркестратор переведёт задачу туда и
    повесит вопросы уже на новую стадию.
    """
    reply:                str
    guarded:              GuardedResult
    questions:            list[str] = field(default_factory=list)
    awaits_plan_approval: bool = False
    auto_transition_to:   Optional[str] = None
    transition_reason:    str = ""
    extra_artifacts:      dict = field(default_factory=dict)
    rollback_to:          Optional[str] = None


class StageAgent(Protocol):
    """Контракт стадийного агента."""
    stage: str

    def run(self,
            task: Task,
            followup_message: str,
            ctx: AgentContext) -> AgentResult: ...


class _BaseStageAgent:
    """Общая шкура: построение system prompt, вызов модели, разбор [QUESTION].

    Подклассы переопределяют `stage` и `_on_clean_reply` — что делать, когда
    ответ модели не содержит уточняющих вопросов.
    """
    stage: str = ""

    def run(self,
            task: Task,
            followup_message: str,
            ctx: AgentContext) -> AgentResult:
        from app.agents._prompting import build_full_prompt, call_llm

        system_prompt = build_full_prompt(task, ctx)
        guarded       = call_llm(ctx, system_prompt, user_message=followup_message)
        reply         = guarded.reply

        questions = parse_questions(reply)
        if questions:
            return AgentResult(reply=reply, guarded=guarded, questions=questions)

        return self._on_clean_reply(task, reply, guarded)

    def _on_clean_reply(self,
                        task: Task,
                        reply: str,
                        guarded: GuardedResult) -> AgentResult:
        """Решение агента, когда модель не задала вопросов. Переопределяется."""
        return AgentResult(reply=reply, guarded=guarded)
