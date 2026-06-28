"""Тесты StdioMcpClient с подменённым Popen — без реального подпроцесса.

Проверяем JSON-RPC поверх stdio: хендшейк initialize → notifications/initialized,
запросы tools/list и tools/call, парсинг content-блоков.
"""
from __future__ import annotations

import io
import json
import threading

import pytest

from infra.mcp_stdio_client import StdioMcpClient, _flatten_mcp_content


class _FakeProc:
    """Минимальная имитация subprocess.Popen для стIO-клиента.

    stdin принимает строки (как text-mode pipe), мы их парсим как JSON-RPC
    и подкладываем ответы в очередь stdout. Чтобы тесты были детерминированы,
    `readline` блокирующий, но он сразу видит результат, потому что мы пишем
    в очередь до того, как клиент его попросит.
    """
    def __init__(self):
        self.stdin  = _FakePipe()
        self.stdout = _FakeStdout()
        self.stderr = io.StringIO("")  # пустой stderr — итерация сразу закончится
        self._handlers = {}
        self._initialized = False
        self.terminated = False
        self.killed     = False

    def register(self, method, handler):
        self._handlers[method] = handler

    def _process_outgoing(self, line: str):
        msg = json.loads(line)
        method = msg.get("method")
        if method is None:
            return  # ответ — не наш случай
        # Нотификация — без id, без ответа.
        if "id" not in msg:
            if method == "notifications/initialized":
                self._initialized = True
            return
        # Запрос — синтезируем ответ.
        msg_id = msg["id"]
        if method in self._handlers:
            result = self._handlers[method](msg.get("params") or {})
            response = {"jsonrpc": "2.0", "id": msg_id, "result": result}
        else:
            response = {"jsonrpc": "2.0", "id": msg_id,
                        "error": {"code": -32601, "message": f"no handler for {method}"}}
        self.stdout.feed(json.dumps(response) + "\n")

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


class _FakePipe:
    """stdin — пишем сюда; всё уходит в _FakeProc._process_outgoing."""
    def __init__(self):
        self._proc = None
        self.closed = False
        self._buffer = ""

    def attach(self, proc):
        self._proc = proc

    def write(self, data: str):
        self._buffer += data
        while "\n" in self._buffer:
            line, _, rest = self._buffer.partition("\n")
            self._buffer = rest
            if line and self._proc:
                self._proc._process_outgoing(line)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class _FakeStdout:
    """stdout — очередь строк, readline блокирует до появления новой."""
    def __init__(self):
        self._lines = []
        self._cv = threading.Condition()

    def feed(self, s: str):
        with self._cv:
            self._lines.append(s)
            self._cv.notify_all()

    def readline(self):
        with self._cv:
            while not self._lines:
                if not self._cv.wait(timeout=5):
                    return ""
            return self._lines.pop(0)


def _popen_factory(proc: _FakeProc):
    """Подсовываем StdioMcpClient'у уже сконструированный _FakeProc."""
    def _popen(*args, **kwargs):
        proc.stdin.attach(proc)
        return proc
    return _popen


# ── тесты ─────────────────────────────────────────────────────────────────────


def test_start_does_initialize_handshake_and_sends_initialized_notification():
    proc = _FakeProc()
    init_seen = {}
    proc.register("initialize", lambda params: (
        init_seen.update(params),
        {"protocolVersion": "2024-11-05",
         "capabilities": {"tools": {}},
         "serverInfo": {"name": "fake", "version": "0"}},
    )[1])

    client = StdioMcpClient(server_id="fake", command="x", popen=_popen_factory(proc))
    client.start()

    assert init_seen["protocolVersion"] == "2024-11-05"
    assert init_seen["clientInfo"]["name"] == "jarvis-cli"
    assert proc._initialized, "клиент должен прислать notifications/initialized"
    client.close()


def test_list_tools_returns_parsed_tools():
    proc = _FakeProc()
    proc.register("initialize", lambda p: {"protocolVersion": "2024-11-05",
                                            "capabilities": {}, "serverInfo": {}})
    proc.register("tools/list", lambda p: {"tools": [
        {"name": "read_file",
         "description": "Read a file",
         "inputSchema": {"type": "object",
                         "properties": {"path": {"type": "string"}}}},
        {"name": "list_dir"},  # без описания и схемы — тоже должен подняться
    ]})

    client = StdioMcpClient(server_id="fs", command="x", popen=_popen_factory(proc))
    client.start()
    tools = client.list_tools()
    assert [t.name for t in tools] == ["read_file", "list_dir"]
    assert tools[0].input_schema["properties"]["path"]["type"] == "string"
    assert tools[1].input_schema == {"type": "object", "properties": {}}
    # Повторный вызов берёт из кэша — сервер второй раз не дёргается.
    assert client.list_tools() == tools
    client.close()


def test_call_tool_flattens_text_content_blocks():
    proc = _FakeProc()
    proc.register("initialize", lambda p: {})
    proc.register("tools/call", lambda p: {
        "content": [
            {"type": "text", "text": "первая строка"},
            {"type": "text", "text": "вторая"},
        ],
        "isError": False,
    })
    client = StdioMcpClient(server_id="x", command="x", popen=_popen_factory(proc))
    client.start()
    result = client.call_tool("any", {"k": "v"})
    assert result.is_error is False
    assert result.text == "первая строка\nвторая"
    client.close()


def test_call_tool_propagates_is_error_flag():
    proc = _FakeProc()
    proc.register("initialize", lambda p: {})
    proc.register("tools/call", lambda p: {
        "content": [{"type": "text", "text": "boom"}],
        "isError": True,
    })
    client = StdioMcpClient(server_id="x", command="x", popen=_popen_factory(proc))
    client.start()
    result = client.call_tool("fail", {})
    assert result.is_error is True
    assert "boom" in result.text
    client.close()


def test_request_raises_on_jsonrpc_error():
    proc = _FakeProc()
    proc.register("initialize", lambda p: {})
    # tools/list не зарегистрирован — _FakeProc вернёт error -32601.
    client = StdioMcpClient(server_id="x", command="x", popen=_popen_factory(proc))
    client.start()
    with pytest.raises(RuntimeError) as exc:
        client.list_tools()
    assert "tools/list" in str(exc.value)
    client.close()


def test_close_terminates_subprocess():
    proc = _FakeProc()
    proc.register("initialize", lambda p: {})
    client = StdioMcpClient(server_id="x", command="x", popen=_popen_factory(proc))
    client.start()
    client.close()
    assert proc.stdin.closed
    assert proc.terminated


def test_flatten_mcp_content_handles_non_text_blocks():
    out = _flatten_mcp_content([
        {"type": "text", "text": "hi"},
        {"type": "image", "data": "AAA", "mimeType": "image/png"},
    ])
    assert out.startswith("hi\n")
    assert "image/png" in out
