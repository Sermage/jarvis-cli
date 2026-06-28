#!/usr/bin/env python3
"""Игрушечный MCP-сервер «notes» — заметки в памяти.

Парный к calc_server.py, нужен чтобы продемонстрировать маршрутизацию
ToolRouter'а между разными серверами (calc.* и notes.*) в одном
длинном tool-loop.

Тулы:
- save_note(title, body)  → сохранить
- list_notes()            → перечислить заголовки
- read_note(title)        → прочитать тело
- delete_note(title)      → удалить

Хранилище — обычный dict в памяти процесса. Жизненный цикл = время
работы подпроцесса (на длину tool-loop этого хватит).
"""
from __future__ import annotations

import json
import sys


PROTOCOL_VERSION = "2024-11-05"
NOTES: dict = {}

TOOLS = [
    {
        "name": "save_note",
        "description": "Сохранить заметку с указанным заголовком (перезапишет, если есть)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "body":  {"type": "string"},
            },
            "required": ["title", "body"],
        },
    },
    {
        "name": "list_notes",
        "description": "Список всех заголовков сохранённых заметок",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "read_note",
        "description": "Прочитать тело заметки по заголовку",
        "inputSchema": {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        },
    },
    {
        "name": "delete_note",
        "description": "Удалить заметку",
        "inputSchema": {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        },
    },
]


def call_tool(name: str, arguments: dict) -> dict:
    try:
        if name == "save_note":
            title = arguments["title"]
            NOTES[title] = arguments["body"]
            return _ok(f"saved {title!r} ({len(arguments['body'])} chars)")
        if name == "list_notes":
            if not NOTES:
                return _ok("(no notes yet)")
            return _ok("\n".join(sorted(NOTES.keys())))
        if name == "read_note":
            title = arguments["title"]
            if title not in NOTES:
                return _err(f"no note {title!r}")
            return _ok(NOTES[title])
        if name == "delete_note":
            title = arguments["title"]
            if title not in NOTES:
                return _err(f"no note {title!r}")
            del NOTES[title]
            return _ok(f"deleted {title!r}")
        return _err(f"unknown tool: {name}")
    except (KeyError, TypeError) as e:
        return _err(f"bad args for {name}: {e}")


def _ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _err(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def handle_request(msg: dict) -> "dict | None":
    method = msg.get("method")
    msg_id = msg.get("id")

    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "notes-demo", "version": "0.1.0"},
        }
    elif method == "notifications/initialized":
        return None
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = msg.get("params") or {}
        result = call_tool(params.get("name", ""), params.get("arguments") or {})
    elif msg_id is None:
        return None
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
