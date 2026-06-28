"""Реестр запущенных MCP-клиентов.

Поднимает все включённые серверы из репозитория конфига, индексирует их
по `server_id`, кэширует объединённый список тулов. Шатдаун — обязателен:
без него останутся висеть подпроцессы.
"""
from __future__ import annotations

from typing import Callable, Optional

from app.ports import McpClient, McpConfigRepository
from domain.mcp import McpServerConfig, McpTool
from infra.mcp_http_client import HttpMcpClient
from infra.mcp_stdio_client import StdioMcpClient


# Фабрика клиента вынесена параметром — это позволит в тестах подменить
# реальный подпроцесс на in-memory fake без monkey-патчей.
ClientFactory = Callable[[McpServerConfig], McpClient]


def _default_factory(cfg: McpServerConfig) -> McpClient:
    if cfg.transport == "stdio":
        return StdioMcpClient(
            server_id = cfg.server_id,
            command   = cfg.command,
            args      = list(cfg.args),
            env       = dict(cfg.env),
            cwd       = cfg.cwd,
        )
    if cfg.transport == "http":
        if not cfg.url:
            raise RuntimeError(
                f"MCP-сервер {cfg.server_id!r}: transport=http, но url пустой"
            )
        return HttpMcpClient(
            server_id = cfg.server_id,
            url       = cfg.url,
            headers   = dict(cfg.headers),
        )
    raise RuntimeError(f"Транспорт {cfg.transport!r} пока не поддерживается")


class StdioMcpRegistry:
    """Поднимает все включённые серверы при `start_all()`."""

    def __init__(self,
                 repo: McpConfigRepository,
                 factory: Optional[ClientFactory] = None):
        self._repo    = repo
        self._factory = factory or _default_factory
        self._clients: dict = {}
        self._tools_cache: Optional[list[McpTool]] = None
        self._failed: list[tuple[str, str]] = []

    # ── жизненный цикл ───────────────────────────────────────────────────────

    def start_all(self) -> None:
        for cfg in self._repo.list_all():
            if not cfg.enabled:
                continue
            if cfg.server_id in self._clients:
                continue
            client = self._factory(cfg)
            try:
                client.start()
            except Exception as e:
                self._failed.append((cfg.server_id, str(e)))
                try:
                    client.close()
                except Exception:
                    pass
                continue
            self._clients[cfg.server_id] = client
        self._tools_cache = None

    def shutdown(self) -> None:
        for client in list(self._clients.values()):
            try:
                client.close()
            except Exception:
                pass
        self._clients.clear()
        self._tools_cache = None

    # ── чтение ───────────────────────────────────────────────────────────────

    def clients(self) -> list:
        return list(self._clients.values())

    def get(self, server_id: str):
        return self._clients.get(server_id)

    def all_tools(self) -> list[McpTool]:
        if self._tools_cache is not None:
            return self._tools_cache
        tools: list[McpTool] = []
        for client in self._clients.values():
            try:
                tools.extend(client.list_tools())
            except Exception as e:
                self._failed.append((client.server_id, f"list_tools: {e}"))
        self._tools_cache = tools
        return tools

    def failures(self) -> list[tuple[str, str]]:
        return list(self._failed)
