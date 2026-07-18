"""Получение diff и списка изменённых файлов пул-реквеста через GitHub CLI.

Реализует порт `app.ports.DiffProvider`. Берёт данные реального PR (base..head)
командами `gh pr diff` и `gh pr view --json files`, поэтому работает и с
форк-ветками, и с любыми базовыми ветками — в отличие от локального git diff.

Транспорт `run` инжектится через конструктор: в тестах подменяется фейком,
в проде — `infra.gh.run_gh`.
"""
from __future__ import annotations

import json
from typing import Callable, Optional

from domain.review import PrDiff
from infra.gh import run_gh


class GhDiffProvider:
    """DiffProvider поверх `gh`."""

    def __init__(self, run: Optional[Callable[..., str]] = None):
        self._run = run or run_gh

    def fetch(self, pr: str) -> PrDiff:
        pr = str(pr)
        diff = self._run(["pr", "diff", pr])
        files_json = self._run(["pr", "view", pr, "--json", "files"])
        files = _parse_files(files_json)
        return PrDiff(number=pr, diff=diff, files=files)


def _parse_files(files_json: str) -> list[str]:
    """Достать список путей из вывода `gh pr view --json files`."""
    try:
        data = json.loads(files_json)
    except (ValueError, TypeError):
        return []
    return [f["path"] for f in data.get("files", []) if f.get("path")]
