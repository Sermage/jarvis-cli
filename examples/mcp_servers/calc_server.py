#!/usr/bin/env python3
"""Игрушечный MCP-сервер «calc» — арифметика.

Без зависимостей: чистый stdin/stdout JSON-RPC, как требует MCP stdio.
Нужен, чтобы можно было прогнать длинный флоу с ToolRouter'ом без
интернета и npm-пакетов. Запускается из jarvis-cli как stdio-подпроцесс.

Тулы:
- add(a, b)      → a + b
- multiply(a, b) → a * b
- sqrt(x)        → корень из x

Каждый тул возвращает MCP content [{"type": "text", "text": "..."}].
"""
from __future__ import annotations

import json
import math
import sys


PROTOCOL_VERSION = "2024-11-05"

TOOLS = [
    {
        "name": "add",
        "description": "Сложить два числа",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["a", "b"],
        },
    },
    {
        "name": "multiply",
        "description": "Перемножить два числа",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["a", "b"],
        },
    },
    {
        "name": "sqrt",
        "description": "Квадратный корень из числа",
        "inputSchema": {
            "type": "object",
            "properties": {"x": {"type": "number"}},
            "required": ["x"],
        },
    },
]


def call_tool(name: str, arguments: dict) -> dict:
    try:
        if name == "add":
            value = float(arguments["a"]) + float(arguments["b"])
        elif name == "multiply":
            value = float(arguments["a"]) * float(arguments["b"])
        elif name == "sqrt":
            x = float(arguments["x"])
            if x < 0:
                return _error(f"sqrt не определён для отрицательных чисел ({x})")
            value = math.sqrt(x)
        else:
            return _error(f"неизвестный тул: {name}")
    except (KeyError, TypeError, ValueError) as e:
        return _error(f"невалидные аргументы для {name}: {e}")

    # Целые числа — без хвоста ".0", чтобы LLM было приятнее читать.
    text = str(int(value)) if value.is_integer() else str(value)
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _error(msg: str) -> dict:
    return {"content": [{"type": "text", "text": msg}], "isError": True}


def handle_request(msg: dict) -> "dict | None":
    """Вернуть response-dict, либо None если это нотификация."""
    method = msg.get("method")
    msg_id = msg.get("id")

    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "calc-demo", "version": "0.1.0"},
        }
    elif method == "notifications/initialized":
        return None
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = msg.get("params") or {}
        result = call_tool(params.get("name", ""), params.get("arguments") or {})
    elif msg_id is None:
        return None  # неизвестная нотификация — игнорируем
    else:
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": -32601, "message": f"method not found: {method}"}}

    if msg_id is None:
        return None
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def main() -> None:
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle_request(msg)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
