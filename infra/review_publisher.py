"""Публикация текста ревью комментарием в пул-реквест через GitHub CLI.

Реализует порт `app.ports.ReviewPublisher`. Тело ревью передаётся в
`gh pr comment --body-file -` через stdin, чтобы не создавать временных файлов
и не спотыкаться на спецсимволах Markdown в аргументах командной строки.
"""
from __future__ import annotations

from typing import Callable, Optional

from infra.gh import run_gh


class GhReviewPublisher:
    """ReviewPublisher поверх `gh pr comment`."""

    def __init__(self, run: Optional[Callable[..., str]] = None):
        self._run = run or run_gh

    def publish(self, pr: str, body: str) -> None:
        self._run(["pr", "comment", str(pr), "--body-file", "-"], stdin=body)
