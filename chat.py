#!/usr/bin/env python3
"""Entrypoint Jarvis CLI.

Запуск:
    python3 chat.py
    jarvis              # если установлен симлинк в /usr/local/bin

Вся логика — в пакетах domain/, app/, infra/, cli/. См. CLAUDE.md.
"""

# Если в системе есть пакет `gnureadline` — подменяем им встроенный readline
# ДО любых других импортов. На macOS Python линкуется с libedit, который
# не подавляет bracketed-paste-маркеры в эхе. С GNU readline 8+ пасты
# атомарны, маркеры не видны, история по стрелке вверх работает как в
# bash/zsh. Если пакета нет — обходимся ручным детектором из
# cli/input_reader.py (функционально работает, но markers будут видны).
#
# Просто `import gnureadline` не помогает: input() использует C-уровневый
# PyOS_Readline-хук, который ставится при первой загрузке модуля под именем
# `readline`. Поэтому пропихиваем через sys.modules.
import sys as _sys
try:  # pragma: no cover — зависит от наличия пакета
    _sys.modules["readline"] = __import__("gnureadline")
except ImportError:
    pass

from cli.main import main


if __name__ == "__main__":
    main()
