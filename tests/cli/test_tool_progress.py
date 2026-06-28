"""Тесты ToolProgressReporter — live-вывод событий tool-loop."""
from __future__ import annotations

import io
from dataclasses import dataclass

from cli.tool_progress import ToolProgressReporter


@dataclass
class _FakeInvocation:
    server_id: str = "fs"
    tool_name: str = "read_file"
    result_text: str = "содержимое"
    is_error: bool = False


def _strip_ansi(s: str) -> str:
    """Грубо вырезаем ANSI escape-последовательности для удобного assert."""
    import re
    return re.sub(r"\x1b\[[0-9;?]*[a-zA-Z]", "", s)


def _reporter():
    buf = io.StringIO()
    return ToolProgressReporter(stream=buf), buf


def test_tool_start_prints_qualified_name_with_args():
    rep, buf = _reporter()
    rep({"type": "tool_start", "server_id": "fs", "tool_name": "read_file",
         "arguments": {"path": "/x"}})
    out = _strip_ansi(buf.getvalue())
    assert "fs" in out and "read_file" in out
    assert '"path":"/x"' in out


def test_tool_end_success_prints_check_and_result():
    rep, buf = _reporter()
    rep({"type": "tool_end", "invocation": _FakeInvocation(result_text="42")})
    out = _strip_ansi(buf.getvalue())
    assert "✓" in out
    assert "42" in out


def test_tool_end_error_prints_cross():
    rep, buf = _reporter()
    rep({"type": "tool_end",
         "invocation": _FakeInvocation(result_text="boom", is_error=True)})
    out = _strip_ansi(buf.getvalue())
    assert "✗" in out
    assert "boom" in out


def test_long_result_text_is_truncated():
    rep, buf = _reporter()
    rep({"type": "tool_end",
         "invocation": _FakeInvocation(result_text="x" * 1000)})
    out = _strip_ansi(buf.getvalue())
    # Должно быть существенно короче исходных 1000.
    assert len(out) < 200
    assert "…" in out


def test_multiline_result_is_squashed_to_one_line():
    rep, buf = _reporter()
    rep({"type": "tool_end",
         "invocation": _FakeInvocation(result_text="line1\nline2\nline3")})
    out = _strip_ansi(buf.getvalue())
    # В одной выведенной строке (плюс \n в конце); внутри переносов нет.
    body = out.rstrip("\n")
    assert "\n" not in body


def test_thinking_events_do_not_throw_without_real_tty():
    """ToolProgressReporter сам управляет Spinner'ом; в тестах с stdio-буфером
    он не должен падать."""
    rep, _ = _reporter()
    rep({"type": "thinking_start", "iteration": 1})
    rep({"type": "thinking_end", "iteration": 1, "had_tool_calls": True})
    rep.stop()  # на всякий случай: повторный stop тоже ок


def test_unknown_event_type_is_ignored():
    """Защита от будущих событий: репортер не должен падать на незнакомых типах."""
    rep, buf = _reporter()
    rep({"type": "some_future_event", "data": 123})
    assert buf.getvalue() == ""


def test_stop_is_idempotent():
    rep, _ = _reporter()
    rep.stop()
    rep.stop()  # повторный вызов — ничего страшного
