"""CLI-обработчики для /profile (выбор, создание, редактирование, удаление)."""
from __future__ import annotations

import os
import subprocess
from typing import Optional

from app.ports import ProfileRepository
from cli.ansi import BOLD, CYAN, DIM, GREEN, RESET, YELLOW
from domain.profile import Profile, sanitize_profile_name


def open_in_editor(path: str) -> None:
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "nano"
    subprocess.call([editor, path])


def create_profile(repo: ProfileRepository,
                   current: Optional[Profile]) -> Optional[Profile]:
    try:
        raw = input("Название профиля (например, android-dev): ").strip()
    except (EOFError, KeyboardInterrupt):
        return current
    if not raw:
        print(f"{YELLOW}Название не может быть пустым.{RESET}")
        return current

    name = sanitize_profile_name(raw)
    if repo.exists(name):
        print(f"{YELLOW}Профиль «{name}» уже существует.{RESET}")
    else:
        repo.save(Profile.from_template(raw))

    print(f"{DIM}Открываю редактор…{RESET}")
    open_in_editor(repo.path_for(name))

    loaded = repo.load(name)
    print(f"{GREEN}Профиль «{name}» загружен.{RESET}")
    return loaded


def edit_profile(repo: ProfileRepository,
                 current: Optional[Profile]) -> Optional[Profile]:
    names = repo.list_names()
    if not names:
        print(f"{YELLOW}Нет профилей для редактирования.{RESET}")
        return current

    print(f"\n{BOLD}Выберите профиль для редактирования:{RESET}")
    for i, n in enumerate(names, 1):
        marker = " ◀" if current and n == current.name else ""
        print(f"  {CYAN}{i}{RESET}. {n}{marker}")
    try:
        choice = input("Номер (Enter — отмена): ").strip()
    except (EOFError, KeyboardInterrupt):
        return current
    if not (choice.isdigit() and 1 <= int(choice) <= len(names)):
        return current

    target = names[int(choice) - 1]
    print(f"{DIM}Открываю редактор…{RESET}")
    open_in_editor(repo.path_for(target))

    if current and target == current.name:
        loaded = repo.load(target)
        print(f"{GREEN}Профиль «{target}» обновлён и перезагружен.{RESET}")
        return loaded
    print(f"{GREEN}Профиль «{target}» сохранён.{RESET}")
    return current


def delete_profile(repo: ProfileRepository,
                   current: Optional[Profile]) -> Optional[Profile]:
    names = repo.list_names()
    if not names:
        print(f"{YELLOW}Нет профилей для удаления.{RESET}")
        return current

    print(f"\n{BOLD}Выберите профиль для удаления:{RESET}")
    for i, n in enumerate(names, 1):
        marker = f" {YELLOW}◀ активный{RESET}" if current and n == current.name else ""
        print(f"  {CYAN}{i}{RESET}. {n}{marker}")
    try:
        choice = input("Номер (Enter — отмена): ").strip()
    except (EOFError, KeyboardInterrupt):
        return current
    if not (choice.isdigit() and 1 <= int(choice) <= len(names)):
        return current

    target = names[int(choice) - 1]
    try:
        confirm = input(f"Удалить «{target}»? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return current
    if confirm != "y":
        print(f"{DIM}Отменено.{RESET}")
        return current

    repo.delete(target)
    print(f"{GREEN}Профиль «{target}» удалён.{RESET}")
    if current and target == current.name:
        print(f"{YELLOW}Активный профиль удалён — профиль сброшен.{RESET}")
        return None
    return current


def choose_profile(repo: ProfileRepository,
                   current: Optional[Profile]) -> Optional[Profile]:
    names = repo.list_names()
    if not names:
        return repo.ensure_default()

    print(f"\n{BOLD}Выберите профиль:{RESET}")
    for i, n in enumerate(names, 1):
        marker = " ◀" if current and n == current.name else ""
        print(f"  {CYAN}{i}{RESET}. {n}{marker}")
    print(f"  {CYAN}+{RESET}. Создать новый")
    print(f"  {CYAN}n{RESET}. Без профиля")
    try:
        choice = input("Номер (Enter — оставить текущий): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return current

    if choice == "+":
        return create_profile(repo, current)
    if choice == "n":
        return None
    if choice.isdigit() and 1 <= int(choice) <= len(names):
        target = names[int(choice) - 1]
        loaded = repo.load(target)
        if loaded:
            print(f"{GREEN}Профиль: {target}{RESET}")
        return loaded
    return current
