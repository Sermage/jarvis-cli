#!/usr/bin/env python3
"""Демо AI-ассистента поддержки: вопрос + номер тикета → адресный ответ.

Собирает мини-сервис поддержки из трёх частей проекта:
  • RAG по FAQ         — MarkdownFaqRetrievalEngine над docs/support-faq/*.md;
  • стор тикетов (MCP) — TicketStoreClient (in-process, JSON users/tickets),
                         поднятый как обычный MCP-сервер в реестре;
  • tool-loop          — ToolRouter: агент сам вызывает support__get_ticket /
                         support__get_user, чтобы учесть контекст обращения.

Сценарий из задания: «Почему не работает авторизация? #T-1024». Тикет T-1024
завёл пользователь на тарифе Free, который входит через Google → это и есть
причина ошибки 403 (SSO недоступен на Free). Агент поднимает тикет, читает
FAQ и отвечает адресно.

Запуск:
    python3 examples/support_agent_demo.py                    # реальный DeepSeek, если есть ключ
    JARVIS_DEMO_SCRIPTED=1 python3 examples/support_agent_demo.py   # без ключа, детерминированно

Без DEEPSEEK_API_KEY автоматически включается скриптованный режим.
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.support_assistant import answer_support_question  # noqa: E402
from app.tool_router import ToolRouter  # noqa: E402
from cli.config import DEEPSEEK_CHAT_URL, load_env  # noqa: E402
from infra.deepseek_client import DeepSeekClient  # noqa: E402
from infra.faq_retrieval import MarkdownFaqRetrievalEngine  # noqa: E402
from infra.ticket_store_client import TicketStoreClient  # noqa: E402
from infra.mcp_registry import StdioMcpRegistry  # noqa: E402


QUESTION = "Почему не работает авторизация?"
TICKET   = "T-1024"

SAMPLE = {
    "users": [
        {"id": "U-100", "name": "Иван Петров", "email": "ivan@example.com",
         "plan": "Free", "auth_method": "SSO (Google)", "platform": "web"},
    ],
    "tickets": [
        {"id": "T-1024", "user_id": "U-100", "status": "open", "priority": "high",
         "product_area": "auth", "error_code": "403",
         "subject": "Не могу войти через Google",
         "messages": [
             {"author": "user", "text": "При входе через Google выдаёт ошибку 403."},
         ]},
    ],
}


class _EmptyRepo:
    def list_all(self):
        return []


class _ScriptedLLM:
    """Детерминированная замена LLM: те же tool_calls, что сделал бы агент."""

    def __init__(self):
        self._script = [
            {"content": None, "tool_calls": [self._call(
                "s1", "support__get_ticket", {"ticket_id": "T-1024"})]},
            {"content": None, "tool_calls": [self._call(
                "s2", "support__get_user", {"user_id": "U-100"})]},
            {"content": (
                "Иван, вход через Google (SSO) на вашем тарифе Free недоступен — "
                "поэтому и возникает ошибка 403. Варианты: войдите по email и "
                "паролю, либо перейдите на тариф Business, где SSO включён "
                "(раздел FAQ «Вход через Google (SSO)»).")},
        ]

    @staticmethod
    def _call(cid, name, args):
        return {"id": cid, "type": "function",
                "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}

    def chat(self, messages, params, system_prompt=None):
        return self._script.pop(0).get("content") or ""

    def chat_with_tools(self, messages, params, tools, system_prompt=None):
        return self._script.pop(0)


def _make_llm():
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    scripted = os.environ.get("JARVIS_DEMO_SCRIPTED") or not api_key
    if scripted:
        print("[режим] скриптованный LLM (детерминированно, без сети)\n")
        return _ScriptedLLM(), {"model": "scripted"}
    print("[режим] реальный DeepSeek\n")
    return (DeepSeekClient(api_key=api_key, chat_url=DEEPSEEK_CHAT_URL),
            {"model": "deepseek-chat", "temperature": 0.2})


def main() -> int:
    load_env(os.path.join(ROOT, ".env"))

    faq = MarkdownFaqRetrievalEngine(os.path.join(ROOT, "docs", "support-faq"))
    registry = StdioMcpRegistry(_EmptyRepo())
    registry.start_all()
    registry.register(TicketStoreClient(data=SAMPLE))

    try:
        llm, params = _make_llm()
        support_chat = ToolRouter(llm, registry, max_iterations=8)

        print("Тулы поддержки:",
              ", ".join(t.qualified_name for t in registry.all_tools()))
        print("FAQ готов:", faq.is_ready())
        print(f"\nВОПРОС: {QUESTION}  (тикет {TICKET})\n" + "─" * 60)

        ans = answer_support_question(QUESTION, TICKET, faq, support_chat, params)

        print("MCP-тулы, которые вызвал агент:")
        for tu in ans.tools_used:
            mark = "✗" if tu.is_error else "✓"
            print(f"  {mark} {tu.server_id}.{tu.tool_name} "
                  f"{json.dumps(tu.arguments, ensure_ascii=False)}")
        print("\nИсточники FAQ:")
        for c in ans.sources:
            print(f"  • {c.source} — {c.section}")
        print("─" * 60)
        print("ОТВЕТ:\n" + ans.reply)
        return 0
    finally:
        registry.shutdown()


if __name__ == "__main__":
    sys.exit(main())
