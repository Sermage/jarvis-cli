"""Общие хелперы построения промптов и вызова модели для агентов.

Используются как стадийными агентами (`_BaseStageAgent`), так и роевыми
вспомогательными агентами (декомпозитор, воркеры). Логику собрали в одном
месте, чтобы все агенты одинаково проходили через `guarded_chat` и
одинаково собирали system prompt из профиля / памяти / инвариантов / блока задачи.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.invariant_guard import GuardedResult, guarded_chat
from app.stage_prompts import build_task_block
from app.system_prompt import build_system_prompt
from domain.invariant import InvariantSet
from domain.task import Task

if TYPE_CHECKING:
    from app.agents.base import AgentContext


def build_full_prompt(task: Task,
                      ctx: "AgentContext",
                      extra_instruction: str = "") -> str:
    """Собрать system prompt: профиль/память/знания/инварианты + блок задачи + опционально доп. инструкция."""
    base       = build_system_prompt(ctx.profile_text, ctx.wm,
                                     ctx.knowledge_repo, ctx.invariant_repo) or ""
    task_block = build_task_block(task, restoration_hint=ctx.restoration_hint)
    prompt     = (base + "\n\n" + task_block) if base else task_block
    if extra_instruction:
        prompt = prompt + "\n\n" + extra_instruction
    return prompt


def call_llm(ctx: "AgentContext",
             system_prompt: str,
             user_message: str = "",
             max_retries: int = 1) -> GuardedResult:
    """Прогон модели через guard. Пустой `user_message` означает «никаких сообщений в истории»."""
    messages: list = []
    if user_message:
        messages.append({"role": "user", "content": user_message})
    invariants = (ctx.invariant_repo.load_all()
                  if ctx.invariant_repo is not None else InvariantSet())
    return guarded_chat(ctx.client, messages, ctx.params,
                        system_prompt, invariants, max_retries=max_retries)
