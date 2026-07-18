"""CLI-обработчик /help.

  /help              — статичная справка по командам (как раньше).
  /help <вопрос>     — ответ на вопрос о проекте по его документации (RAG)
                       + текущая git-ветка через MCP.

Тонкий слой: парсит ввод, зовёт use case `answer_project_question`, печатает
ответ и источники. Вся логика — в `app/project_help.py`.
"""
from __future__ import annotations

from typing import Optional

from app.ports import GitContextProvider, LLMClient, RetrievalEngine
from app.project_help import answer_project_question
from cli.ansi import BOLD, CYAN, DIM, GREEN, MAGENTA, RESET, YELLOW
from cli.spinner import Spinner
from cli.views import print_help


def handle_help(cmd_str: str,
                engine: Optional[RetrievalEngine],
                git: Optional[GitContextProvider],
                client: LLMClient,
                params: dict,
                top_k: int = 5) -> None:
    # Отрезаем саму команду «/help», остаётся вопрос.
    question = cmd_str[len("/help"):].strip()

    if not question:
        print_help()
        return

    if engine is None or not engine.is_ready():
        print(f"{YELLOW}  RAG-индекс не готов — не могу ответить по документации.{RESET}")
        print(f"{DIM}  Проверь путь индекса: /rag status. Собрать индекс — "
              f"ingest.py по README + docs.{RESET}")
        return

    with Spinner("Ищу в документации проекта..."):
        result = answer_project_question(question, engine, git, client, params, top_k=top_k)

    print(f"\n{BOLD}{GREEN}Справка по проекту:{RESET} {result.reply}\n")

    if result.branch:
        print(f"{DIM}  git-ветка (через MCP): {CYAN}{result.branch}{RESET}")
    if result.sources:
        locs = []
        for c in result.sources:
            loc = c.section or c.title or c.source
            locs.append(loc if loc == c.source else f"{c.source} — {loc}")
        seen: list[str] = []
        for l in locs:
            if l not in seen:
                seen.append(l)
        print(f"{DIM}  Источники: {MAGENTA}{' · '.join(seen)}{RESET}")
    print()
