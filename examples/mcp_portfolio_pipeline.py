"""Комплексный пайплайн анализа портфеля через 6+ MCP-серверов.

Сценарий:
  1) time      — текущая дата в MSK (timestamp журнала)
  2) tinvest   — get_accounts + get_portfolio_summary
  3) calc      — расчёты процентов и средних
  4) sqlite    — INSERT в журнал portfolio_log + SELECT для верификации
  5) filesystem— записать markdown-отчёт + прочитать назад
  6) memory    — добавить факты о портфеле в knowledge graph

Этот демо показывает, что:
  • агент сам решает, какой тул из 51 доступного нужен на каждом шаге;
  • маршрутизация работает через 2 транспорта (HTTP для tinvest, stdio для всех остальных);
  • один цельный длинный флоу пересекает границы ≥6 серверов.

Берёт конфиг ровно тот же, что и `python3 chat.py` — `~/.jarvis/mcp/servers.json`.
"""
from __future__ import annotations

import json
import os
import sys


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.tool_router import ToolRouter  # noqa: E402
from cli.config import DEEPSEEK_CHAT_URL, MCP_CONFIG_FILE, load_env  # noqa: E402
from infra.deepseek_client import DeepSeekClient  # noqa: E402
from infra.mcp_config_repository import FileMcpConfigRepository  # noqa: E402
from infra.mcp_registry import StdioMcpRegistry  # noqa: E402


PROMPT = """\
Тебе доступны MCP-серверы: tinvest (T-Invest API), sqlite (журнал сделок),
filesystem (рабочая папка ~/.jarvis/mcp-workspace), memory (граф знаний),
time, calc, notes. Пайплайн — строго в этом порядке, каждый шаг через тул:

Шаг 1. time.get_current_time(timezone="Europe/Moscow") — запиши результат.

Шаг 2. tinvest.get_accounts(). Возьми account_id первого счёта (ACCOUNT_TYPE_TINKOFF).

Шаг 3. tinvest.get_portfolio_summary(account_id, days=7).
Найди в результате значение поля "total_value" (если нет — "total_amount_portfolio").
Это рубли. Пусть это V_RUB.

Шаг 4. calc.multiply(a=V_RUB, b=0.011) — пересчёт в USD по курсу 90.91 (1/0.011).
Получи V_USD.

Шаг 5. sqlite.list_tables(). Если таблицы portfolio_log нет —
sqlite.create_table со схемой:
  CREATE TABLE portfolio_log (
    ts TEXT,
    account_id TEXT,
    rub REAL,
    usd REAL
  )

Шаг 6. sqlite.write_query — INSERT INTO portfolio_log VALUES (
'<timestamp-from-step-1>', '<account_id>', <V_RUB>, <V_USD>).

Шаг 7. sqlite.read_query — SELECT * FROM portfolio_log ORDER BY ts DESC LIMIT 5.

Шаг 8. filesystem.write_file(path="portfolio-report.md", content=<markdown>).
В markdown — таблица с timestamp/account/rub/usd, заголовок "# Portfolio snapshot".

Шаг 9. filesystem.read_file(path="portfolio-report.md") — убедись, что записалось.

Шаг 10. memory.create_entities — создай сущность с
name="Portfolio_<account_id>", entityType="account",
observations=["snapshot at <ts>", "RUB=<V_RUB>", "USD=<V_USD>"].

В финальном ответе одной фразой: сколько шагов выполнил, текущий V_RUB, путь к отчёту.
Без markdown-разметки в финальном ответе, только обычный текст.
"""


def main() -> int:
    load_env(os.path.join(ROOT, ".env"))
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        print("Нужен DEEPSEEK_API_KEY в .env", file=sys.stderr)
        return 1

    repo = FileMcpConfigRepository(MCP_CONFIG_FILE)
    print(f"конфиг: {len(repo.list_all())} серверов")

    registry = StdioMcpRegistry(repo)
    print("поднимаю MCP-серверы...")
    registry.start_all()
    print(f"запустилось: {len(registry.clients())} из {len(repo.list_all())}")
    for sid, err in registry.failures():
        print(f"  [ERROR] {sid}: {err[:200]}")
    tool_count = len(registry.all_tools())
    print(f"всего тулов в LLM: {tool_count}\n")

    try:
        client = DeepSeekClient(api_key=api_key, chat_url=DEEPSEEK_CHAT_URL)
        router = ToolRouter(client, registry, max_iterations=30)

        print("USER:", PROMPT.replace("\n", "\n      "))
        print()
        result = router.chat(
            [{"role": "user", "content": PROMPT}],
            {"model": "deepseek-chat", "temperature": 0},
        )

        print("─" * 78)
        print(f"TOOL-LOOP TRACE ({len(result.trace)} вызов(ов), "
              f"{result.iterations} итераций)")
        print("─" * 78)
        for inv in result.trace:
            args = json.dumps(inv.arguments, ensure_ascii=False)
            err  = " [ERROR]" if inv.is_error else ""
            print(f"  #{inv.iteration:>2}  {inv.server_id}.{inv.tool_name}{err}")
            if len(args) > 220:
                args = args[:217] + "..."
            print(f"        args: {args}")
            res = inv.result_text.replace("\n", " ⏎ ")
            if len(res) > 220:
                res = res[:217] + "..."
            print(f"        → {res}")
        print()
        print("─" * 78)
        print("FINAL REPLY:")
        print("─" * 78)
        print(result.reply)
        if result.truncated:
            print("\n[!] tool-loop был обрезан по лимиту max_iterations")

        servers_used = sorted({inv.server_id for inv in result.trace})
        print()
        print(f"задействовано серверов: {len(servers_used)} → {servers_used}")
        print(f"всего вызовов: {len(result.trace)}, итераций: {result.iterations}")
        return 0
    finally:
        registry.shutdown()


if __name__ == "__main__":
    sys.exit(main())
