#!/usr/bin/env python3
"""Демо файлового агента: цель → сам читает/ищет/пишет файлы проекта.

Собирает во временном каталоге мини-проект, поднимает встроенный
`LocalFilesystemClient` (тулы fs__list_dir/read_file/search/write_file) и
даёт агенту ЦЕЛЬ уровня «найди все использования api_client и задокументируй
их в ADR» — без указания «открой файл X». Дальше tool-loop сам:

  • ищет использование по нескольким файлам (fs__search),
  • читает найденное (fs__read_file),
  • генерирует новый файл docs/adr/… (fs__write_file) — с показом
    цветного diff (красный/зелёный) перед записью.

Запуск:
    python3 examples/fs_agent_demo.py          # реальный DeepSeek, если есть ключ
    JARVIS_DEMO_SCRIPTED=1 python3 examples/fs_agent_demo.py   # без ключа, детерминированно

Без DEEPSEEK_API_KEY автоматически включается скриптованный режим, поэтому
демо воспроизводимо повторно на любой машине.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.tool_router import ToolRouter  # noqa: E402
from cli.config import DEEPSEEK_CHAT_URL, load_env  # noqa: E402
from cli.fs_confirm import colorize_diff  # noqa: E402
from infra.deepseek_client import DeepSeekClient  # noqa: E402
from infra.local_fs_client import LocalFilesystemClient  # noqa: E402
from infra.mcp_registry import StdioMcpRegistry  # noqa: E402


GOAL = (
    "Ты — файловый агент над проектом. Используй ТОЛЬКО тулы fs__* (не выдумывай "
    "содержимое). Цель:\n"
    "1) Найди все места, где используется api_client (fs__search).\n"
    "2) Прочитай хотя бы один такой файл, чтобы понять, какие методы вызываются.\n"
    "3) Сгенерируй ADR-файл 'docs/adr/0001-api-client-usage.md' (fs__write_file) "
    "с заголовком, статусом Accepted, списком файлов-потребителей и методов.\n"
    "В финальном ответе кратко перечисли, что сделал."
)


class _EmptyRepo:
    def list_all(self): return []


def _sample_project(root: Path) -> None:
    (root / "app").mkdir(parents=True)
    (root / "app" / "orders.py").write_text(
        "from infra import api_client\n\n"
        "def place_order(o):\n    return api_client.post('/orders', o)\n",
        encoding="utf-8")
    (root / "app" / "catalog.py").write_text(
        "from infra import api_client\n\n"
        "def get_item(i):\n    return api_client.get(f'/items/{i}')\n",
        encoding="utf-8")
    (root / "README.md").write_text("# Shop\n", encoding="utf-8")


def _demo_confirm(rel: str, diff: str) -> bool:
    """Авто-подтверждение с показом цветного diff (как увидит пользователь в чате)."""
    print(f"\n  Агент пишет файл: {rel}")
    print(colorize_diff(diff))
    print("  → авто-подтверждено (demo)\n")
    return True


class _ScriptedLLM:
    """Детерминированная замена LLM: воспроизводит те же tool_calls, что сделал бы агент."""
    def __init__(self):
        self._script = [
            {"content": None, "tool_calls": [self._call("s1", "fs__search",
                                                        {"query": "api_client", "glob": "*.py"})]},
            {"content": None, "tool_calls": [self._call("s2", "fs__read_file",
                                                        {"path": "app/orders.py"})]},
            {"content": None, "tool_calls": [self._call("s3", "fs__write_file",
                {"path": "docs/adr/0001-api-client-usage.md",
                 "content": ("# ADR 0001: Использование api_client\n\n"
                             "## Status\nAccepted\n\n"
                             "## Context\n"
                             "`api_client` вызывается из нескольких модулей приложения.\n\n"
                             "## Consumers\n"
                             "- `app/orders.py` — `api_client.post`\n"
                             "- `app/catalog.py` — `api_client.get`\n\n"
                             "## Decision\n"
                             "Обращения к внешнему API централизуются через `api_client`.\n")})]},
            {"content": "Нашёл 2 потребителя api_client (orders.py, catalog.py) и создал ADR 0001."},
        ]

    @staticmethod
    def _call(cid, name, args):
        return {"id": cid, "type": "function",
                "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}

    def chat(self, messages, params, system_prompt=None):
        return (self._script.pop(0).get("content") or "")

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
            {"model": "deepseek-chat", "temperature": 0})


def main() -> int:
    load_env(os.path.join(ROOT, ".env"))
    with tempfile.TemporaryDirectory(prefix="jarvis-fs-demo-") as tmp:
        proj = Path(tmp)
        _sample_project(proj)

        registry = StdioMcpRegistry(_EmptyRepo())
        registry.start_all()
        registry.register(LocalFilesystemClient(root=str(proj), confirm=_demo_confirm))

        try:
            llm, params = _make_llm()
            router = ToolRouter(llm, registry, max_iterations=12)
            print("Тулы для агента:",
                  ", ".join(t.qualified_name for t in registry.all_tools()))
            print("\nЦЕЛЬ:\n" + GOAL + "\n")

            result = router.chat([{"role": "user", "content": GOAL}], params)

            print("─" * 60)
            print(f"TRACE ({len(result.trace)} вызов(ов)):")
            for inv in result.trace:
                print(f"  #{inv.iteration} {inv.server_id}.{inv.tool_name} "
                      f"{json.dumps(inv.arguments, ensure_ascii=False)[:80]}")
                print(f"     → {inv.result_text[:90].splitlines()[0] if inv.result_text else ''}")
            print("─" * 60)
            print("FINAL:", result.reply)

            adr = proj / "docs" / "adr" / "0001-api-client-usage.md"
            print("\nСоздан файл:", adr.name, "—", "OK" if adr.exists() else "НЕ создан")
            if adr.exists():
                print("─" * 60)
                print(adr.read_text(encoding="utf-8"))
            return 0
        finally:
            registry.shutdown()


if __name__ == "__main__":
    sys.exit(main())
