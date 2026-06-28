"""Клиент MCP поверх stdio-транспорта (JSON-RPC 2.0, newline-delimited).

MCP спецификация описывает stdio как «по одному JSON-сообщению на строку
stdin/stdout сервера». Сообщения — обычный JSON-RPC: запрос с `id` ждёт
ответ, нотификация без `id` — fire-and-forget.

Здесь реализован минимально необходимый объём:
- initialize / notifications/initialized — хендшейк;
- tools/list — обнаружение тулов;
- tools/call — вызов тула.

Зачем не официальный `mcp` SDK: он требует Python 3.10+, а проект пока на
3.9. Свой минимальный клиент в ~150 строк — проще, чем тянуть upgrade.
"""
from __future__ import annotations

import itertools
import json
import os
import subprocess
import threading
import time
from typing import Optional

from domain.mcp import McpTool, ToolResult


MCP_PROTOCOL_VERSION = "2024-11-05"


class StdioMcpClient:
    """Подключение к одному MCP-серверу через stdio-подпроцесс.

    Транспорт инжектируется через `popen` для тестируемости (по дефолту
    `subprocess.Popen`).
    """

    def __init__(self,
                 server_id: str,
                 command: str,
                 args: Optional[list] = None,
                 env: Optional[dict] = None,
                 cwd: Optional[str] = None,
                 popen=None,
                 request_timeout: float = 30.0,
                 call_timeout: float = 120.0,
                 client_name: str = "jarvis-cli",
                 client_version: str = "0.1.0"):
        self.server_id        = server_id
        self._cmd             = [command] + list(args or [])
        self._extra_env       = env or {}
        self._cwd             = cwd
        self._popen           = popen or subprocess.Popen
        self._request_timeout = request_timeout
        self._call_timeout    = call_timeout
        self._client_name     = client_name
        self._client_version  = client_version

        self._proc: Optional[subprocess.Popen] = None
        self._id_gen          = itertools.count(1)
        self._tools_cache: Optional[list[McpTool]] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._stderr_buf: list[str] = []
        self._io_lock         = threading.Lock()

    # ── жизненный цикл ───────────────────────────────────────────────────────

    def start(self) -> None:
        if self._proc is not None:
            return
        env = dict(os.environ)
        env.update(self._extra_env)
        self._proc = self._popen(
            self._cmd,
            stdin   = subprocess.PIPE,
            stdout  = subprocess.PIPE,
            stderr  = subprocess.PIPE,
            cwd     = self._cwd,
            env     = env,
            text    = True,
            bufsize = 1,
        )
        # stderr читаем в фоне — если сервер пишет туда логи, иначе
        # 64KB-буфер pipe заполняется и всё встаёт.
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, daemon=True, name=f"mcp-{self.server_id}-stderr",
        )
        self._stderr_thread.start()

        self._request("initialize", {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities":    {"tools": {}},
            "clientInfo":      {"name": self._client_name, "version": self._client_version},
        })
        self._notify("notifications/initialized", {})

    def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin and not proc.stdin.closed:
                try:
                    proc.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
            if proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
        except Exception:
            pass

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

    # ── JSON-RPC ──────────────────────────────────────────────────────────────

    def _request(self, method: str, params: Optional[dict] = None,
                 timeout: Optional[float] = None) -> dict:
        if self._proc is None:
            raise RuntimeError(f"MCP server {self.server_id} not started")
        msg_id = next(self._id_gen)
        msg = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}
        deadline = time.monotonic() + (timeout or self._request_timeout)
        with self._io_lock:
            self._send_raw(msg)
            while True:
                if time.monotonic() > deadline:
                    raise RuntimeError(
                        f"MCP server {self.server_id} did not respond to {method} in time"
                    )
                data = self._read_raw()
                if data is None:
                    stderr_tail = "".join(self._stderr_buf[-20:])
                    raise RuntimeError(
                        f"MCP server {self.server_id} closed stdout during {method}. "
                        f"stderr tail:\n{stderr_tail}"
                    )
                # Серверы могут слать notifications (без id) между нашими запросами —
                # просто их игнорируем (тулы пока не подписываемся).
                if "id" not in data:
                    continue
                if data.get("id") != msg_id:
                    continue
                if "error" in data:
                    err = data["error"]
                    raise RuntimeError(
                        f"MCP {method} on {self.server_id} failed: "
                        f"{err.get('code')} {err.get('message')}"
                    )
                return data.get("result") or {}

    def _notify(self, method: str, params: Optional[dict] = None) -> None:
        if self._proc is None:
            return
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        with self._io_lock:
            self._send_raw(msg)

    def _send_raw(self, msg: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError(f"MCP server {self.server_id} stdin not available")
        line = json.dumps(msg, ensure_ascii=False) + "\n"
        self._proc.stdin.write(line)
        self._proc.stdin.flush()

    def _read_raw(self) -> Optional[dict]:
        if self._proc is None or self._proc.stdout is None:
            return None
        line = self._proc.stdout.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            return self._read_raw()
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            # Не-JSON строка в stdout — сервер пишет диагностику не туда;
            # пропускаем, чтобы не повесить весь pipeline.
            return self._read_raw()

    def _drain_stderr(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for raw in proc.stderr:
                # Держим последние ~200 строк, чтобы было что показать
                # в диагностике при падении.
                self._stderr_buf.append(raw)
                if len(self._stderr_buf) > 200:
                    self._stderr_buf = self._stderr_buf[-200:]
        except Exception:
            pass

    # ── для отладки ──────────────────────────────────────────────────────────

    def stderr_tail(self, n: int = 20) -> str:
        return "".join(self._stderr_buf[-n:])


def _flatten_mcp_content(content: list) -> str:
    """Превратить MCP `content` (массив text/image/... блоков) в одну строку.

    LLM в tool-сообщении принимает только текст, поэтому текстовые блоки
    склеиваем, а нетекстовые сериализуем как JSON — модель хотя бы поймёт,
    что там был не-text-результат.
    """
    if not content:
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            parts.append(str(block))
            continue
        btype = block.get("type")
        if btype == "text":
            parts.append(block.get("text", "") or "")
        else:
            parts.append(json.dumps(block, ensure_ascii=False))
    return "\n".join(parts)
