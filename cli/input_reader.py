"""Bracketed-paste-aware ввод для REPL.

Без bracketed-paste терминал отправляет вставленный многострочный текст
как последовательность keystrokes, и каждый `\\n` внутри пасты возвращает
управление `input()` — то есть каждый кусок «уходит на отправку»
автоматически. Это раздражает: чтобы вставить промпт из нескольких строк,
приходится клеить руками.

Решение из xterm-спеки: терминалу включают режим
`\\e[?2004h` — он оборачивает любой paste-блок маркерами
`\\e[200~ ... \\e[201~`. Здесь мы:
  1) включаем режим при старте REPL,
  2) в обёртке над `input()` детектим эти маркеры,
  3) накапливаем все строки пасты в одну, пока не увидим `\\e[201~`,
  4) возвращаем итоговую многострочную строку только после того,
     как пользователь сам нажмёт Enter.

В обычном вводе (когда человек просто печатает) обёртка прозрачна —
input() работает как раньше, со всей нативной поддержкой readline.
"""
from __future__ import annotations

import sys
from typing import Optional


PASTE_START = "\x1b[200~"
PASTE_END   = "\x1b[201~"

# ANSI-команды управления bracketed paste mode из xterm.
_ENABLE_SEQ  = "\x1b[?2004h"
_DISABLE_SEQ = "\x1b[?2004l"


def enable_bracketed_paste(stream=None) -> None:
    """Включить bracketed paste mode в активном терминале."""
    s = stream or sys.stdout
    try:
        s.write(_ENABLE_SEQ)
        s.flush()
    except Exception:
        # Не-TTY (например, в тестах с redirected stdout) — молча игнорим.
        pass


def disable_bracketed_paste(stream=None) -> None:
    """Выключить bracketed paste mode (вернуть терминал в дефолт)."""
    s = stream or sys.stdout
    try:
        s.write(_DISABLE_SEQ)
        s.flush()
    except Exception:
        pass


def read_input(prompt: str,
               _input=input,
               _continuation_prompt: str = "") -> str:
    """Вариант `input()`, склеивающий bracketed-paste в одну строку.

    Возвращает обычную строку без управляющих маркеров. Если терминал не
    шлёт маркеры (потому что не поддерживает paste mode или мы не в TTY) —
    ведёт себя как обычный `input()`.

    `_input` инжектируется для тестов (заменяем сразу всё чтение).
    """
    line = _input(prompt)

    if PASTE_START not in line:
        # Терминал может отослать одинокий END после короткой пасты —
        # подчищаем и возвращаем как есть.
        return line.replace(PASTE_END, "")

    # PASTE_START может стоять не в начале (если человек уже что-то набрал).
    before, _, after_start = line.partition(PASTE_START)

    # Если END уже в той же физической строке — это однострочная вставка.
    if PASTE_END in after_start:
        inside, _, _trailing = after_start.partition(PASTE_END)
        return before + inside

    # Многострочная: продолжаем читать пока не встретим маркер конца.
    buf = before + after_start
    while True:
        try:
            cont = _input(_continuation_prompt)
        except EOFError:
            # Пользователь нажал Ctrl-D в середине пасты — отдадим, что собрали.
            return buf
        if PASTE_END in cont:
            head, _, _tail = cont.partition(PASTE_END)
            return buf + "\n" + head
        buf += "\n" + cont
