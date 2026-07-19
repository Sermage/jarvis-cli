"""In-process источник тулов поддержки над JSON с пользователями и тикетами.

Реализует тот же протокол `McpClient` (`app.ports.McpClient`), что и
stdio/http-клиенты, но вместо JSON-RPC к внешнему серверу читает данные из
локального JSON-файла. За счёт этого он бесшовно встаёт в `McpRegistry`
(`register()`), а `ToolRouter` отдаёт его тулы модели и маршрутизирует
вызовы `support__<tool>` сюда — как к обычной MCP-CRM. Никаких правок в
tool-loop не требуется: «агент сам выбирает инструмент» уже работает.

Это и есть «подключить CRM или JSON с пользователями/тикетами через MCP»
из задания — только вместо реального CRM за портом стоит файл, что удобно
для демо и тестов. Заменить на реальный CRM = поднять внешний MCP-сервер и
включить его в конфиге, use case (`app/support_assistant.py`) не изменится.

Формат файла (`~/.jarvis/support/tickets.json` по умолчанию):

    {
      "users":   [{"id": "U-100", "name": ..., "plan": ..., "auth_method": ...}],
      "tickets": [{"id": "T-1024", "user_id": "U-100", "status": "open",
                   "product_area": "auth", "subject": ...,
                   "messages": [{"author": "user", "text": ...}]}]
    }

Тулы (агент вызывает их сам в tool-loop):
  • get_ticket     — тикет по id + профиль автора + переписка
  • get_user       — профиль пользователя + список его тикетов
  • search_tickets — похожие обращения по тексту / статусу / области продукта
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from domain.mcp import McpTool, ToolResult


class TicketStoreError(Exception):
    """Проблема с файлом-хранилищем тикетов (нет файла, битый JSON)."""


class TicketStoreClient:
    """Тулы поддержки над JSON-файлом `path`.

    Данные читаются один раз в `start()` и держатся в памяти. Для демо/тестов
    можно передать `data=` напрямую — тогда файл не читается.
    """

    def __init__(self,
                 path: Optional[str] = None,
                 server_id: str = "support",
                 data: Optional[dict] = None,
                 max_results: int = 20):
        self.server_id   = server_id
        self._path       = Path(path).expanduser() if path else None
        self._max        = max_results
        self._users: dict[str, dict] = {}
        self._tickets: dict[str, dict] = {}
        if data is not None:
            self._ingest(data)

    # ── жизненный цикл (McpClient) ────────────────────────────────────────────

    def start(self) -> None:
        # data= уже загружены в конструкторе — файл не обязателен.
        if self._users or self._tickets:
            return
        if self._path is None:
            raise TicketStoreError("не задан ни путь к файлу тикетов, ни data=")
        if not self._path.is_file():
            raise TicketStoreError(f"нет файла хранилища тикетов: {self._path}")
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise TicketStoreError(f"не читается {self._path}: {e}") from e
        self._ingest(raw)

    def close(self) -> None:
        pass

    def _ingest(self, raw: dict) -> None:
        if not isinstance(raw, dict):
            raise TicketStoreError("корень JSON должен быть объектом с users/tickets")
        self._users = {u["id"]: u for u in raw.get("users", []) if u.get("id")}
        self._tickets = {t["id"]: t for t in raw.get("tickets", []) if t.get("id")}

    # ── описание тулов ────────────────────────────────────────────────────────

    def list_tools(self) -> list[McpTool]:
        return [
            self._tool("get_ticket",
                       "Получить тикет поддержки по идентификатору: тему, статус, "
                       "приоритет, область продукта, код ошибки, всю переписку и "
                       "профиль автора (тариф, способ входа). Вызывай первым, если "
                       "в вопросе упомянут номер тикета вида T-1024.",
                       {"ticket_id": {"type": "string",
                                      "description": "Идентификатор тикета, напр. 'T-1024'."}},
                       required=["ticket_id"]),
            self._tool("get_user",
                       "Получить профиль пользователя по идентификатору: имя, email, "
                       "тариф, способ авторизации, платформу — и список его тикетов. "
                       "Используй, чтобы учесть контекст пользователя в ответе.",
                       {"user_id": {"type": "string",
                                    "description": "Идентификатор пользователя, напр. 'U-100'."}},
                       required=["user_id"]),
            self._tool("search_tickets",
                       "Найти похожие обращения по подстроке в теме/тексте, а также "
                       "отфильтровать по статусу (open/pending/closed) и области "
                       "продукта (auth/billing/...). Полезно, чтобы сослаться на "
                       "уже решённые похожие тикеты.",
                       {"query": {"type": "string",
                                  "description": "Подстрока для поиска в теме и сообщениях (необязательно).",
                                  "default": ""},
                        "status": {"type": "string",
                                   "description": "Фильтр по статусу: open/pending/closed.",
                                   "default": ""},
                        "product_area": {"type": "string",
                                         "description": "Фильтр по области продукта, напр. 'auth'.",
                                         "default": ""}}),
        ]

    def _tool(self, name: str, description: str, properties: dict,
              required: Optional[list] = None) -> McpTool:
        return McpTool(
            server_id    = self.server_id,
            name         = name,
            description  = description,
            input_schema = {
                "type": "object",
                "properties": properties,
                "required": required or [],
            },
        )

    # ── вызов тула (McpClient) ────────────────────────────────────────────────

    def call_tool(self, name: str, arguments: dict) -> ToolResult:
        args = arguments or {}
        try:
            if name == "get_ticket":
                return self._get_ticket(str(args["ticket_id"]).strip())
            if name == "get_user":
                return self._get_user(str(args["user_id"]).strip())
            if name == "search_tickets":
                return self._search_tickets(
                    query        = str(args.get("query", "") or "").strip(),
                    status       = str(args.get("status", "") or "").strip().lower(),
                    product_area = str(args.get("product_area", "") or "").strip().lower(),
                )
        except KeyError as e:
            return ToolResult(text=f"Отсутствует обязательный аргумент: {e}", is_error=True)
        return ToolResult(text=f"Неизвестный тул: {name}", is_error=True)

    # ── реализация тулов ──────────────────────────────────────────────────────

    def _get_ticket(self, ticket_id: str) -> ToolResult:
        ticket = self._tickets.get(ticket_id) or self._find_ci(self._tickets, ticket_id)
        if ticket is None:
            return ToolResult(
                text=f"Тикет {ticket_id!r} не найден. Доступные: "
                     f"{', '.join(sorted(self._tickets)) or '(нет)'}",
                is_error=True)
        user = self._users.get(ticket.get("user_id", ""))
        return ToolResult(text=self._format_ticket(ticket, user))

    def _get_user(self, user_id: str) -> ToolResult:
        user = self._users.get(user_id) or self._find_ci(self._users, user_id)
        if user is None:
            return ToolResult(
                text=f"Пользователь {user_id!r} не найден. Доступные: "
                     f"{', '.join(sorted(self._users)) or '(нет)'}",
                is_error=True)
        their = [t for t in self._tickets.values() if t.get("user_id") == user["id"]]
        return ToolResult(text=self._format_user(user, their))

    def _search_tickets(self, query: str, status: str, product_area: str) -> ToolResult:
        q = query.lower()
        hits: list[dict] = []
        for t in self._tickets.values():
            if status and str(t.get("status", "")).lower() != status:
                continue
            if product_area and str(t.get("product_area", "")).lower() != product_area:
                continue
            if q and q not in self._ticket_haystack(t):
                continue
            hits.append(t)
            if len(hits) >= self._max:
                break
        if not hits:
            return ToolResult(text="Похожих тикетов не найдено под заданные фильтры.")
        lines = [f"Найдено тикетов: {len(hits)}"]
        for t in hits:
            lines.append(
                f"  {t.get('id')} [{t.get('status', '?')}/{t.get('product_area', '-')}] "
                f"{t.get('subject', '(без темы)')}")
        return ToolResult(text="\n".join(lines))

    # ── форматирование (чистое) ───────────────────────────────────────────────

    @staticmethod
    def _ticket_haystack(t: dict) -> str:
        parts = [str(t.get("subject", "")), str(t.get("error_code", ""))]
        for m in t.get("messages", []) or []:
            parts.append(str(m.get("text", "")))
        return " ".join(parts).lower()

    @staticmethod
    def _format_ticket(t: dict, user: Optional[dict]) -> str:
        lines = [
            f"Тикет {t.get('id')}",
            f"  Тема:      {t.get('subject', '(без темы)')}",
            f"  Статус:    {t.get('status', '?')}   Приоритет: {t.get('priority', '-')}",
            f"  Область:   {t.get('product_area', '-')}",
        ]
        if t.get("error_code"):
            lines.append(f"  Код ошибки: {t.get('error_code')}")
        if t.get("created_at"):
            lines.append(f"  Создан:    {t.get('created_at')}")
        if user:
            lines.append(
                f"  Автор:     {user.get('name', user.get('id'))} "
                f"(тариф {user.get('plan', '-')}, вход через {user.get('auth_method', '-')}, "
                f"платформа {user.get('platform', '-')})")
        else:
            lines.append(f"  Автор:     {t.get('user_id', '-')} (профиль не найден)")
        msgs = t.get("messages") or []
        if msgs:
            lines.append("  Переписка:")
            for m in msgs:
                lines.append(f"    [{m.get('author', '?')}] {m.get('text', '')}")
        return "\n".join(lines)

    @staticmethod
    def _format_user(u: dict, tickets: list[dict]) -> str:
        lines = [
            f"Пользователь {u.get('id')}",
            f"  Имя:       {u.get('name', '-')}",
            f"  Email:     {u.get('email', '-')}",
            f"  Тариф:     {u.get('plan', '-')}",
            f"  Вход:      {u.get('auth_method', '-')}",
            f"  Платформа: {u.get('platform', '-')}",
        ]
        if tickets:
            lines.append("  Тикеты:")
            for t in tickets:
                lines.append(
                    f"    {t.get('id')} [{t.get('status', '?')}] {t.get('subject', '')}")
        else:
            lines.append("  Тикеты:    (нет)")
        return "\n".join(lines)

    @staticmethod
    def _find_ci(index: dict, key: str) -> Optional[Any]:
        """Регистронезависимый поиск по ключу (LLM может прислать 't-1024')."""
        low = key.lower()
        for k, v in index.items():
            if k.lower() == low:
                return v
        return None
