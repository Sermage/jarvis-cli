"""Пост-проверка ответа модели по инвариантам с автоматическим retry.

Это вторая половина двойной защиты из лекции (11.2):
  1) инварианты вставлены в system prompt (`app/system_prompt.py`);
  2) сгенерированный ответ прогоняется через `InvariantSet.check()`,
     и если есть block-нарушения, модели уходит feedback-сообщение
     с просьбой переделать — до `max_retries` раз.

warn-нарушения не блокируют, но возвращаются в `violations`, чтобы
UI мог показать предупреждение. После исчерпания retries при
сохранившихся block-нарушениях результат помечается `blocked=True`,
а в `reply` остаётся последний (нарушающий) ответ — пусть CLI решает,
показывать его пользователю или нет.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.ports import LLMClient
from domain.invariant import InvariantSet, InvariantSeverity, Violation


@dataclass(frozen=True)
class GuardedResult:
    reply: str
    violations: tuple[Violation, ...] = ()
    retries_used: int = 0
    blocked: bool = False


def guarded_chat(client: LLMClient,
                 messages: list,
                 params: dict,
                 system_prompt: Optional[str],
                 invariants: InvariantSet,
                 max_retries: int = 1) -> GuardedResult:
    """Вызвать модель и (если нужно) затребовать переделку через feedback.

    `messages` не модифицируется: при retry внутри строится временная
    история с добавленным assistant-ответом и user-feedback'ом.
    """
    reply = client.chat(messages, params, system_prompt)

    if invariants.is_empty():
        return GuardedResult(reply=reply)

    violations = invariants.check(reply)
    blocks = _blocks(violations)
    if not blocks:
        return GuardedResult(reply=reply, violations=tuple(violations))

    history = list(messages)
    last_violations = violations
    for attempt in range(1, max_retries + 1):
        history = history + [
            {"role": "assistant", "content": reply},
            {"role": "user",      "content": _feedback_text(blocks)},
        ]
        reply = client.chat(history, params, system_prompt)
        last_violations = invariants.check(reply)
        blocks = _blocks(last_violations)
        if not blocks:
            return GuardedResult(
                reply=reply,
                violations=tuple(last_violations),
                retries_used=attempt,
            )

    return GuardedResult(
        reply=reply,
        violations=tuple(last_violations),
        retries_used=max_retries,
        blocked=True,
    )


def _blocks(violations) -> list[Violation]:
    return [v for v in violations if v.severity is InvariantSeverity.BLOCK]


def _feedback_text(blocks: list[Violation]) -> str:
    lines = ["Твой предыдущий ответ нарушает инварианты проекта:"]
    for v in blocks:
        lines.append(f"- [{v.invariant_id}] {v.title}: {v.reason}")
    lines.append("")
    lines.append(
        "Переделай ответ так, чтобы он не нарушал перечисленные инварианты. "
        "Если выполнить запрос без нарушения принципиально невозможно — "
        "явно скажи об этом и предложи допустимый альтернативный вариант."
    )
    return "\n".join(lines)
