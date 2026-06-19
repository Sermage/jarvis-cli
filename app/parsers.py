"""Парсеры ответа модели: уточняющие вопросы и вердикт валидации."""
from __future__ import annotations

import re
from typing import Optional


_QUESTION_RE = re.compile(
    r"^\s*\[QUESTION\]\s*(.+?)(?=^\s*\[[A-Z][A-Z _]*\]|\Z)",
    re.MULTILINE | re.DOTALL,
)


def parse_questions(text: str) -> list[str]:
    """Извлекает уточняющие вопросы агента из ответа модели."""
    return [m.strip() for m in _QUESTION_RE.findall(text) if m.strip()]


# Метки вердикта стадии validation. Якорим на начало строки, чтобы случайные
# упоминания «[VALIDATION OK]» внутри прозы не триггерили автопереход.
_VALIDATION_OK_RE = re.compile(
    r"^\s*\[VALIDATION\s+OK\]\s*$", re.MULTILINE | re.IGNORECASE,
)
_VALIDATION_ISSUES_RE = re.compile(
    r"^\s*\[VALIDATION\s+(?:ISSUES|FAILED|FAIL|NOK)\]\s*$",
    re.MULTILINE | re.IGNORECASE,
)


def parse_validation_verdict(text: str) -> Optional[str]:
    """Возвращает 'ok', 'issues' или None (вердикт не задан).

    Если в ответе одновременно есть обе метки — приоритет у issues
    (безопаснее: лучше пройти ещё один круг execution, чем закрыть с проблемами).
    """
    has_issues = bool(_VALIDATION_ISSUES_RE.search(text))
    has_ok     = bool(_VALIDATION_OK_RE.search(text))
    if has_issues:
        return "issues"
    if has_ok:
        return "ok"
    return None
