"""Тесты CLI-подтверждения записи: раскраска diff и y/n-логика."""
from __future__ import annotations

import io

from cli.ansi import GREEN, RED, RESET
from cli.fs_confirm import colorize_diff, make_interactive_confirm


def test_colorize_marks_added_and_removed():
    diff = "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-old\n+new\n unchanged\n"
    out = colorize_diff(diff)
    assert f"{RED}-old{RESET}" in out
    assert f"{GREEN}+new{RESET}" in out
    # +++/--- заголовки не должны попасть под +/− раскраску как обычные строки
    assert out.count(RED + "-old") == 1


def test_confirm_yes_variants_approve():
    for ans in ("y", "yes", "да", "Y"):
        confirm = make_interactive_confirm(reader=lambda _p, a=ans: a,
                                           stream=io.StringIO())
        assert confirm("f.py", "diff") is True


def test_confirm_default_is_no():
    confirm = make_interactive_confirm(reader=lambda _p: "", stream=io.StringIO())
    assert confirm("f.py", "diff") is False


def test_confirm_shows_colored_diff():
    buf = io.StringIO()
    confirm = make_interactive_confirm(reader=lambda _p: "n", stream=buf)
    confirm("f.py", "-gone\n+added\n")
    printed = buf.getvalue()
    assert "f.py" in printed
    assert RED in printed and GREEN in printed
