#!/usr/bin/env python3
"""Entrypoint Jarvis CLI.

Запуск:
    python3 chat.py
    jarvis              # если установлен симлинк в /usr/local/bin

Вся логика — в пакетах domain/, app/, infra/, cli/. См. CLAUDE.md.
"""
from cli.main import main


if __name__ == "__main__":
    main()
