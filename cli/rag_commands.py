"""CLI-обработчик /rag — переключение и статус RAG-режима."""
from __future__ import annotations

from typing import Optional

from app.ports import RetrievalEngine
from cli.ansi import BOLD, CYAN, DIM, GREEN, RESET, YELLOW
from domain.retrieval import RetrievalConfig


def handle_rag(cmd_str: str,
               rag_config: RetrievalConfig,
               engine: Optional[RetrievalEngine]) -> None:
    """Обрабатывает /rag <sub>: on · off · status."""
    parts = cmd_str.split(None, 1)
    sub = parts[1].lower().strip() if len(parts) > 1 else "status"

    ready = engine is not None and engine.is_ready()

    if sub == "on":
        if engine is None or not ready:
            print(f"{YELLOW}  Индекс не готов — включить RAG нельзя.{RESET}")
            print(f"{DIM}  Проверь путь: {rag_config.index_path} "
                  f"(файлы {rag_config.strategy}.faiss / .meta.json) "
                  f"и что установлены faiss-cpu, numpy.{RESET}")
            return
        rag_config.enabled = True
        print(f"{GREEN}  RAG включён.{RESET} {DIM}top_k={rag_config.top_k}, "
              f"стратегия={rag_config.strategy}{RESET}")

    elif sub == "off":
        rag_config.enabled = False
        print(f"{GREEN}  RAG выключен.{RESET} {DIM}(обычный чат без базы){RESET}")

    elif sub in ("status", ""):
        state = f"{GREEN}включён{RESET}" if rag_config.enabled else f"{DIM}выключен{RESET}"
        ready_txt = f"{GREEN}готов{RESET}" if ready else f"{YELLOW}не готов{RESET}"
        print(f"\n{BOLD}RAG:{RESET}")
        print(f"  Режим:     {state}")
        print(f"  Индекс:    {rag_config.index_path}  ({ready_txt})")
        print(f"  Стратегия: {rag_config.strategy}")
        print(f"  top_k:     {rag_config.top_k}")
        print()

    else:
        print(f"{YELLOW}  Подкоманды /rag: {CYAN}on · off · status{RESET}")
