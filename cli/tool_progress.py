"""Live-репортер для ToolRouter: стримит события tool-loop в терминал.

Между LLM-вызовами крутится спиннер «Думаю...», на каждом tool_call
печатается отдельная строка с именем сервера/тула и аргументами,
после возврата — строка с результатом (✓/✗). Пользователь сразу видит,
какой именно тул дёргается в моменте.

Использование:

    reporter = ToolProgressReporter()
    try:
        loop = tool_router.chat(messages, params, system_prompt, on_event=reporter)
    finally:
        reporter.stop()
"""
from __future__ import annotations

import json
import sys
from typing import Optional

from cli.ansi import CYAN, DIM, GREEN, MAGENTA, RESET, YELLOW
from cli.spinner import Spinner


class ToolProgressReporter:
    """Подписчик на события ToolRouter.chat. Сам управляет своим спиннером."""

    def __init__(self,
                 args_limit: int = 80,
                 result_limit: int = 110,
                 stream=None):
        self._spinner: Optional[Spinner] = None
        self._args_limit   = args_limit
        self._result_limit = result_limit
        self._stream       = stream or sys.stdout

    def __call__(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "thinking_start":
            self._start_spinner(self._thinking_label(event))
        elif kind == "thinking_end":
            self._stop_spinner()
        elif kind == "tool_start":
            self._stop_spinner()
            self._print_tool_start(event)
        elif kind == "tool_end":
            # Спиннер уже снят на tool_start; ничего стартовать не нужно.
            self._print_tool_end(event)

    def stop(self) -> None:
        """Гарантированно погасить спиннер при выходе/исключении."""
        self._stop_spinner()

    # ── визуал ───────────────────────────────────────────────────────────────

    def _thinking_label(self, event: dict) -> str:
        iteration = event.get("iteration")
        if iteration and iteration > 1:
            return f"Думаю... (шаг {iteration})"
        return "Думаю..."

    def _print_tool_start(self, event: dict) -> None:
        sid   = event.get("server_id") or "?"
        tool  = event.get("tool_name") or "?"
        args  = event.get("arguments") or {}
        args_preview = _short(_json_compact(args), self._args_limit)
        self._stream.write(
            f"  {DIM}▸{RESET} {MAGENTA}{sid}{RESET}.{CYAN}{tool}{RESET}"
            f"{DIM}({args_preview}){RESET}\n"
        )
        self._stream.flush()

    def _print_tool_end(self, event: dict) -> None:
        inv = event.get("invocation")
        if inv is None:
            return
        marker = f"{YELLOW}✗{RESET}" if getattr(inv, "is_error", False) else f"{GREEN}✓{RESET}"
        res = _short(getattr(inv, "result_text", "") or "(no output)", self._result_limit)
        self._stream.write(f"  {marker} {DIM}→ {res}{RESET}\n")
        self._stream.flush()

    # ── спиннер ──────────────────────────────────────────────────────────────

    def _start_spinner(self, label: str) -> None:
        if self._spinner is not None:
            self._stop_spinner()
        self._spinner = Spinner(label)
        self._spinner.__enter__()

    def _stop_spinner(self) -> None:
        if self._spinner is not None:
            self._spinner.__exit__(None, None, None)
            self._spinner = None


# ── helpers ──────────────────────────────────────────────────────────────────


def _json_compact(value) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _short(s: str, n: int) -> str:
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"
