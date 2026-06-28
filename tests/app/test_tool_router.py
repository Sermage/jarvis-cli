"""Тесты ToolRouter — главного оркестратора tool-loop'а.

Фейки изолируют LLM и MCP, чтобы убедиться:
- агент действительно делает несколько раундов tool_calls подряд (длинный флоу);
- каждый вызов уходит на правильный сервер (маршрутизация по префиксу);
- порядок вызовов сохраняется и виден в trace;
- лимит итераций срабатывает.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from app.tool_router import ToolRouter
from domain.mcp import McpTool, ToolResult


# ── фейки ────────────────────────────────────────────────────────────────────


@dataclass
class FakeMcpClient:
    server_id: str
    tools: list = field(default_factory=list)
    responses: dict = field(default_factory=dict)
    calls: list = field(default_factory=list)
    started: bool = False
    closed: bool = False

    def start(self):
        self.started = True

    def list_tools(self):
        return list(self.tools)

    def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        result = self.responses.get(name)
        if isinstance(result, Exception):
            raise result
        if isinstance(result, ToolResult):
            return result
        return ToolResult(text=str(result) if result is not None else "ok")

    def close(self):
        self.closed = True


@dataclass
class FakeRegistry:
    by_id: dict

    def clients(self):
        return list(self.by_id.values())

    def get(self, server_id):
        return self.by_id.get(server_id)

    def all_tools(self):
        out = []
        for c in self.by_id.values():
            out.extend(c.list_tools())
        return out

    def shutdown(self):
        for c in self.by_id.values():
            c.close()


@dataclass
class FakeLLM:
    """Очередь скриптованных ответов: каждый вызов отдаёт следующий."""
    script: list
    calls: list = field(default_factory=list)

    def chat(self, messages, params, system_prompt=None):
        return self._next(messages, params, system_prompt).get("content") or ""

    def chat_with_tools(self, messages, params, tools, system_prompt=None):
        return self._next(messages, params, system_prompt, tools=tools)

    def _next(self, messages, params, system_prompt, tools=None):
        self.calls.append({
            "messages":      [m for m in messages],
            "tools":         tools,
            "system_prompt": system_prompt,
        })
        if not self.script:
            raise AssertionError("FakeLLM script exhausted")
        return self.script.pop(0)


def _tool(server_id, name, description="", schema=None):
    return McpTool(
        server_id=server_id, name=name, description=description,
        input_schema=schema or {"type": "object", "properties": {}},
    )


def _tool_call(call_id, qualified_name, arguments):
    return {
        "id":   call_id,
        "type": "function",
        "function": {
            "name":      qualified_name,
            "arguments": json.dumps(arguments),
        },
    }


# ── тесты ─────────────────────────────────────────────────────────────────────


def test_passes_through_when_no_tools_available():
    """Если в реестре нет тулов, ToolRouter не делает tool-loop."""
    registry = FakeRegistry(by_id={})
    llm = FakeLLM(script=[{"content": "просто текст"}])
    router = ToolRouter(llm, registry)

    result = router.chat([{"role": "user", "content": "?"}], {"model": "m"})
    assert result.reply == "просто текст"
    assert result.iterations == 0
    assert result.trace == []


def test_single_tool_call_then_final_answer():
    """Базовый сценарий: LLM делает один tool_call, потом отвечает."""
    fs = FakeMcpClient(
        server_id="fs",
        tools=[_tool("fs", "read_file")],
        responses={"read_file": "содержимое"},
    )
    registry = FakeRegistry(by_id={"fs": fs})
    llm = FakeLLM(script=[
        {"content": None, "tool_calls": [_tool_call("c1", "fs__read_file", {"path": "/x"})]},
        {"content": "вот файл: содержимое"},
    ])
    router = ToolRouter(llm, registry)

    result = router.chat([{"role": "user", "content": "прочти /x"}], {"model": "m"})

    assert result.reply == "вот файл: содержимое"
    assert result.iterations == 1
    assert len(result.trace) == 1
    inv = result.trace[0]
    assert inv.server_id == "fs"
    assert inv.tool_name == "read_file"
    assert inv.arguments == {"path": "/x"}
    assert not inv.is_error
    assert fs.calls == [("read_file", {"path": "/x"})]


def test_routes_calls_to_correct_servers_by_prefix():
    """Длинный флоу с пересечением границ серверов: маршрутизация по префиксу."""
    fetch = FakeMcpClient(server_id="fetch",
                          tools=[_tool("fetch", "fetch")],
                          responses={"fetch": "<title>Hello</title>"})
    fs    = FakeMcpClient(server_id="fs",
                          tools=[_tool("fs", "write_file")],
                          responses={"write_file": "wrote"})
    db    = FakeMcpClient(server_id="db",
                          tools=[_tool("db", "execute"), _tool("db", "query")],
                          responses={"execute": "1 row inserted", "query": "[{id:1, title:'Hello'}]"})
    registry = FakeRegistry(by_id={"fetch": fetch, "fs": fs, "db": db})

    llm = FakeLLM(script=[
        {"content": None, "tool_calls": [_tool_call("a", "fetch__fetch",
                                                    {"url": "https://ex.com"})]},
        {"content": None, "tool_calls": [_tool_call("b", "fs__write_file",
                                                    {"path": "/tmp/t.json",
                                                     "content": '{"title":"Hello"}'})]},
        {"content": None, "tool_calls": [_tool_call("c", "db__execute",
                                                    {"sql": "INSERT ..."})]},
        {"content": None, "tool_calls": [_tool_call("d", "db__query",
                                                    {"sql": "SELECT ..."})]},
        {"content": "готово, последние строки: …"},
    ])
    router = ToolRouter(llm, registry)

    result = router.chat([{"role": "user", "content": "сделай длинный флоу"}],
                         {"model": "m"})

    # 4 вызова на 3 разных сервера, порядок сохранён, текст финален.
    assert [(i.server_id, i.tool_name) for i in result.trace] == [
        ("fetch", "fetch"),
        ("fs",    "write_file"),
        ("db",    "execute"),
        ("db",    "query"),
    ]
    assert result.iterations == 4
    assert result.reply.startswith("готово")
    # Каждый клиент получил ровно свои вызовы и ничего лишнего:
    assert fetch.calls == [("fetch",      {"url": "https://ex.com"})]
    assert fs.calls    == [("write_file", {"path": "/tmp/t.json",
                                           "content": '{"title":"Hello"}'})]
    assert db.calls    == [("execute", {"sql": "INSERT ..."}),
                           ("query",   {"sql": "SELECT ..."})]


def test_history_gets_tool_messages_appended_between_iterations():
    """В истории между chat_with_tools-вызовами должны появиться tool-сообщения,
    иначе модель не увидит результаты предыдущих тулов."""
    fs = FakeMcpClient(server_id="fs", tools=[_tool("fs", "ls")], responses={"ls": "a b c"})
    registry = FakeRegistry(by_id={"fs": fs})
    llm = FakeLLM(script=[
        {"content": None, "tool_calls": [_tool_call("x", "fs__ls", {})]},
        {"content": "вижу: a b c"},
    ])
    router = ToolRouter(llm, registry)

    router.chat([{"role": "user", "content": "что в папке?"}], {"model": "m"})

    # Второй вызов модели уже содержит и assistant-tool_calls, и tool-результат.
    second_call_messages = llm.calls[1]["messages"]
    roles = [m["role"] for m in second_call_messages]
    assert roles == ["user", "assistant", "tool"]
    assert second_call_messages[-1]["tool_call_id"] == "x"
    assert second_call_messages[-1]["content"] == "a b c"


def test_unknown_server_does_not_crash_loop():
    """Если LLM выдумала имя сервера — пишем ошибку в tool-message,
    но цикл продолжается (модель сможет извиниться или попробовать другой тул)."""
    fs = FakeMcpClient(server_id="fs", tools=[_tool("fs", "ls")], responses={"ls": "ok"})
    registry = FakeRegistry(by_id={"fs": fs})
    llm = FakeLLM(script=[
        {"content": None, "tool_calls": [_tool_call("y", "ghost__phantom", {})]},
        {"content": "понял, сервера ghost нет"},
    ])
    router = ToolRouter(llm, registry)

    result = router.chat([{"role": "user", "content": "?"}], {"model": "m"})

    assert result.trace[0].is_error
    assert "ghost" in result.trace[0].result_text
    assert result.reply == "понял, сервера ghost нет"


def test_tool_call_exception_is_captured_not_raised():
    """Падение call_tool — это is_error, а не исключение наружу."""
    bad = FakeMcpClient(server_id="bad", tools=[_tool("bad", "boom")],
                        responses={"boom": RuntimeError("server died")})
    registry = FakeRegistry(by_id={"bad": bad})
    llm = FakeLLM(script=[
        {"content": None, "tool_calls": [_tool_call("z", "bad__boom", {})]},
        {"content": "обработал ошибку"},
    ])
    router = ToolRouter(llm, registry)
    result = router.chat([{"role": "user", "content": "?"}], {"model": "m"})
    assert result.trace[0].is_error
    assert "server died" in result.trace[0].result_text


def test_max_iterations_trunc_returns_truncated_flag():
    """Если модель упорно крутит тулы — обрываем после max_iterations."""
    fs = FakeMcpClient(server_id="fs", tools=[_tool("fs", "ls")], responses={"ls": "x"})
    registry = FakeRegistry(by_id={"fs": fs})
    # Бесконечный скрипт: каждый ответ — снова tool_call.
    forever = [{"content": None, "tool_calls": [_tool_call(f"i{i}", "fs__ls", {})]}
               for i in range(10)]
    llm = FakeLLM(script=forever)
    router = ToolRouter(llm, registry, max_iterations=3)

    result = router.chat([{"role": "user", "content": "loop"}], {"model": "m"})
    assert result.truncated
    assert result.iterations == 3
    assert len(result.trace) == 3


def test_on_event_streams_thinking_and_tool_events_in_order():
    """Репортер видит события в правильном порядке: думаю → tool_start → tool_end → ...
    Это контракт, на котором держится live-прогресс в CLI."""
    fs = FakeMcpClient(server_id="fs", tools=[_tool("fs", "ls")], responses={"ls": "a b"})
    registry = FakeRegistry(by_id={"fs": fs})
    llm = FakeLLM(script=[
        {"content": None, "tool_calls": [_tool_call("c1", "fs__ls", {"path": "/x"})]},
        {"content": "готово"},
    ])
    router = ToolRouter(llm, registry)
    events = []
    router.chat([{"role": "user", "content": "?"}], {"model": "m"},
                on_event=lambda ev: events.append(ev))

    types = [e["type"] for e in events]
    # 2 итерации = 2 thinking_start + 2 thinking_end + 1 tool_start + 1 tool_end
    assert types == [
        "thinking_start", "thinking_end",
        "tool_start", "tool_end",
        "thinking_start", "thinking_end",
    ]
    # tool_start содержит распарсенные аргументы и server/tool до диспетча.
    ts = next(e for e in events if e["type"] == "tool_start")
    assert ts["server_id"] == "fs"
    assert ts["tool_name"] == "ls"
    assert ts["arguments"] == {"path": "/x"}
    # tool_end несёт ту же ToolInvocation, что и в trace.
    te = next(e for e in events if e["type"] == "tool_end")
    assert te["invocation"].server_id == "fs"
    assert te["invocation"].result_text == "a b"


def test_on_event_works_when_no_tools():
    """Даже без тулов — события think_start/think_end должны прийти,
    чтобы CLI знал, что показывать спиннер."""
    registry = FakeRegistry(by_id={})
    llm = FakeLLM(script=[{"content": "ответ"}])
    router = ToolRouter(llm, registry)
    events = []
    router.chat([{"role": "user", "content": "?"}], {"model": "m"},
                on_event=lambda ev: events.append(ev))
    assert [e["type"] for e in events] == ["thinking_start", "thinking_end"]


def test_on_event_exception_does_not_break_loop():
    """Сбой в репортере не должен ронять весь чат."""
    fs = FakeMcpClient(server_id="fs", tools=[_tool("fs", "x")], responses={"x": "ok"})
    registry = FakeRegistry(by_id={"fs": fs})
    llm = FakeLLM(script=[
        {"content": None, "tool_calls": [_tool_call("c", "fs__x", {})]},
        {"content": "done"},
    ])
    def buggy(ev): raise RuntimeError("ui exploded")
    router = ToolRouter(llm, registry)
    # не должно бросить
    result = router.chat([{"role": "user", "content": "?"}], {"model": "m"}, on_event=buggy)
    assert result.reply == "done"
    assert len(result.trace) == 1


def test_collect_openai_tools_uses_qualified_names_and_schemas():
    """Имена тулов уходят в LLM как server__tool, схемы — как parameters."""
    schema = {"type": "object",
              "properties": {"path": {"type": "string"}},
              "required": ["path"]}
    fs = FakeMcpClient(server_id="fs",
                       tools=[_tool("fs", "read_file", "Read text file", schema)])
    registry = FakeRegistry(by_id={"fs": fs})
    router = ToolRouter(FakeLLM(script=[]), registry)
    tools = router.collect_openai_tools()
    assert tools == [{
        "type": "function",
        "function": {
            "name":        "fs__read_file",
            "description": "Read text file",
            "parameters":  schema,
        },
    }]


def test_malformed_arguments_json_produces_error_invocation():
    """LLM иногда шлёт не-JSON в arguments — не должно ронять loop."""
    fs = FakeMcpClient(server_id="fs", tools=[_tool("fs", "ls")])
    registry = FakeRegistry(by_id={"fs": fs})
    llm = FakeLLM(script=[
        {"content": None, "tool_calls": [{
            "id": "k", "type": "function",
            "function": {"name": "fs__ls", "arguments": "not-json"}
        }]},
        {"content": "переделал"},
    ])
    router = ToolRouter(llm, registry)
    result = router.chat([{"role": "user", "content": "?"}], {"model": "m"})
    assert result.trace[0].is_error
    assert "JSON" in result.trace[0].result_text
    # call_tool НЕ должен был быть вызван при сломанных аргументах.
    assert fs.calls == []
