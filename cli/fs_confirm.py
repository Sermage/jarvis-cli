"""CLI-подтверждение записи файла с цветным diff.

`LocalFilesystemClient` перед записью вызывает `confirm(rel, diff)`. Здесь —
интерактивная реализация этого коллбэка: печатает unified diff с подсветкой
(удалённые строки красным, добавленные зелёным) и спрашивает y/n. Логика
раскраски вынесена в чистую `colorize_diff`, чтобы её можно было переиспользовать
для показа diff, который тул вернул после успешной записи.

Живёт в `cli/` (это ввод/вывод), в композит-рут прокидывается как обычный
callable — инфраструктурный клиент про терминал ничего не знает.
"""
from __future__ import annotations

import sys
from typing import Callable

from cli.ansi import BOLD, CYAN, DIM, GREEN, RED, RESET


def colorize_diff(diff: str) -> str:
    """Раскрасить unified diff: '-' строки красным, '+' зелёным, @@ голубым."""
    out: list[str] = []
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            out.append(f"{BOLD}{line}{RESET}")
        elif line.startswith("@@"):
            out.append(f"{CYAN}{line}{RESET}")
        elif line.startswith("+"):
            out.append(f"{GREEN}{line}{RESET}")
        elif line.startswith("-"):
            out.append(f"{RED}{line}{RESET}")
        else:
            out.append(f"{DIM}{line}{RESET}")
    return "\n".join(out)


def make_interactive_confirm(reader: Callable[[str], str] = input,
                             stream=None) -> Callable[[str, str], bool]:
    """Собрать confirm-коллбэк для `LocalFilesystemClient`.

    `reader` — как читать ответ пользователя (по умолчанию `input`); в тестах
    подменяется. Возвращает `True`, только если пользователь явно согласился.
    """
    out = stream or sys.stdout

    def confirm(rel: str, diff: str) -> bool:
        print(f"\n{BOLD}Агент хочет изменить файл:{RESET} {CYAN}{rel}{RESET}", file=out)
        print(colorize_diff(diff), file=out)
        try:
            answer = reader(f"{BOLD}Применить изменения? [y/N] {RESET}").strip().lower()
        except EOFError:
            answer = ""
        approved = answer in ("y", "yes", "д", "да")
        if not approved:
            print(f"{DIM}Пропущено — файл не изменён.{RESET}", file=out)
        return approved

    return confirm
