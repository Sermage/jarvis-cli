#!/usr/bin/env python3
"""Живой демо-сценарий: DeepSeek + два MCP-сервера + ToolRouter.

Запускает оба stdio-сервера (calc + notes), даёт DeepSeek многошаговую
задачу и печатает trace tool-loop'а — чтобы было видно, что:
  • агент сам выбирает нужный тул;
  • запросы маршрутизируются на правильные серверы;
  • цепочка вызовов длинная и упорядоченная;
  • используются тулы с РАЗНЫХ серверов в одном диалоге.

Требует валидный DEEPSEEK_API_KEY в .env. Делает реальный POST на
api.deepseek.com — расходует токены.

Запуск:
    python3 examples/mcp_live_demo.py
"""
from __future__ import annotations

import json
import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.tool_router import ToolRouter  # noqa: E402
from cli.config import DEEPSEEK_CHAT_URL, load_env  # noqa: E402
from domain.mcp import McpServerConfig  # noqa: E402
from infra.deepseek_client import DeepSeekClient  # noqa: E402
from infra.mcp_registry import StdioMcpRegistry  # noqa: E402


class _InMemoryRepo:
    def __init__(self, items): self._items = items
    def list_all(self):        return list(self._items)
    def get(self, sid):
        for c in self._items:
            if c.server_id == sid:
                return c
        return None
    def save(self, cfg): pass
    def delete(self, sid): pass
    def set_enabled(self, sid, enabled): pass


PROMPT = (
    "У тебя есть два MCP-сервера: calc (add, multiply, sqrt) и "
    "notes (save_note, list_notes, read_note, delete_note). "
    "Сделай по шагам, используя ИМЕННО тулы (не считай в уме):\n"
    "1) Вычисли sqrt(2025).\n"
    "2) Умножь результат шага 1 на 7.\n"
    "3) Сохрани результат шага 2 как заметку с заголовком 'pi-ish'.\n"
    "4) Прочитай эту заметку обратно и сообщи её содержимое.\n"
    "5) Список всех заметок.\n"
    "В финальном ответе напиши ровно одну строку с числом из шага 2 "
    "и подтверждение, что заметка сохранена."
)


def main() -> int:
    load_env(os.path.join(ROOT, ".env"))
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("Нужен DEEPSEEK_API_KEY в .env", file=sys.stderr)
        return 1

    calc = McpServerConfig(server_id="calc",
                           command=sys.executable,
                           args=(os.path.join(ROOT, "examples/mcp_servers/calc_server.py"),))
    notes = McpServerConfig(server_id="notes",
                            command=sys.executable,
                            args=(os.path.join(ROOT, "examples/mcp_servers/notes_server.py"),))
    registry = StdioMcpRegistry(_InMemoryRepo([calc, notes]))
    registry.start_all()

    try:
        client = DeepSeekClient(api_key=api_key, chat_url=DEEPSEEK_CHAT_URL)
        router = ToolRouter(client, registry, max_iterations=12)
        tools  = router.collect_openai_tools()
        print(f"DeepSeek получит {len(tools)} тулов: "
              + ", ".join(t['function']['name'] for t in tools))
        print()
        print("USER:", PROMPT)
        print()

        result = router.chat(
            [{"role": "user", "content": PROMPT}],
            {"model": "deepseek-chat", "temperature": 0},
        )

        print("─" * 60)
        print(f"TOOL-LOOP TRACE ({len(result.trace)} вызов(ов), {result.iterations} итераций)")
        print("─" * 60)
        for inv in result.trace:
            args = json.dumps(inv.arguments, ensure_ascii=False)
            err  = " [ERROR]" if inv.is_error else ""
            print(f"  #{inv.iteration:>2}  {inv.server_id}.{inv.tool_name}{err}")
            print(f"        args: {args}")
            print(f"        → {inv.result_text[:120]}")
        print()
        print("─" * 60)
        print("FINAL REPLY:")
        print("─" * 60)
        print(result.reply)
        if result.truncated:
            print("\n[!] tool-loop был обрезан по лимиту max_iterations")

        servers_used = sorted({inv.server_id for inv in result.trace})
        print()
        print(f"задействовано серверов: {len(servers_used)} → {servers_used}")
        return 0
    finally:
        registry.shutdown()


if __name__ == "__main__":
    sys.exit(main())
