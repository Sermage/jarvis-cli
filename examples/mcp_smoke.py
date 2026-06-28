#!/usr/bin/env python3
"""Smoke-проверка интеграции с MCP без LLM.

Поднимает два игрушечных stdio-сервера (calc + notes), достаёт список
тулов через StdioMcpRegistry, прогоняет несколько вызовов и печатает
результат. Если этот скрипт работает — значит, наш ToolRouter уже сможет
маршрутизировать настоящие запросы DeepSeek в эти серверы.

Запуск из корня проекта:
    python3 examples/mcp_smoke.py
"""
from __future__ import annotations

import os
import sys

# Чтобы импортировать пакеты jarvis-cli без установки.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.tool_router import ToolRouter  # noqa: E402
from domain.mcp import McpServerConfig  # noqa: E402
from infra.mcp_registry import StdioMcpRegistry  # noqa: E402


class _InMemoryRepo:
    def __init__(self, items):
        self._items = items

    def list_all(self):
        return list(self._items)

    def get(self, server_id):
        for c in self._items:
            if c.server_id == server_id:
                return c
        return None

    def save(self, cfg): pass
    def delete(self, server_id): pass
    def set_enabled(self, server_id, enabled): pass


def main() -> int:
    calc = McpServerConfig(
        server_id="calc",
        command=sys.executable,
        args=(os.path.join(ROOT, "examples/mcp_servers/calc_server.py"),),
    )
    notes = McpServerConfig(
        server_id="notes",
        command=sys.executable,
        args=(os.path.join(ROOT, "examples/mcp_servers/notes_server.py"),),
    )
    registry = StdioMcpRegistry(_InMemoryRepo([calc, notes]))
    registry.start_all()

    try:
        clients = registry.clients()
        print(f"запущено серверов: {len(clients)}")
        for c in clients:
            print(f"  ● {c.server_id}")

        tools = registry.all_tools()
        print(f"\nвсего тулов: {len(tools)}")
        for t in tools:
            print(f"  {t.server_id}.{t.name}  ({t.qualified_name})")

        print("\n── прямые вызовы (минуя LLM) ──")
        calc_client  = registry.get("calc")
        notes_client = registry.get("notes")
        print("calc.add(7, 35)        →", calc_client.call_tool("add", {"a": 7, "b": 35}).text)
        print("calc.multiply(6, 9)    →", calc_client.call_tool("multiply", {"a": 6, "b": 9}).text)
        print("calc.sqrt(2)           →", calc_client.call_tool("sqrt", {"x": 2}).text)
        print("notes.save_note(...)   →",
              notes_client.call_tool("save_note", {"title": "answer", "body": "42"}).text)
        print("notes.list_notes()     →", notes_client.call_tool("list_notes", {}).text)
        print("notes.read_note(...)   →",
              notes_client.call_tool("read_note", {"title": "answer"}).text)

        print("\n── ToolRouter c fake-LLM ──")
        # Имитируем LLM, который сам выбирает тулы. Это «контракт» того,
        # что произойдёт с настоящим DeepSeek, только без сети.
        import json as _json

        class _ScriptedLLM:
            def __init__(self, script):
                self.script = script
            def chat(self, *a, **kw): raise AssertionError("должен быть chat_with_tools")
            def chat_with_tools(self, messages, params, tools, system_prompt=None):
                return self.script.pop(0)

        def tc(call_id, name, args):
            return {"id": call_id, "type": "function",
                    "function": {"name": name, "arguments": _json.dumps(args)}}

        # Длинный флоу: сложить 7+35, потом возвести в куб (через multiply дважды),
        # сохранить, прочитать.
        llm = _ScriptedLLM(script=[
            {"content": None, "tool_calls": [tc("1", "calc__add", {"a": 7, "b": 35})]},
            {"content": None, "tool_calls": [tc("2", "calc__multiply", {"a": 42, "b": 42})]},
            {"content": None, "tool_calls": [tc("3", "calc__multiply", {"a": 1764, "b": 42})]},
            {"content": None, "tool_calls": [tc("4", "notes__save_note",
                                                {"title": "cube", "body": "74088"})]},
            {"content": None, "tool_calls": [tc("5", "notes__read_note", {"title": "cube"})]},
            {"content": "42^3 = 74088, сохранил под именем 'cube'."},
        ])
        router = ToolRouter(llm, registry)
        result = router.chat([{"role": "user", "content": "посчитай и сохрани"}],
                             {"model": "test"})
        for inv in result.trace:
            print(f"  iter {inv.iteration}: "
                  f"{inv.server_id}.{inv.tool_name}({inv.arguments}) → {inv.result_text}")
        print(f"\nфинальный ответ агента: {result.reply}")
        print(f"итераций: {result.iterations}, truncated: {result.truncated}")
        return 0
    finally:
        registry.shutdown()


if __name__ == "__main__":
    sys.exit(main())
