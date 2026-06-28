"""Тесты bracketed-paste-aware input."""
from __future__ import annotations

import io

import pytest

from cli.input_reader import (
    PASTE_END,
    PASTE_START,
    disable_bracketed_paste,
    enable_bracketed_paste,
    read_input,
)


def _scripted_input(*lines):
    """Очередь строк: каждый input(...) отдаёт следующую."""
    queue = list(lines)
    def _fake(prompt=""):
        if not queue:
            raise EOFError
        return queue.pop(0)
    return _fake


def test_plain_input_passes_through_unchanged():
    fake = _scripted_input("просто текст")
    assert read_input("» ", _input=fake) == "просто текст"


def test_single_line_paste_strips_markers():
    fake = _scripted_input(f"{PASTE_START}одна строка{PASTE_END}")
    assert read_input("» ", _input=fake) == "одна строка"


def test_paste_with_typed_prefix_keeps_typed_text():
    """Пользователь напечатал «hi », потом вставил «бла» — должно стать «hi бла»."""
    fake = _scripted_input(f"hi {PASTE_START}бла{PASTE_END}")
    assert read_input("» ", _input=fake) == "hi бла"


def test_multiline_paste_accumulates_until_end_marker():
    """Это главный сценарий: вставка с \\n внутри не должна отправиться
    по первому же \\n — мы накапливаем строки до маркера END."""
    fake = _scripted_input(
        f"{PASTE_START}первая",
        "вторая",
        f"третья{PASTE_END}",
    )
    assert read_input("» ", _input=fake) == "первая\nвторая\nтретья"


def test_multiline_paste_end_on_own_line():
    """Если pasted-текст оканчивается на \\n, END приходит отдельной строкой —
    и ввод не возвращается, пока пользователь не нажмёт Enter."""
    fake = _scripted_input(
        f"{PASTE_START}a",
        "b",
        PASTE_END,
    )
    assert read_input("» ", _input=fake) == "a\nb\n"


def test_orphan_end_marker_is_stripped():
    """Странный случай: END без START — не падаем, просто удаляем маркер."""
    fake = _scripted_input(f"hello{PASTE_END}")
    assert read_input("» ", _input=fake) == "hello"


def test_eof_inside_paste_returns_partial_buffer():
    """Если внутри пасты случился Ctrl+D — возвращаем то, что уже собрали."""
    fake = _scripted_input(f"{PASTE_START}part1", "part2")
    # Третий вызов поднимет EOFError из _scripted_input — обёртка должна это
    # перехватить и вернуть накопленное.
    assert read_input("» ", _input=fake) == "part1\npart2"


def test_typed_text_after_paste_within_same_line_is_kept():
    """Маркер END в середине строки: всё до него — paste, всё после — выкидываем
    (в реальном терминале «после END» не бывает, но проверяем устойчивость)."""
    fake = _scripted_input(f"{PASTE_START}inside{PASTE_END}trailing-garbage")
    assert read_input("» ", _input=fake) == "inside"


def test_enable_disable_dont_crash_on_non_tty():
    """В тестах stdout — это перехваченный поток. Включение/выключение режима
    должно проходить без исключения, даже если write/flush падают."""
    enable_bracketed_paste()
    disable_bracketed_paste()


def test_enable_writes_xterm_sequence():
    """Проверяем, что включение реально отправляет 2004h в указанный поток."""
    buf = io.StringIO()
    enable_bracketed_paste(stream=buf)
    assert buf.getvalue() == "\x1b[?2004h"


def test_disable_writes_xterm_sequence():
    buf = io.StringIO()
    disable_bracketed_paste(stream=buf)
    assert buf.getvalue() == "\x1b[?2004l"


def test_continuation_input_uses_empty_prompt_by_default():
    """Внутри пасты не должно повторяться приглашение `You:` — это сбило бы вывод."""
    seen_prompts = []
    def fake(prompt=""):
        seen_prompts.append(prompt)
        # Скриптуем 2 строки пасты: первая с START, вторая с END
        if len(seen_prompts) == 1:
            return f"{PASTE_START}a"
        return f"b{PASTE_END}"
    read_input("You: ", _input=fake)
    assert seen_prompts == ["You: ", ""]
