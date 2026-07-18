"""CLI-обработчик /review.

  /review <номер PR>  — сгенерировать AI-ревью пул-реквеста: достать diff и
                        изменённые файлы через GitHub, подмешать RAG-контекст
                        (документация + код) и вывести структурированный разбор.

Тонкий слой: парсит номер, зовёт use case `review_pull_request`, печатает
результат. В чате ревью только выводится; публикацию комментарием в PR делает
CI-энтрипойнт review_pr.py. Вся логика — в `app/pr_review.py`.
"""
from __future__ import annotations

from typing import Optional

from app.ports import DiffProvider, LLMClient, RetrievalEngine
from app.pr_review import review_pull_request
from cli.ansi import BOLD, CYAN, DIM, GREEN, MAGENTA, RESET, YELLOW
from cli.spinner import Spinner


def handle_review(cmd_str: str,
                  engine: Optional[RetrievalEngine],
                  diff_provider: DiffProvider,
                  client: LLMClient,
                  params: dict,
                  top_k: int = 5) -> None:
    arg = cmd_str[len("/review"):].strip()

    if not arg:
        print(f"{YELLOW}  Укажи номер PR: {RESET}{DIM}/review 12{RESET}")
        return
    if not arg.isdigit():
        print(f"{YELLOW}  Номер PR должен быть числом, например: {RESET}{DIM}/review 12{RESET}")
        return

    try:
        with Spinner(f"Достаю diff PR #{arg} через GitHub..."):
            pr = diff_provider.fetch(arg)
    except Exception as e:
        print(f"{YELLOW}  Не удалось получить PR #{arg}: {e}{RESET}")
        print(f"{DIM}  Проверь, что gh авторизован (gh auth status) и номер PR верный.{RESET}")
        return

    with Spinner("Анализирую изменения (RAG + LLM)..."):
        result = review_pull_request(pr.diff, pr.files, engine, client, params, top_k=top_k)

    print(f"\n{BOLD}{GREEN}AI-ревью PR #{arg}:{RESET}\n")
    print(result.text)
    print()

    if result.files:
        print(f"{DIM}  Изменённые файлы: {CYAN}{', '.join(result.files)}{RESET}")
    if result.sources:
        locs: list[str] = []
        for c in result.sources:
            loc = c.section or c.title or c.source
            label = loc if loc == c.source else f"{c.source} — {loc}"
            if label not in locs:
                locs.append(label)
        print(f"{DIM}  Контекст (RAG): {MAGENTA}{' · '.join(locs)}{RESET}")
    print()
