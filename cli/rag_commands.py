"""CLI-обработчик /rag — переключение, статус и настройка ступеней RAG.

Пайплайн читает настройки из RetrievalConfig в момент запроса, поэтому здесь
достаточно менять поля конфига — они применятся к следующему же вопросу без
пересборки движка.
"""
from __future__ import annotations

from typing import Optional

from app.ports import RetrievalEngine
from cli.ansi import BOLD, CYAN, DIM, GREEN, RESET, YELLOW
from domain.retrieval import RERANKERS, RetrievalConfig


def _print_status(cfg: RetrievalConfig, ready: bool) -> None:
    state = f"{GREEN}включён{RESET}" if cfg.enabled else f"{DIM}выключен{RESET}"
    ready_txt = f"{GREEN}готов{RESET}" if ready else f"{YELLOW}не готов{RESET}"
    rw = f"{GREEN}on{RESET}" if cfg.rewrite else f"{DIM}off{RESET}"
    thr = f"{cfg.min_score:.2f}" if cfg.min_score > 0 else f"{DIM}выкл{RESET}"
    print(f"\n{BOLD}RAG:{RESET}")
    print(f"  Режим:      {state}")
    print(f"  Индекс:     {cfg.index_path}  ({ready_txt})")
    print(f"  Стратегия:  {cfg.strategy}")
    print(f"  Конвейер:   rewrite={rw}  →  fetch_k={cfg.fetch_k}  →  "
          f"порог={thr}  →  реранк={CYAN}{cfg.reranker}{RESET}  →  top_k={cfg.top_k}")
    print()


def _set_int(cur: int, arg: str, lo: int, label: str) -> int:
    try:
        v = int(arg)
    except ValueError:
        print(f"{YELLOW}  {label}: нужно целое число.{RESET}")
        return cur
    if v < lo:
        print(f"{YELLOW}  {label}: минимум {lo}.{RESET}")
        return cur
    print(f"{GREEN}  {label} = {v}{RESET}")
    return v


def handle_rag(cmd_str: str,
               rag_config: RetrievalConfig,
               engine: Optional[RetrievalEngine]) -> None:
    """Обрабатывает /rag <sub>: on · off · status · reranker · rewrite · threshold · fetchk · topk."""
    parts = cmd_str.split()
    sub = parts[1].lower() if len(parts) > 1 else "status"
    arg = parts[2].strip() if len(parts) > 2 else ""

    ready = engine is not None and engine.is_ready()

    if sub == "on":
        if not ready:
            print(f"{YELLOW}  Индекс не готов — включить RAG нельзя.{RESET}")
            print(f"{DIM}  Проверь путь: {rag_config.index_path} "
                  f"(файлы {rag_config.strategy}.faiss / .meta.json) "
                  f"и что установлены faiss-cpu, numpy.{RESET}")
            return
        rag_config.enabled = True
        print(f"{GREEN}  RAG включён.{RESET} {DIM}реранк={rag_config.reranker}, "
              f"top_k={rag_config.top_k}, стратегия={rag_config.strategy}{RESET}")

    elif sub == "off":
        rag_config.enabled = False
        print(f"{GREEN}  RAG выключен.{RESET} {DIM}(обычный чат без базы){RESET}")

    elif sub == "reranker":
        if arg not in RERANKERS:
            print(f"{YELLOW}  Реранкер: {CYAN}{' · '.join(RERANKERS)}{RESET}")
        else:
            rag_config.reranker = arg
            print(f"{GREEN}  Реранкер = {arg}{RESET}")

    elif sub == "rewrite":
        if arg in ("on", "off"):
            rag_config.rewrite = (arg == "on")
            print(f"{GREEN}  Query rewrite = {arg}{RESET}")
        else:
            print(f"{YELLOW}  /rag rewrite {CYAN}on · off{RESET}")

    elif sub in ("threshold", "min-score", "порог"):
        try:
            rag_config.min_score = float(arg)
            print(f"{GREEN}  Порог min_score = {rag_config.min_score:.2f}{RESET} "
                  f"{DIM}(0 = фильтр выключен){RESET}")
        except ValueError:
            print(f"{YELLOW}  /rag threshold <число>, напр. 0.45 (0 — выключить){RESET}")

    elif sub in ("fetchk", "fetch-k"):
        rag_config.fetch_k = _set_int(rag_config.fetch_k, arg, 1, "fetch_k")

    elif sub == "topk":
        rag_config.top_k = _set_int(rag_config.top_k, arg, 1, "top_k")

    elif sub in ("status", ""):
        _print_status(rag_config, ready)

    else:
        print(f"{YELLOW}  Подкоманды /rag: {CYAN}on · off · status · "
              f"reranker · rewrite · threshold · fetchk · topk{RESET}")
