"""CLI-обработчик /support — AI-ассистент поддержки.

  /support <вопрос>            — ответ по FAQ (RAG).
  /support <вопрос> #T-1024    — ответ с учётом данных тикета (через MCP).

Тонкий слой: парсит ввод, вытаскивает номер тикета/пользователя, зовёт use
case `answer_support_question`, печатает ответ, задействованные тулы и
источники FAQ. Вся логика — в `app/support_assistant.py`.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from app.support_assistant import SupportChat, answer_support_question
from app.ports import RetrievalEngine
from cli.ansi import BOLD, CYAN, DIM, GREEN, MAGENTA, RESET, YELLOW
from cli.spinner import Spinner

# Идентификатор тикета (T-…) или пользователя (U-…), с необязательной решёткой.
_TICKET_RE = re.compile(r"#?\b([TU]-\w+)\b", re.IGNORECASE)


def _extract_ticket(payload: str) -> tuple[Optional[str], str]:
    """Вытащить первый #T-1024 / U-100 из текста; вернуть (id, вопрос-без-тега)."""
    m = _TICKET_RE.search(payload)
    if not m:
        return None, payload.strip()
    ticket = m.group(1).upper()
    # Убираем сам тег (вместе с решёткой) из вопроса, схлопываем пробелы.
    question = (payload[:m.start()] + payload[m.end():]).strip()
    question = re.sub(r"\s{2,}", " ", question)
    return ticket, question


def handle_support(cmd_str: str,
                   engine: Optional[RetrievalEngine],
                   support_chat: Optional[SupportChat],
                   params: dict,
                   top_k: int = 5) -> None:
    payload = cmd_str[len("/support"):].strip()

    if not payload:
        print(f"{DIM}  Использование: /support <вопрос> [#T-1024]{RESET}")
        print(f"{DIM}  Примеры:{RESET}")
        print(f"{DIM}    /support Почему не работает авторизация? #T-1024{RESET}")
        print(f"{DIM}    /support Как поменять тариф?{RESET}")
        return

    if support_chat is None:
        print(f"{YELLOW}  Ассистент поддержки недоступен: нужен провайдер с "
              f"tool calling (deepseek) для доступа к тикетам через MCP.{RESET}")
        print(f"{DIM}  Переключись: /provider deepseek{RESET}")
        return

    ticket_hint, question = _extract_ticket(payload)
    if not question:
        question = payload  # был только тег без текста — оставим как есть

    with Spinner("Ищу в FAQ и поднимаю контекст тикета..."):
        result = answer_support_question(
            question, ticket_hint, engine, support_chat, params, top_k=top_k)

    print(f"\n{BOLD}{GREEN}Поддержка:{RESET} {result.reply}\n")

    if result.ticket_id:
        print(f"{DIM}  Контекст тикета: {CYAN}{result.ticket_id}{RESET}")

    if result.tools_used:
        seen: list[str] = []
        for tu in result.tools_used:
            args = json.dumps(tu.arguments, ensure_ascii=False)
            mark = "✗" if tu.is_error else "✓"
            label = f"{mark} {tu.server_id}.{tu.tool_name} {args}"
            if label not in seen:
                seen.append(label)
        print(f"{DIM}  MCP-тулы: {CYAN}{' · '.join(seen)}{RESET}")

    if result.sources:
        locs: list[str] = []
        for c in result.sources:
            loc = c.section or c.title or c.source
            label = loc if loc == c.source else f"{c.source} — {loc}"
            if label not in locs:
                locs.append(label)
        print(f"{DIM}  Источники FAQ: {MAGENTA}{' · '.join(locs)}{RESET}")
    elif not result.used_faq:
        print(f"{DIM}  (в FAQ по запросу ничего не нашлось){RESET}")

    if result.truncated:
        print(f"{YELLOW}  (tool-loop прерван по лимиту итераций){RESET}")
    print()
