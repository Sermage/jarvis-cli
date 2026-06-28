"""Оркестратор tool-loop: связывает LLM (ToolCallingLLMClient) и MCP-серверы.

Делает классический OpenAI tool-calling цикл:

    chat → (tool_calls?) → выполнить каждый через нужный McpClient →
    добавить tool-сообщения в историю → chat → ... → пока модель не
    вернёт обычный ответ без tool_calls (или пока не упрёмся в лимит).

Маршрутизация: имена тулов LLM получает как `serverId__toolName`,
поэтому по префиксу до `__` всегда понятно, куда отправлять вызов.
Это и есть «агент сам выбирает инструмент» из задания.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from app.ports import McpRegistry, ToolCallingLLMClient
from domain.mcp import (
    McpTool,
    ToolResult,
    make_qualified_tool_name,
    split_qualified_tool_name,
)


@dataclass(frozen=True)
class ToolInvocation:
    """След одного вызова тула в рамках tool-loop — нужен для отчёта и тестов."""
    iteration: int
    call_id: str
    server_id: str
    tool_name: str
    arguments: dict
    result_text: str
    is_error: bool


@dataclass
class ToolLoopResult:
    reply: str
    iterations: int = 0
    trace: list = field(default_factory=list)
    truncated: bool = False  # True если упёрлись в max_iterations


class ToolRouter:
    """Связывает ToolCallingLLMClient и реестр MCP-серверов в один chat().

    Если в реестре нет тулов — просто проксирует `chat()` без tool-loop.
    """

    def __init__(self,
                 llm: ToolCallingLLMClient,
                 registry: McpRegistry,
                 max_iterations: int = 10):
        self._llm      = llm
        self._registry = registry
        self._max      = max_iterations

    # ── публичный API ─────────────────────────────────────────────────────────

    def collect_openai_tools(self) -> list[dict]:
        """Собрать все MCP-тулы в формате OpenAI function calling."""
        out: list[dict] = []
        for tool in self._registry.all_tools():
            out.append(self._tool_to_openai(tool))
        return out

    def chat(self,
             messages: list,
             params: dict,
             system_prompt: Optional[str] = None) -> ToolLoopResult:
        tools = self.collect_openai_tools()
        if not tools:
            # Нет тулов — обычный chat без tool calling.
            reply = self._llm.chat(messages, params, system_prompt)
            return ToolLoopResult(reply=reply, iterations=0)

        history = list(messages)
        trace: list[ToolInvocation] = []

        for iteration in range(1, self._max + 1):
            msg = self._llm.chat_with_tools(history, params, tools, system_prompt)
            tool_calls = msg.get("tool_calls") or []
            content    = msg.get("content") or ""

            if not tool_calls:
                return ToolLoopResult(
                    reply      = content,
                    iterations = iteration - 1,
                    trace      = trace,
                )

            # OpenAI-совместимый протокол требует, чтобы перед tool-сообщениями
            # в истории лежал assistant-ход с массивом tool_calls. content
            # может быть None — допустимо.
            history.append({
                "role":       "assistant",
                "content":    content or None,
                "tool_calls": tool_calls,
            })

            for call in tool_calls:
                invocation = self._dispatch_call(iteration, call)
                trace.append(invocation)
                history.append({
                    "role":         "tool",
                    "tool_call_id": invocation.call_id,
                    "content":      invocation.result_text or "(no output)",
                })

        return ToolLoopResult(
            reply      = "(достигнут лимит итераций tool-loop — задача не завершена)",
            iterations = self._max,
            trace      = trace,
            truncated  = True,
        )

    # ── внутренности ──────────────────────────────────────────────────────────

    @staticmethod
    def _tool_to_openai(tool: McpTool) -> dict:
        # description в OpenAI ограничен ~1024 символами; не трогаем здесь —
        # MCP-серверы держат описания компактными. При нужде обрежем позже.
        return {
            "type": "function",
            "function": {
                "name":        tool.qualified_name,
                "description": tool.description or tool.name,
                "parameters":  tool.input_schema or {"type": "object", "properties": {}},
            },
        }

    def _dispatch_call(self, iteration: int, call: dict) -> ToolInvocation:
        call_id  = call.get("id") or f"call_{iteration}"
        function = call.get("function") or {}
        qname    = function.get("name") or ""
        raw_args = function.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
        except (json.JSONDecodeError, TypeError):
            return ToolInvocation(
                iteration   = iteration,
                call_id     = call_id,
                server_id   = split_qualified_tool_name(qname)[0],
                tool_name   = split_qualified_tool_name(qname)[1],
                arguments   = {},
                result_text = f"[error] не удалось разобрать arguments как JSON: {raw_args!r}",
                is_error    = True,
            )

        server_id, tool_name = split_qualified_tool_name(qname)
        client = self._registry.get(server_id)
        if client is None:
            return ToolInvocation(
                iteration   = iteration,
                call_id     = call_id,
                server_id   = server_id,
                tool_name   = tool_name,
                arguments   = args,
                result_text = f"[error] неизвестный MCP-сервер {server_id!r}",
                is_error    = True,
            )

        try:
            result: ToolResult = client.call_tool(tool_name, args)
        except Exception as e:
            return ToolInvocation(
                iteration   = iteration,
                call_id     = call_id,
                server_id   = server_id,
                tool_name   = tool_name,
                arguments   = args,
                result_text = f"[error] вызов тула {tool_name} упал: {e}",
                is_error    = True,
            )

        return ToolInvocation(
            iteration   = iteration,
            call_id     = call_id,
            server_id   = server_id,
            tool_name   = tool_name,
            arguments   = args,
            result_text = result.text,
            is_error    = result.is_error,
        )


def make_tool_name(server_id: str, tool_name: str) -> str:
    """Реэкспорт хелпера — удобно использовать в тестах и CLI."""
    return make_qualified_tool_name(server_id, tool_name)
