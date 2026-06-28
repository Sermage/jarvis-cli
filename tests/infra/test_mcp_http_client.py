"""Тесты HttpMcpClient с фейковым requests.post — без реальной сети.

Streamable HTTP допускает два варианта ответа на POST:
- application/json — одно JSON-RPC сообщение;
- text/event-stream — SSE-стрим из одного или нескольких сообщений.
Тесты покрывают обе ветки + session-id, ошибки, нотификации.
"""
from __future__ import annotations

import json

import pytest
import requests

from infra.mcp_http_client import HttpMcpClient


class _FakeResp:
    def __init__(self, *, status=200, headers=None, json_body=None, sse_lines=None,
                 raise_for_status_exc=None):
        self.status_code = status
        self.headers = headers or {}
        self._json = json_body
        self._sse  = sse_lines or []
        self._raise = raise_for_status_exc
        self.text = json.dumps(json_body) if json_body is not None else ""
        self.closed = False

    def raise_for_status(self):
        if self._raise:
            raise self._raise

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def iter_lines(self, decode_unicode=False):
        for line in self._sse:
            yield line

    def close(self):
        self.closed = True


class _FakePost:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, url, headers=None, json=None, timeout=None, stream=None):
        self.calls.append({"url": url, "headers": headers or {}, "json": json,
                           "timeout": timeout, "stream": stream})
        if not self._responses:
            raise AssertionError(f"Unexpected POST to {url}: {json}")
        nxt = self._responses.pop(0)
        return nxt(json) if callable(nxt) else nxt


def _json_resp(body, sid=None, status=200):
    headers = {"Content-Type": "application/json"}
    if sid:
        headers["Mcp-Session-Id"] = sid
    return _FakeResp(status=status, headers=headers, json_body=body)


def _sse_resp(messages, sid=None):
    # SSE-формат: `data: <json>\n\n` для каждого сообщения.
    lines: list = []
    for m in messages:
        lines.append("data: " + json.dumps(m))
        lines.append("")  # пустая строка завершает событие
    headers = {"Content-Type": "text/event-stream"}
    if sid:
        headers["Mcp-Session-Id"] = sid
    return _FakeResp(status=200, headers=headers, sse_lines=lines)


def _client(post, **kw):
    return HttpMcpClient(
        server_id="x",
        url="https://srv.example/mcp",
        headers={"Authorization": "Bearer secret"},
        http_post=post,
        **kw,
    )


# ── handshake ─────────────────────────────────────────────────────────────────


def test_start_sends_initialize_and_initialized_notification():
    # responses: 1) initialize → json result; 2) notifications/initialized → 202
    fake = _FakePost([
        _json_resp({"jsonrpc": "2.0", "id": 1, "result": {
            "protocolVersion": "2024-11-05", "capabilities": {}, "serverInfo": {}
        }}, sid="sess-1"),
        _FakeResp(status=202, headers={}),
    ])
    client = _client(fake)
    client.start()
    assert len(fake.calls) == 2
    # Первый запрос — initialize, с Bearer-токеном из user-headers.
    assert fake.calls[0]["json"]["method"] == "initialize"
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer secret"
    # После initialize клиент должен сохранить Mcp-Session-Id из ответа
    # и пробросить его в следующий запрос (notifications/initialized).
    assert fake.calls[1]["json"]["method"] == "notifications/initialized"
    assert fake.calls[1]["headers"]["Mcp-Session-Id"] == "sess-1"


def test_session_id_propagated_to_subsequent_requests():
    fake = _FakePost([
        _json_resp({"jsonrpc": "2.0", "id": 1, "result": {}}, sid="sess-XYZ"),
        _FakeResp(status=202, headers={}),  # initialized notification
        _json_resp({"jsonrpc": "2.0", "id": 2, "result": {"tools": []}}),
    ])
    client = _client(fake)
    client.start()
    client.list_tools()
    assert fake.calls[2]["headers"]["Mcp-Session-Id"] == "sess-XYZ"


# ── list_tools ────────────────────────────────────────────────────────────────


def test_list_tools_parses_json_response():
    fake = _FakePost([
        _json_resp({"jsonrpc": "2.0", "id": 1, "result": {}}),
        _FakeResp(status=202, headers={}),
        _json_resp({"jsonrpc": "2.0", "id": 2, "result": {"tools": [
            {"name": "get_price",
             "description": "fetch a price",
             "inputSchema": {"type": "object",
                             "properties": {"ticker": {"type": "string"}}}},
        ]}}),
    ])
    client = _client(fake)
    client.start()
    tools = client.list_tools()
    assert [t.name for t in tools] == ["get_price"]
    assert tools[0].server_id == "x"
    assert tools[0].input_schema["properties"]["ticker"]["type"] == "string"


