"""Доменные модели AI-ревью пул-реквестов.

Чистые данные без I/O: сырой diff пул-реквеста и список изменённых файлов.
Получение этих данных (через `gh`/GitHub) — в infra за портом
`app.ports.DiffProvider`; генерация текста ревью — в use case `app/pr_review.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PrDiff:
    """Изменения одного пул-реквеста для ревью.

    number — номер PR (строкой, как приходит из CI/gh);
    diff   — unified diff всего PR (base..head);
    files  — пути изменённых файлов относительно корня репозитория.
    """
    number: str
    diff: str
    files: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        """Нечего ревьюить — ни diff, ни файлов."""
        return not self.diff.strip() and not self.files
