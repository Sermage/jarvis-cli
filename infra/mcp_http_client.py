"""MCP-клиент поверх Streamable HTTP (спека 2025-03-26).

Streamable HTTP — это новый сетевой транспорт MCP: вместо двух подпроцессных
pipe'ов клиент шлёт JSON-RPC сообщения POST-ом на один URL, а сервер
отвечает либо `application/json` (одно сообщение), либо `text/event-stream`
(SSE-стрим из нескольких сообщений).

Здесь — минимальный синхронный клиент: каждый запрос блокирует до получения
ответа с нашим `id`, нотификации отправляются и забываются (сервер
отвечает 202 без тела). Session-Id, если сервер выдал на initialize,
эхо-пробрасывается в заголовках всех последующих запросов.
"""
from __future__ import annotations

import itertools
import json
import threading
from typing import Any, Callable, Optional

import requests

from domain.mcp import McpTool, ToolResult
from infra.mcp_stdio_client import _flatten_mcp_content


MCP_PROTOCOL_VERSION = "2024-11-05"


class HttpMcpClient:
    """Реализация McpClient поверх Streamable HTTP.

    Транспорт инжектируется через `http_post` для тестов; по умолчанию —
    `requests.post`. Сигнатура совместима с `requests.post(url, headers=...,
    json=..., timeout=..., stream=True)`.
    """

    def __init__(self,
                 server_id: str,
                 url: str,
                 headers: Optional[dict] = None,
                 http_post: Optional[Callable[..., Any]] = None,
                 request_timeout: float = 30.0,
                 call_timeout: float = 120.0,
                 client_name: str = "jarvis-cli",
                 client_version: str = "0.1.0"):
        self.server_id        = server_id
        self._url             = url
        self._extra_headers   = dict(headers or {})
        self._post            = http_post or requests.post
        self._request_timeout = request_timeout
        self._call_timeout    = call_timeout
        self._client_name     = client_name
        self._client_version  = client_version

        self._id_gen          = itertools.count(1)
        self._session_id: Optional[str] = None
        self._tools_cache: Optional[list[McpTool]] = None
        self._io_lock         = threading.Lock()
        self._started         = False

    # ── жизненный цикл ───────────────────────────────────────────────────────

    def start(self) -> None:
        if self._started:
            return
        self._request("initialize", {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities":    {"tools": {}},
            "clientInfo":      {"name": self._client_name, "version": self._client_version},
        })
        # MCP-серверы по спеке принимают эту нотификацию без ответа (202).
        # Если сервер ответит ошибкой — игнорируем: handshake уже выполнен.
        try:
            self._notify("notifications/initialized", {})
        except Exception:
            pass
        self._started = True

    def close(self) -> None:
        # HTTP-транспорт stateless — нечего закрывать. Сбрасываем session_id,
        # чтобы повторный start() прошёл хендшейк заново.
        self._session_id = None
        self._started = False

    # ── публичный API ─────────────────────────────────────────────────────────

    def list_tools(self) -> list[McpTool]:
        if self._tools_cache is not None:
            return self._tools_cache
        result = self._request("tools/list", {})
        tools: list[McpTool] = []
        for t in result.get("tools", []):
            tools.append(McpTool(
                server_id    = self.server_id,
                name         = t["name"],
                description  = t.get("description", "") or "",
                input_schema = t.get("inputSchema") or {"type": "object", "properties": {}},
            ))
        self._tools_cache = tools
        return tools

    def call_tool(self, name: str, arguments: dict) -> ToolResult:
        result = self._request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            timeout=self._call_timeout,
        )
        return ToolResult(
            text     = _flatten_mcp_content(result.get("content", [])),
            is_error = bool(result.get("isError", False)),
            raw      = result,
        )

    # ── JSON-RPC поверх HTTP ──────────────────────────────────────────────────

    def _request(self, method: str, params: Optional[dict] = None,
                 timeout: Optional[float] = None) -> dict:
        msg_id = next(self._id_gen)
        msg    = {"jsonrpc": "2.0", "id": msg_id, "method": method,
                  "params": params or {}}
        with self._io_lock:
            resp = self._post_message(msg, timeout or self._request_timeout)
        try:
            data = self._read_response(resp, msg_id)
        finally:
            # При SSE мы могли не до конца прочитать стрим — закрываем явно.
            try:
                resp.close()
            except Exception:
                pass
        if "error" in data:
            err = data["error"]
            raise RuntimeError(
                f"MCP {method} on {self.server_id} failed: "
                f"{err.get('code')} {err.get('message')}"
            )
        return data.get("result") or {}

    def _notify(self, method: str, params: Optional[dict] = None) -> None:
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        with self._io_lock:
            resp = self._post_message(msg, self._request_timeout)
        try:
            resp.close()
        except Exception:
            pass

    def _post_message(self, msg: dict, timeout: float):
        headers = {
            "Content-Type": "application/json",
            "Accept":       "application/json, text/event-stream",
        }
        # User-headers перекрывают наши дефолтные (например, для Authorization).
        headers.update(self._extra_headers)
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        resp = self._post(self._url, headers=headers, json=msg,
                          timeout=timeout, stream=True)
        resp.raise_for_status()
        new_sid = resp.headers.get("Mcp-Session-Id") if getattr(resp, "headers", None) else None
        if new_sid:
            self._session_id = new_sid
        return resp

    def _read_response(self, resp, expected_id: int) -> dict:
        # 202 No Body — нормальный ответ на нотификацию.
        if getattr(resp, "status_code", 200) == 202:
            return {"result": {}}
        content_type = (resp.headers.get("Content-Type") or "").lower()
        if "text/event-stream" in content_type:
            return self._read_sse(resp, expected_id)
        # JSON-ответ: может быть одно сообщение или массив батча.
        try:
            data = resp.json()
        except ValueError:
            text = resp.text if hasattr(resp, "text") else ""
            raise RuntimeError(
                f"MCP {self.server_id}: ожидался JSON, получено {content_type!r}: "
                f"{text[:200]!r}"
            )
        return _pick_message(data, expected_id)

    def _read_sse(self, resp, expected_id: int) -> dict:
        """Читать SSE до сообщения с нужным `id`.

        Формат SSE: события разделены пустой строкой, тело каждого — это
        `data: <fragment>` строки, склеиваемые в единый payload (JSON-RPC).
        """
        buf: list[str] = []
        # iter_lines декодирует строки и убирает trailing \n.
        for raw in resp.iter_lines(decode_unicode=True):
            line = "" if raw is None else raw
            if line == "":
                if buf:
                    payload = "\n".join(buf)
                    buf = []
                    try:
                        msg = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(msg, list):
                        for sub in msg:
                            if isinstance(sub, dict) and sub.get("id") == expected_id:
                                return sub
                        continue
                    if msg.get("id") == expected_id:
                        return msg
                continue
            if line.startswith(":"):
                continue  # SSE comment / keep-alive
            if line.startswith("data:"):
                buf.append(line[5:].lstrip())
            # event:, id:, retry: и прочее — игнорируем, нас интересует только data.
        # Стрим закрылся без нужного сообщения.
        raise RuntimeError(
            f"MCP {self.server_id}: SSE-стрим завершился без ответа на id={expected_id}"
        )


def _pick_message(data, expected_id: int) -> dict:
    """Из одного JSON-ответа или массива выбрать сообщение с нашим id."""
    if isinstance(data, list):
        for msg in data:
            if isinstance(msg, dict) and msg.get("id") == expected_id:
                return msg
        # Если своего id не нашли — возвращаем первое сообщение (на случай
        # серверов, которые не эхо-проставляют id; в норме MCP это требует).
        return data[0] if data else {}
    if isinstance(data, dict):
        return data
    return {}
