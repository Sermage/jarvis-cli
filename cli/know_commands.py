"""CLI-обработчик /know — управление долговременной базой знаний."""
from __future__ import annotations

from app.ports import KnowledgeRepository
from cli.ansi import BLUE, BOLD, DIM, GREEN, RESET, YELLOW
from domain.knowledge import KnowledgeEntry, sanitize_knowledge_name


def handle_know(cmd_str: str, repo: KnowledgeRepository) -> None:
    """/know save <имя> | /know list | /know show <имя>"""
    parts = cmd_str.split(None, 2)
    sub   = parts[1].lower() if len(parts) > 1 else "list"

    if sub == "list":
        names = repo.list_names()
        if not names:
            print(f"{DIM}  База знаний пуста. Используй /know save <имя>{RESET}")
        else:
            print(f"\n{BOLD}{BLUE}База знаний:{RESET}")
            for n in names:
                print(f"    {BLUE}•{RESET} {n}")
            print()

    elif sub == "save":
        name = sanitize_knowledge_name(parts[2]) if len(parts) > 2 else ""
        if not name:
            try:
                name = sanitize_knowledge_name(input("  Имя записи: "))
            except (EOFError, KeyboardInterrupt):
                return
        if not name:
            print(f"{YELLOW}  Имя не может быть пустым.{RESET}")
            return
        print(f"  Введите содержимое (пустая строка — конец):")
        lines = []
        try:
            while True:
                line = input("  > ")
                if line == "":
                    break
                lines.append(line)
        except (EOFError, KeyboardInterrupt):
            pass
        if lines:
            repo.save(KnowledgeEntry(name=name, content="\n".join(lines)))
            print(f"{GREEN}  Сохранено в базу знаний: {name}{RESET}")

    elif sub == "show":
        name = parts[2].strip() if len(parts) > 2 else ""
        if not name:
            print(f"{YELLOW}  Использование: /know show <имя>{RESET}")
            return
        entry = repo.load(name)
        if entry is None:
            print(f"{YELLOW}  Запись «{name}» не найдена.{RESET}")
        else:
            print(f"\n{BOLD}{BLUE}{entry.name}:{RESET}\n{entry.to_file_text()}")

    else:
        print(f"{YELLOW}  Подкоманды /know: list · save · show{RESET}")
