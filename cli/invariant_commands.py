"""CLI-обработчик /inv — управление инвариантами проекта.

Подкоманды:
  /inv list                  — показать все инварианты
  /inv show <id>             — показать один полностью (включая patterns)
  /inv add  <id>             — создать заготовку и открыть в $EDITOR
  /inv rm   <id>             — удалить (с подтверждением для block-инвариантов)
  /inv edit <id>             — открыть в $EDITOR

Полное редактирование (patterns, severity) — через `edit` в редакторе;
команды нацелены на самый ходовой UX, остальное — руками в JSON.
"""
from __future__ import annotations

import os
import subprocess

from app.ports import InvariantRepository
from cli.ansi import BLUE, BOLD, CYAN, DIM, GREEN, MAGENTA, RESET, YELLOW
from domain.invariant import (
    Invariant,
    InvariantSeverity,
    is_valid_invariant_id,
    sanitize_invariant_id,
)


def _open_in_editor(path: str) -> None:
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "nano"
    subprocess.call([editor, path])


def _severity_label(sev: InvariantSeverity) -> str:
    if sev is InvariantSeverity.BLOCK:
        return f"{MAGENTA}block{RESET}"
    return f"{YELLOW}warn{RESET}"


def handle_inv(cmd_str: str, repo: InvariantRepository) -> None:
    parts = cmd_str.split(None, 2)
    sub = parts[1].lower() if len(parts) > 1 else "list"

    if sub == "list":
        _cmd_list(repo)
    elif sub == "show":
        _cmd_show(repo, parts[2].strip() if len(parts) > 2 else "")
    elif sub == "add":
        _cmd_add(repo, parts[2].strip() if len(parts) > 2 else "")
    elif sub in ("rm", "remove", "delete"):
        _cmd_rm(repo, parts[2].strip() if len(parts) > 2 else "")
    elif sub == "edit":
        _cmd_edit(repo, parts[2].strip() if len(parts) > 2 else "")
    else:
        print(f"{YELLOW}  Подкоманды /inv: list · show · add · rm · edit{RESET}")


def _cmd_list(repo: InvariantRepository) -> None:
    inv_set = repo.load_all()
    if inv_set.is_empty():
        print(f"{DIM}  Инвариантов нет. /inv add <id> — создать.{RESET}")
        return
    print(f"\n{BOLD}{BLUE}Инварианты проекта:{RESET}")
    for inv in inv_set.items:
        status = "" if inv.enabled else f" {DIM}(off){RESET}"
        print(f"  {BLUE}•{RESET} {inv.id}  [{_severity_label(inv.severity)}]{status}  {inv.title}")
    print()


def _cmd_show(repo: InvariantRepository, raw_id: str) -> None:
    if not raw_id:
        print(f"{YELLOW}  Использование: /inv show <id>{RESET}")
        return
    inv_id = sanitize_invariant_id(raw_id)
    inv = repo.load(inv_id)
    if inv is None:
        print(f"{YELLOW}  Инвариант «{inv_id}» не найден.{RESET}")
        return
    print(f"\n{BOLD}{BLUE}{inv.id}{RESET}  [{_severity_label(inv.severity)}]"
          f"{'' if inv.enabled else f' {DIM}(off){RESET}'}")
    print(f"  {BOLD}Заголовок:{RESET} {inv.title}")
    print(f"  {BOLD}Правило:{RESET}   {inv.rule}")
    if inv.forbidden_patterns:
        print(f"  {BOLD}Запрещено:{RESET}")
        for p in inv.forbidden_patterns:
            print(f"    {DIM}-{RESET} {p}")
    if inv.required_patterns:
        print(f"  {BOLD}Обязательно:{RESET}")
        for p in inv.required_patterns:
            print(f"    {DIM}-{RESET} {p}")
    print(f"  {DIM}Файл: {repo.path_for(inv.id)}{RESET}\n")


def _cmd_add(repo: InvariantRepository, raw_id: str) -> None:
    if not raw_id:
        try:
            raw_id = input("  id инварианта (a-z, 0-9, дефисы): ").strip()
        except (EOFError, KeyboardInterrupt):
            return
    inv_id = sanitize_invariant_id(raw_id)
    if not is_valid_invariant_id(inv_id):
        print(f"{YELLOW}  Некорректный id. Допустимо: a-z, 0-9, дефис. Начинается не с дефиса.{RESET}")
        return
    if repo.exists(inv_id):
        print(f"{YELLOW}  Инвариант «{inv_id}» уже существует. /inv edit {inv_id}{RESET}")
        return

    try:
        title = input("  Заголовок (короткое название): ").strip()
        rule  = input("  Правило (одной фразой): ").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if not title or not rule:
        print(f"{YELLOW}  Заголовок и правило не могут быть пустыми.{RESET}")
        return

    inv = Invariant(
        id=inv_id,
        title=title,
        rule=rule,
        severity=InvariantSeverity.BLOCK,
        enabled=True,
    )
    repo.save(inv)
    print(f"{GREEN}  Создан инвариант «{inv_id}».{RESET}")
    print(f"{DIM}  Открыть редактор для добавления patterns? [y/N]{RESET}")
    try:
        ans = input("  > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        ans = ""
    if ans in ("y", "yes", "да", "д"):
        _open_in_editor(repo.path_for(inv_id))
        # Перечитываем, чтобы валидация формата произошла прямо сейчас.
        reloaded = repo.load(inv_id)
        if reloaded is None:
            print(f"{YELLOW}  Файл стал нечитаемым после редактирования.{RESET}")


def _cmd_edit(repo: InvariantRepository, raw_id: str) -> None:
    if not raw_id:
        print(f"{YELLOW}  Использование: /inv edit <id>{RESET}")
        return
    inv_id = sanitize_invariant_id(raw_id)
    if not repo.exists(inv_id):
        print(f"{YELLOW}  Инвариант «{inv_id}» не найден.{RESET}")
        return
    _open_in_editor(repo.path_for(inv_id))
    reloaded = repo.load(inv_id)
    if reloaded is None:
        print(f"{YELLOW}  Файл стал нечитаемым после редактирования "
              f"(проверь JSON: {repo.path_for(inv_id)}).{RESET}")
    else:
        print(f"{GREEN}  Инвариант «{inv_id}» обновлён.{RESET}")


def _cmd_rm(repo: InvariantRepository, raw_id: str) -> None:
    if not raw_id:
        print(f"{YELLOW}  Использование: /inv rm <id>{RESET}")
        return
    inv_id = sanitize_invariant_id(raw_id)
    inv = repo.load(inv_id)
    if inv is None:
        print(f"{YELLOW}  Инвариант «{inv_id}» не найден.{RESET}")
        return
    prompt = (f"  Удалить block-инвариант «{inv_id}»? Это снимет защиту. [y/N]: "
              if inv.severity is InvariantSeverity.BLOCK
              else f"  Удалить «{inv_id}»? [y/N]: ")
    try:
        confirm = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if confirm not in ("y", "yes", "да", "д"):
        print(f"{DIM}  Отменено.{RESET}")
        return
    repo.delete(inv_id)
    print(f"{GREEN}  Инвариант «{inv_id}» удалён.{RESET}")