def test_list_tools_cached_between_calls():
    fake = _FakePost([
        _json_resp({"jsonrpc": "2.0", "id": 1, "result": {}}),
        _FakeResp(status=202, headers={}),
        _json_resp({"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "x"}]}}),
    ])
    client = _client(fake)
    client.start()
    a = client.list_tools()
    b = client.list_tools()  # без второго HTTP-запроса
    assert a is b
    # Дополнительных POST не было.
    assert len(fake.calls) == 3


# ── call_tool ─────────────────────────────────────────────────────────────────


def test_call_tool_returns_flattened_text_from_json_response():
    fake = _FakePost([
        _json_resp({"jsonrpc": "2.0", "id": 1, "result": {}}),
        _FakeResp(status=202, headers={}),
        _json_resp({"jsonrpc": "2.0", "id": 2, "result": {
            "content": [
                {"type": "text", "text": "линия 1"},
                {"type": "text", "text": "линия 2"},
            ],
            "isError": False,
        }}),
    ])
    client = _client(fake)
    client.start()
    out = client.call_tool("doit", {"a": 1})
    assert out.is_error is False
    assert out.text == "линия 1\nлиния 2"
    # Тело tools/call содержит правильный method и arguments.
    last = fake.calls[-1]["json"]
    assert last["method"] == "tools/call"
    assert last["params"] == {"name": "doit", "arguments": {"a": 1}}


def test_call_tool_propagates_is_error_flag():
    fake = _FakePost([
        _json_resp({"jsonrpc": "2.0", "id": 1, "result": {}}),
        _FakeResp(status=202, headers={}),
        _json_resp({"jsonrpc": "2.0", "id": 2, "result": {
            "content": [{"type": "text", "text": "boom"}], "isError": True,
        }}),
    ])
    client = _client(fake)
    client.start()
    out = client.call_tool("fail", {})
    assert out.is_error is True
    assert "boom" in out.text


# ── SSE-ветка ─────────────────────────────────────────────────────────────────


def test_call_tool_reads_response_from_sse_stream():
    fake = _FakePost([
        _json_resp({"jsonrpc": "2.0", "id": 1, "result": {}}),
        _FakeResp(status=202, headers={}),
        _sse_resp([
            {"jsonrpc": "2.0", "id": 2, "result": {
                "content": [{"type": "text", "text": "ok-sse"}], "isError": False,
            }},
        ]),
    ])
    client = _client(fake)
    client.start()
    out = client.call_tool("do", {})
    assert out.text == "ok-sse"


def test_sse_skips_messages_with_different_ids():
    """Сервер может прислать промежуточную нотификацию в стриме."""
    fake = _FakePost([
        _json_resp({"jsonrpc": "2.0", "id": 1, "result": {}}),
        _FakeResp(status=202, headers={}),
        _sse_resp([
            {"jsonrpc": "2.0", "method": "notifications/progress", "params": {"pct": 50}},
            {"jsonrpc": "2.0", "id": 2, "result": {
                "content": [{"type": "text", "text": "final"}],
            }},
        ]),
    ])
    client = _client(fake)
    client.start()
    out = client.call_tool("do", {})
    assert out.text == "final"


def test_sse_raises_when_stream_closes_without_response():
    fake = _FakePost([
        _json_resp({"jsonrpc": "2.0", "id": 1, "result": {}}),
        _FakeResp(status=202, headers={}),
        _sse_resp([{"jsonrpc": "2.0", "method": "tick", "params": {}}]),  # без ответа
    ])
    client = _client(fake)
    client.start()
    with pytest.raises(RuntimeError) as exc:
        client.list_tools()
    assert "SSE" in str(exc.value)


# ── ошибки ────────────────────────────────────────────────────────────────────


def test_jsonrpc_error_is_raised_as_runtime_error():
    fake = _FakePost([
        _json_resp({"jsonrpc": "2.0", "id": 1, "result": {}}),
        _FakeResp(status=202, headers={}),
        _json_resp({"jsonrpc": "2.0", "id": 2,
                    "error": {"code": -32601, "message": "method not found"}}),
    ])
    client = _client(fake)
    client.start()
    with pytest.raises(RuntimeError) as exc:
        client.list_tools()
    assert "method not found" in str(exc.value)
    assert "tools/list" in str(exc.value)


def test_http_error_propagates():
    fake = _FakePost([
        _FakeResp(status=500, headers={},
                  raise_for_status_exc=requests.HTTPError("500")),
    ])
    client = _client(fake)
    with pytest.raises(requests.HTTPError):
        client.start()


def test_close_resets_session_so_next_start_can_handshake_again():
    fake = _FakePost([
        _json_resp({"jsonrpc": "2.0", "id": 1, "result": {}}, sid="s1"),
        _FakeResp(status=202, headers={}),
        # после close → следующий start снова делает initialize
        _json_resp({"jsonrpc": "2.0", "id": 2, "result": {}}, sid="s2"),
        _FakeResp(status=202, headers={}),
    ])
    client = _client(fake)
    client.start()
    client.close()
    client.start()
    assert fake.calls[2]["json"]["method"] == "initialize"
    # Старый session-id уже не прикладывается к новому initialize:
    assert "Mcp-Session-Id" not in fake.calls[2]["headers"]
