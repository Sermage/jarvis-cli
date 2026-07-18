"""Тонкая обёртка над GitHub CLI (`gh`).

Общий транспорт для infra-реализаций, которым нужен GitHub: получить diff PR,
список файлов, оставить комментарий. Держим её отдельно, чтобы `gh`-вызов был в
одном месте и легко подменялся фейком в тестах (провайдеры принимают `run`
через конструктор).
"""
from __future__ import annotations

import subprocess
from typing import Optional


def run_gh(args: list[str], stdin: Optional[str] = None) -> str:
    """Выполнить `gh <args>` и вернуть stdout. Упасть с понятной ошибкой на ненулевом коде."""
    proc = subprocess.run(
        ["gh", *args],
        input=stdin,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"gh {' '.join(args)} завершился с кодом {proc.returncode}: "
            f"{proc.stderr.strip()}"
        )
    return proc.stdout
