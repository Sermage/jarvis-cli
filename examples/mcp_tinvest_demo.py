#!/usr/bin/env python3
"""Длинный флоу через ТРИ MCP-сервера сразу:
  • tinvest  (HTTP)   — реальный T-Invest API
  • calc     (stdio)  — арифметика
  • notes    (stdio)  — заметки в памяти процесса

Цель — продемонстрировать, что:
  • агент сам выбирает инструменты с трёх разных серверов;
  • запросы корректно маршрутизируются по транспортам (HTTP vs stdio);
  • цепочка вызовов длинная и осмысленная.

Tinvest URL/токен берутся из ~/.claude.json (секция mcpServers.tinvest),
если они там есть и совпадают по сигнатуре, — это позволяет переиспользовать
уже настроенный сервер без дублирования секрета в .env.
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


def _load_tinvest_from_claude_config():
    path = os.path.expanduser("~/.claude.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        d = json.load(f)
    return (d.get("mcpServers") or {}).get("tinvest")


PROMPT = (
    "Тебе доступны 3 MCP-сервера:\n"
    " • tinvest — реальный T-Invest API (счета, позиции, цены, операции).\n"
    " • calc    — арифметика (add, multiply, sqrt).\n"
    " • notes   — заметки в памяти (save_note, list_notes, read_note).\n"
    "\n"
    "Сделай по шагам, используя ТОЛЬКО тулы (никаких числовых выводов "
    "«в голове» — каждое вычисление через calc):\n"
    " 1) tinvest.get_accounts — получи список счетов.\n"
    " 2) Возьми первый счёт и вызови tinvest.get_portfolio_summary "
    "    с этим account_id (если нужны другие параметры — выбери разумные).\n"
    " 3) Из ответа выдели число total_amount_portfolio или аналогичную "
    "    суммарную стоимость портфеля (число в рублях, поле может называться "
    "    'total_amount_portfolio', 'total_value', 'portfolio_value'). Если "
    "    несколько кандидатов — бери первое релевантное.\n"
    " 4) Через calc.multiply посчитай, какой это объём в долларах при курсе 100.\n"
    " 5) Через calc.sqrt оцени «корень» из этой суммы (просто как иллюстрация).\n"
    " 6) Сохрани в notes под именем 'portfolio-report' строку вида:\n"
    "       RUB=<число>  USD=<число>  sqrt=<число>\n"
    " 7) Прочитай заметку обратно через notes.read_note и убедись, что сохранилось.\n"
    "\n"
    "В финальном ответе одной строкой подытожь: сколько счетов нашёл, "
    "какая сумма портфеля, где сохранён отчёт."
)


def main() -> int:
    load_env(os.path.join(ROOT, ".env"))
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("Нужен DEEPSEEK_API_KEY в .env", file=sys.stderr)
        return 1

    tinvest_cc = _load_tinvest_from_claude_config()
    if not tinvest_cc or tinvest_cc.get("type") != "http":
        print("Не нашёл секцию mcpServers.tinvest (type=http) в ~/.claude.json",
              file=sys.stderr)
        return 2

    tinvest = McpServerConfig(
        server_id = "tinvest",
        transport = "http",
        url       = tinvest_cc["url"],
        headers   = dict(tinvest_cc.get("headers") or {}),
    )
    calc = McpServerConfig(
        server_id = "calc",
        command   = sys.executable,
        args      = (os.path.join(ROOT, "examples/mcp_servers/calc_server.py"),),
    )
    notes = McpServerConfig(
        server_id = "notes",
        command   = sys.executable,
        args      = (os.path.join(ROOT, "examples/mcp_servers/notes_server.py"),),
    )
    registry = StdioMcpRegistry(_InMemoryRepo([tinvest, calc, notes]))
    registry.start_all()

    try:
        clients = registry.clients()
        if len(clients) < 3:
            for sid, err in registry.failures():
                print(f"[ERROR] {sid}: {err}", file=sys.stderr)
        print(f"подключено серверов: {len(clients)} → "
              f"{[c.server_id for c in clients]}")

        client = DeepSeekClient(api_key=api_key, chat_url=DEEPSEEK_CHAT_URL)
        router = ToolRouter(client, registry, max_iterations=20)
        tools  = router.collect_openai_tools()
        print(f"всего тулов в LLM: {len(tools)}\n")

        print("USER:", PROMPT.replace("\n", "\n      "))
        print()
        result = router.chat(
            [{"role": "user", "content": PROMPT}],
            {"model": "deepseek-chat", "temperature": 0},
        )

        print("─" * 70)
        print(f"TOOL-LOOP TRACE ({len(result.trace)} вызов(ов), "
              f"{result.iterations} итераций)")
        print("─" * 70)
        for inv in result.trace:
            args = json.dumps(inv.arguments, ensure_ascii=False)
            err  = " [ERROR]" if inv.is_error else ""
            print(f"  #{inv.iteration:>2}  {inv.server_id}.{inv.tool_name}{err}")
            print(f"        args: {args[:200]}")
            res_preview = inv.result_text.replace("\n", " ⏎ ")
            print(f"        → {res_preview[:200]}")
        print()
        print("─" * 70)
        print("FINAL REPLY:")
        print("─" * 70)
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
