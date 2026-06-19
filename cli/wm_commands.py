"""CLI-обработчик /wm — управление рабочей памятью."""
from __future__ import annotations

from app.ports import WorkingMemoryRepository
from cli.ansi import BOLD, DIM, GREEN, MAGENTA, RESET, YELLOW
from cli.views import wm_show
from domain.working_memory import WorkingMemory


def handle_wm(cmd_str: str,
              wm: WorkingMemory,
              wm_repo: WorkingMemoryRepository) -> None:
    """Обрабатывает /wm <sub> <args>."""
    parts = cmd_str.split(None, 2)
    sub   = parts[1].lower() if len(parts) > 1 else "show"

    if sub in ("show", ""):
        print(f"\n{BOLD}{MAGENTA}Рабочая память:{RESET}")
        wm_show(wm)
        print()

    elif sub == "task":
        desc = parts[2].strip() if len(parts) > 2 else ""
        if not desc:
            try:
                desc = input("  Описание задачи: ").strip()
            except (EOFError, KeyboardInterrupt):
                return
        if desc:
            wm.task = desc
            wm_repo.save(wm)
            print(f"{GREEN}  Задача установлена.{RESET}")

    elif sub == "set":
        rest = parts[2] if len(parts) > 2 else ""
        kv   = rest.split(None, 1)
        if len(kv) < 2:
            print(f"{YELLOW}  Использование: /wm set <ключ> <значение>{RESET}")
        else:
            wm.context[kv[0]] = kv[1]
            wm_repo.save(wm)
            print(f"{GREEN}  Сохранено: {kv[0]} = {kv[1]}{RESET}")

    elif sub == "note":
        text = parts[2].strip() if len(parts) > 2 else ""
        if not text:
            try:
                text = input("  Заметка: ").strip()
            except (EOFError, KeyboardInterrupt):
                return
        if text:
            wm.notes.append(text)
            wm_repo.save(wm)
            print(f"{GREEN}  Заметка добавлена.{RESET}")

    elif sub == "del":
        key = parts[2].strip() if len(parts) > 2 else ""
        if key in wm.context:
            del wm.context[key]
            wm_repo.save(wm)
            print(f"{GREEN}  Ключ «{key}» удалён.{RESET}")
        else:
            print(f"{YELLOW}  Ключ «{key}» не найден.{RESET}")

    elif sub == "clear":
        wm_repo.clear()
        wm.task = wm.created_at = wm.updated_at = None
        wm.context.clear()
        wm.notes.clear()
        print(f"{DIM}  Рабочая память очищена.{RESET}")

    else:
        print(f"{YELLOW}  Подкоманды /wm: show · task · set · note · del · clear{RESET}")
