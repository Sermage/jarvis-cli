"""E2E tool-loop поверх реальных LocalFilesystemClient + StdioMcpRegistry.

LLM замокан скриптом, файловая система настоящая (tmp_path). Проверяем
сценарий из задания целиком: агент сам ищет использование API по нескольким
файлам, читает файл, затем обновляет документацию — и всё это одним
tool-loop'ом через ToolRouter, без ручного «открой файл X».
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from app.tool_router import ToolRouter
from infra.local_fs_client import LocalFilesystemClient
from infra.mcp_registry import StdioMcpRegistry


@dataclass
class FakeConfigRepo:
    """Пустой конфиг: внешних MCP-серверов нет, есть только встроенный fs."""
    def list_all(self):
        return []


@dataclass
class FakeLLM:
    script: list
    calls: list = field(default_factory=list)

    def chat(self, messages, params, system_prompt=None):
        return self._next().get("content") or ""

    def chat_with_tools(self, messages, params, tools, system_prompt=None):
        self.calls.append(tools)
        return self._next()

    def _next(self):
        assert self.script, "FakeLLM script exhausted"
        return self.script.pop(0)


def _call(cid, name, args):
    return {"id": cid, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


@pytest.fixture
def registry(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "a.py").write_text("api_client.get('/x')\n", encoding="utf-8")
    (tmp_path / "app" / "b.py").write_text("api_client.post('/y')\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Proj\nold docs\n", encoding="utf-8")
    reg = StdioMcpRegistry(FakeConfigRepo())
    reg.start_all()
    reg.register(LocalFilesystemClient(root=str(tmp_path), confirm=lambda r, d: True))
    return reg, tmp_path


def test_fs_tools_are_exposed_to_llm(registry):
    reg, _ = registry
    names = {t.qualified_name for t in reg.all_tools()}
    assert {"fs__search", "fs__read_file", "fs__write_file", "fs__list_dir"} <= names


def test_find_usages_then_update_docs(registry):
    reg, root = registry
    llm = FakeLLM(script=[
        # 1) агент сам решает поискать использования API
        {"content": None, "tool_calls": [_call("1", "fs__search",
                                               {"query": "api_client"})]},
        # 2) читает один из найденных файлов
        {"content": None, "tool_calls": [_call("2", "fs__read_file",
                                               {"path": "app/a.py"})]},
        # 3) обновляет документацию на основе найденного
        {"content": None, "tool_calls": [_call("3", "fs__write_file",
                                               {"path": "README.md",
                                                "content": "# Proj\napi_client: get, post\n"})]},
        {"content": "Готово: нашёл 2 использования, обновил README."},
    ])
    router = ToolRouter(llm, reg)

    result = router.chat(
        [{"role": "user", "content": "Найди использования api_client и обнови доки"}],
        {"model": "m"})

    # агент прошёл 3 шага сам
    assert result.iterations == 3
    steps = [(inv.tool_name) for inv in result.trace]
    assert steps == ["search", "read_file", "write_file"]
    # поиск реально нашёл оба файла
    assert "app/a.py:" in result.trace[0].result_text
    assert "app/b.py:" in result.trace[0].result_text
    # документация действительно изменена на диске (воспроизводимо)
    assert (root / "README.md").read_text() == "# Proj\napi_client: get, post\n"
    assert "обновил README" in result.reply
