"""Интеграционный тест: реальные stdio-подпроцессы (examples/mcp_servers/*).

Поднимает оба демо-сервера через настоящий subprocess, проверяет, что
наш StdioMcpClient корректно делает хендшейк, list_tools и call_tool.
Это страховка от регрессов в JSON-RPC поверх stdio.
"""
from __future__ import annotations

import os
import sys

import pytest

from infra.mcp_stdio_client import StdioMcpClient


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CALC  = os.path.join(ROOT, "examples/mcp_servers/calc_server.py")
NOTES = os.path.join(ROOT, "examples/mcp_servers/notes_server.py")


@pytest.fixture()
def calc_client():
    client = StdioMcpClient(server_id="calc", command=sys.executable, args=[CALC])
    client.start()
    yield client
    client.close()


@pytest.fixture()
def notes_client():
    client = StdioMcpClient(server_id="notes", command=sys.executable, args=[NOTES])
    client.start()
    yield client
    client.close()


def test_calc_server_exposes_three_tools(calc_client):
    tools = calc_client.list_tools()
    assert {t.name for t in tools} == {"add", "multiply", "sqrt"}


def test_calc_add_returns_sum(calc_client):
    result = calc_client.call_tool("add", {"a": 7, "b": 35})
    assert result.is_error is False
    assert result.text == "42"


def test_calc_sqrt_negative_marked_as_error(calc_client):
    result = calc_client.call_tool("sqrt", {"x": -1})
    assert result.is_error is True
    assert "отрицательных" in result.text


def test_notes_save_then_read_roundtrip(notes_client):
    notes_client.call_tool("save_note", {"title": "foo", "body": "bar"})
    out = notes_client.call_tool("read_note", {"title": "foo"})
    assert out.text == "bar"


def test_notes_list_reflects_saves(notes_client):
    notes_client.call_tool("save_note", {"title": "a", "body": "x"})
    notes_client.call_tool("save_note", {"title": "b", "body": "y"})
    out = notes_client.call_tool("list_notes", {})
    assert set(out.text.splitlines()) == {"a", "b"}


def test_unknown_tool_returns_isError(calc_client):
    result = calc_client.call_tool("not_a_tool", {})
    assert result.is_error is True
