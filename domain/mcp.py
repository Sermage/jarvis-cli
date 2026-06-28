"""Доменные модели для интеграции с MCP-серверами.

MCP (Model Context Protocol) — это спецификация Anthropic для подключения
внешних инструментов к LLM. Сервер выставляет список тулов, а агент
выбирает нужный и вызывает его. Здесь — чистые DTO без зависимостей от
транспорта или конкретного протокола (JSON-RPC спрятан в `infra/`).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional


# OpenAI tool calling требует имя из [a-zA-Z0-9_-]{1,64}. MCP такой строгости
# не накладывает, поэтому при склейке `server__tool` обе части санитизируем.
_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]")
TOOL_NAME_SEP = "__"
TOOL_NAME_MAX = 64


def sanitize_name_part(raw: str) -> str:
    """Привести часть имени (server_id или tool name) к [A-Za-z0-9_-]+."""
    cleaned = _SAFE_NAME_RE.sub("_", raw.strip())
    return cleaned or "x"


def make_qualified_tool_name(server_id: str, tool_name: str) -> str:
    """Собрать имя `server__tool` для отправки в LLM. Обрезаем до 64 символов."""
    full = f"{sanitize_name_part(server_id)}{TOOL_NAME_SEP}{sanitize_name_part(tool_name)}"
    return full[:TOOL_NAME_MAX]


def split_qualified_tool_name(qualified: str) -> tuple[str, str]:
    """Разделить `server__tool` обратно. Если разделителя нет — server_id пуст."""
    if TOOL_NAME_SEP not in qualified:
        return ("", qualified)
    server_id, _, tool_name = qualified.partition(TOOL_NAME_SEP)
    return (server_id, tool_name)


@dataclass(frozen=True)
class McpServerConfig:
    """Декларативное описание MCP-сервера для запуска.

    Поддерживаемые транспорты:
    - `stdio` — подпроцесс. Используются `command`, `args`, `env`, `cwd`.
    - `http`  — Streamable HTTP (MCP 2025-03-26). Используются `url`,
                `headers`; `command` остаётся пустым.

    Поля для «не моего» транспорта просто игнорируются — это ок, конфиг
    задаётся либо stdio-стилем, либо http-стилем, не вперемешку.
    """
    server_id: str
    command: str = ""
    args: tuple[str, ...] = ()
    env: dict = field(default_factory=dict)
    cwd: Optional[str] = None
    enabled: bool = True
    transport: str = "stdio"
    url: Optional[str] = None
    headers: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "server_id": self.server_id,
            "command":   self.command,
            "args":      list(self.args),
            "env":       dict(self.env),
            "cwd":       self.cwd,
            "enabled":   self.enabled,
            "transport": self.transport,
            "url":       self.url,
            "headers":   dict(self.headers),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "McpServerConfig":
        return cls(
            server_id = data["server_id"],
            command   = data.get("command", "") or "",
            args      = tuple(data.get("args") or ()),
            env       = dict(data.get("env") or {}),
            cwd       = data.get("cwd"),
            enabled   = bool(data.get("enabled", True)),
            transport = data.get("transport", "stdio"),
            url       = data.get("url"),
            headers   = dict(data.get("headers") or {}),
        )


@dataclass(frozen=True)
class McpTool:
    """Описание одного тула, обнаруженного на конкретном сервере.

    `input_schema` — это JSON Schema из ответа `tools/list`. Передаём её
    в LLM как `function.parameters` без изменений.
    """
    server_id: str
    name: str
    description: str
    input_schema: dict

    @property
    def qualified_name(self) -> str:
        return make_qualified_tool_name(self.server_id, self.name)


@dataclass(frozen=True)
class ToolCall:
    """Один вызов тула, как просит LLM."""
    call_id: str
    qualified_name: str
    arguments: dict

    @property
    def server_id(self) -> str:
        return split_qualified_tool_name(self.qualified_name)[0]

    @property
    def tool_name(self) -> str:
        return split_qualified_tool_name(self.qualified_name)[1]


@dataclass(frozen=True)
class ToolResult:
    """Унифицированный результат вызова тула.

    `text` — плоское текстовое представление для отправки обратно в LLM
    (модель не умеет читать структурные content-блоки MCP напрямую,
    поэтому склеиваем text-части и сериализуем нетекстовые).
    `is_error` — флаг MCP `isError`, выставляется сервером при ошибке тула.
    `raw` — исходный ответ для отладки.
    """
    text: str
    is_error: bool = False
    raw: Any = None
