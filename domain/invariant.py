"""Доменная модель инвариантов.

Инвариант — ограничение, которое не должно меняться от запроса к запросу:
выбранный стек, архитектура, бизнес-правила, запрет конкретных технологий.

Хранятся отдельно от диалога (см. `infra/invariant_repository.py`),
явно вставляются в system prompt и используются для пост-проверки ответа
модели через `InvariantSet.check()`.

Слой `domain/` — без I/O и внешних зависимостей.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def sanitize_invariant_id(raw: str) -> str:
    """Привести id к виду, безопасному для файловой системы и поиска."""
    s = raw.strip().lower().replace(" ", "-").replace("_", "-")
    s = re.sub(r"[^a-z0-9-]", "", s)
    return s.strip("-")


def is_valid_invariant_id(s: str) -> bool:
    return bool(_ID_RE.match(s))


class InvariantSeverity(str, Enum):
    BLOCK = "block"   # нарушение → ответ отбрасывается
    WARN  = "warn"    # нарушение → пометить, но не блокировать


@dataclass(frozen=True)
class Violation:
    invariant_id: str
    title: str
    reason: str
    severity: InvariantSeverity


@dataclass(frozen=True)
class Invariant:
    id: str
    title: str
    rule: str
    severity: InvariantSeverity = InvariantSeverity.BLOCK
    enabled: bool = True
    forbidden_patterns: tuple[str, ...] = field(default_factory=tuple)
    required_patterns:  tuple[str, ...] = field(default_factory=tuple)

    def check(self, text: str) -> list[Violation]:
        """Найти нарушения этого инварианта в тексте.

        Паттерны интерпретируются как regex (case-insensitive). Кривой regex
        не должен ломать всю проверку — такой паттерн просто игнорируется.
        """
        if not self.enabled:
            return []
        violations: list[Violation] = []
        for pat in self.forbidden_patterns:
            try:
                if re.search(pat, text, re.IGNORECASE):
                    violations.append(Violation(
                        invariant_id=self.id,
                        title=self.title,
                        reason=f"встречен запрещённый паттерн: {pat!r}",
                        severity=self.severity,
                    ))
            except re.error:
                continue
        for pat in self.required_patterns:
            try:
                if not re.search(pat, text, re.IGNORECASE):
                    violations.append(Violation(
                        invariant_id=self.id,
                        title=self.title,
                        reason=f"отсутствует обязательный паттерн: {pat!r}",
                        severity=self.severity,
                    ))
            except re.error:
                continue
        return violations


@dataclass(frozen=True)
class InvariantSet:
    items: tuple[Invariant, ...] = ()

    @classmethod
    def from_iterable(cls, invs: Iterable[Invariant]) -> "InvariantSet":
        return cls(items=tuple(invs))

    def is_empty(self) -> bool:
        return not self.items

    def get(self, invariant_id: str) -> Invariant | None:
        for inv in self.items:
            if inv.id == invariant_id:
                return inv
        return None

    def to_prompt(self) -> str:
        """Текстовое представление набора для system prompt."""
        if self.is_empty():
            return ""
        lines = []
        for inv in self.items:
            if not inv.enabled:
                continue
            tag = "ОБЯЗАТЕЛЬНО" if inv.severity is InvariantSeverity.BLOCK else "ЖЕЛАТЕЛЬНО"
            lines.append(f"- [{tag}] {inv.title}: {inv.rule}")
        return "\n".join(lines)

    def check(self, text: str) -> list[Violation]:
        """Найти все нарушения в тексте по всем активным инвариантам."""
        out: list[Violation] = []
        for inv in self.items:
            out.extend(inv.check(text))
        return out
