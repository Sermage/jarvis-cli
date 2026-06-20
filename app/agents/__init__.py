"""Рой агентов: по одному агенту на стадию задачи.

Каждый агент владеет своим post-LLM решением (нужны ли вопросы, ждать ли
утверждения, какой следующий переход). Общая часть — вызов модели + guard —
живёт в `_BaseStageAgent`. Оркестратор (`app.orchestrator.Orchestrator`)
выбирает агента по текущей стадии задачи и применяет его `AgentResult`.
"""
from __future__ import annotations

from app.agents.base import AgentContext, AgentResult, StageAgent
from app.agents.stages import (
    ExecutorAgent,
    IntakeAgent,
    PlannerAgent,
    ValidatorAgent,
    build_default_agents,
)
from app.agents.swarm import (
    CoderWorker,
    Decomposer,
    GenericWorker,
    ResearcherWorker,
    SwarmExecutorAgent,
    TesterWorker,
    WriterWorker,
    build_default_workers,
)

__all__ = [
    "AgentContext",
    "AgentResult",
    "StageAgent",
    "IntakeAgent",
    "PlannerAgent",
    "ExecutorAgent",
    "ValidatorAgent",
    "build_default_agents",
    "Decomposer",
    "SwarmExecutorAgent",
    "CoderWorker",
    "ResearcherWorker",
    "WriterWorker",
    "TesterWorker",
    "GenericWorker",
    "build_default_workers",
]
