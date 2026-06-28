"""Файловое хранилище конфигурации MCP-серверов.

Конфиг лежит в `~/.jarvis/mcp/servers.json` — один JSON-массив объектов,
по структуре близкий к `~/.claude.json` (поле `mcpServers`).
"""
from __future__ import annotations

import json
import os
from typing import Optional

from domain.mcp import McpServerConfig


class FileMcpConfigRepository:
    def __init__(self, file_path: str):
        self._path = file_path

    # ── чтение ───────────────────────────────────────────────────────────────

    def list_all(self) -> list[McpServerConfig]:
        if not os.path.exists(self._path):
            return []
        try:
            with open(self._path, encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            return []
        items = raw.get("servers") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            return []
        return [McpServerConfig.from_dict(d) for d in items if isinstance(d, dict)]

    def get(self, server_id: str) -> Optional[McpServerConfig]:
        for c in self.list_all():
            if c.server_id == server_id:
                return c
        return None

    # ── запись ───────────────────────────────────────────────────────────────

    def save(self, cfg: McpServerConfig) -> None:
        items = self.list_all()
        replaced = False
        for i, c in enumerate(items):
            if c.server_id == cfg.server_id:
                items[i] = cfg
                replaced = True
                break
        if not replaced:
            items.append(cfg)
        self._write(items)

    def delete(self, server_id: str) -> None:
        items = [c for c in self.list_all() if c.server_id != server_id]
        self._write(items)

    def set_enabled(self, server_id: str, enabled: bool) -> None:
        items = self.list_all()
        for i, c in enumerate(items):
            if c.server_id == server_id:
                items[i] = McpServerConfig(
                    server_id = c.server_id,
                    command   = c.command,
                    args      = c.args,
                    env       = c.env,
                    cwd       = c.cwd,
                    enabled   = enabled,
                    transport = c.transport,
                )
                break
        self._write(items)

    def _write(self, items: list[McpServerConfig]) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        payload = {"servers": [c.to_dict() for c in items]}
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
