"""Оркестратор tool-loop: связывает LLM (ToolCallingLLMClient) и MCP-серверы.

Делает классический OpenAI tool-calling цикл:

    chat → (tool_calls?) → выполнить каждый через нужный McpClient →
    добавить tool-сообщения в историю → chat → ... → пока модель не
    вернёт обычный ответ без tool_calls (или пока не упрёмся в лимит).

Маршрутизация: имена тулов LLM получает как `serverId__toolName`,
поэтому по префиксу до `__` всегда понятно, куда отправлять вызов.
Это и есть «агент сам выбирает инструмент» из задания.

Опциональный `on_event` коллбек получает события по мере их появления
(`thinking_start`, `thinking_end`, `tool_start`, `tool_end`). CLI этим
рисует live-прогресс — пользователь видит, какой тул дёргается прямо
сейчас, как в Claude Code.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Optional

from app.ports import McpRegistry, ToolCallingLLMClient
from domain.mcp import (
    McpTool,
    ToolResult,
    make_qualified_tool_name,
    split_qualified_tool_name,
)


EventSink = Callable[[dict], None]


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
                 max_iterations: int = 20,
                 failure_hint_threshold: int = 2):
        self._llm      = llm
        self._registry = registry
        self._max      = max_iterations
        # Сколько подряд провалов одного и того же тула терпеть до того,
        # как мы аннотируем tool-сообщение подсказкой «не упорствуй».
        self._fail_threshold = failure_hint_threshold

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
             system_prompt: Optional[str] = None,
             on_event: Optional[EventSink] = None) -> ToolLoopResult:
        emit = _safe_emit(on_event)
        tools = self.collect_openai_tools()
        if not tools:
            # Нет тулов — обычный chat без tool calling.
            emit({"type": "thinking_start", "iteration": 0})
            reply = self._llm.chat(messages, params, system_prompt)
            emit({"type": "thinking_end", "iteration": 0,
                  "had_tool_calls": False, "num_calls": 0})
            return ToolLoopResult(reply=reply, iterations=0)

        history = list(messages)
        trace: list[ToolInvocation] = []
        # Счётчик подряд идущих провалов по каждому тулу. Если LLM упорно
        # дёргает то же самое и оно падает, добавим в tool-message подсказку,
        # чтобы модель сменила тактику и не упёрлась в max_iterations.
        consecutive_failures: dict[tuple[str, str], int] = {}

        for iteration in range(1, self._max + 1):
            emit({"type": "thinking_start", "iteration": iteration})
            msg = self._llm.chat_with_tools(history, params, tools, system_prompt)
            tool_calls = msg.get("tool_calls") or []
            content    = msg.get("content") or ""
            emit({"type": "thinking_end", "iteration": iteration,
                  "had_tool_calls": bool(tool_calls), "num_calls": len(tool_calls)})

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
                self._emit_tool_start(emit, iteration, call)
                invocation = self._dispatch_call(iteration, call)
                trace.append(invocation)
                emit({"type": "tool_end", "iteration": iteration,
                      "invocation": invocation})

                key = (invocation.server_id, invocation.tool_name)
                if invocation.is_error:
                    consecutive_failures[key] = consecutive_failures.get(key, 0) + 1
                else:
                    consecutive_failures[key] = 0

                tool_content = invocation.result_text or "(no output)"
                fail_n = consecutive_failures[key]
                if fail_n >= self._fail_threshold:
                    tool_content = (
                        f"{tool_content}\n\n"
                        f"[hint] тул {invocation.server_id}.{invocation.tool_name} "
                        f"падает {fail_n} раз(а) подряд. Повтор с похожими аргументами "
                        f"скорее всего тоже упадёт — попробуй другой тул, другой "
                        f"сервер или сократи диапазон/объём запроса."
                    )

                history.append({
                    "role":         "tool",
                    "tool_call_id": invocation.call_id,
                    "content":      tool_content,
                })

        return ToolLoopResult(
            reply      = "(достигнут лимит итераций tool-loop — задача не завершена)",
            iterations = self._max,
            trace      = trace,
            truncated  = True,
        )

    @staticmethod
    def _emit_tool_start(emit: EventSink, iteration: int, call: dict) -> None:
        """Послать tool_start ДО фактического вызова — чтобы UI успел нарисовать.

        Парсинг аргументов дублируется с `_dispatch_call`, но это копейки,
        и зато тул-стартовое событие имеет уже разобранные args для печати.
        """
        function = call.get("function") or {}
        qname    = function.get("name") or ""
        raw_args = function.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
        except (json.JSONDecodeError, TypeError):
            args = {}
        server_id, tool_name = split_qualified_tool_name(qname)
        emit({
            "type":      "tool_start",
            "iteration": iteration,
            "call_id":   call.get("id") or f"call_{iteration}",
            "server_id": server_id,
            "tool_name": tool_name,
            "arguments": args,
        })

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


def _safe_emit(on_event: Optional[EventSink]) -> EventSink:
    """Обёртка, чтобы исключение в репортере не валило tool-loop.

    UI-сторона не должна влиять на корректность бизнес-логики.
    """
    if on_event is None:
        return lambda _ev: None
    def _wrapped(ev: dict) -> None:
        try:
            on_event(ev)
        except Exception:
            pass
    return _wrapped
