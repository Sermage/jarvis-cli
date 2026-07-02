"""Сравнение качества ответов jarvis с RAG и без RAG на контрольном наборе.

Для каждого вопроса из questions.json:
  • no-RAG  — вопрос уходит в LLM как есть (без контекста из базы);
  • RAG     — к вопросу подмешиваются найденные в индексе фрагменты.

Метрики:
  • hit@k   — попал ли ожидаемый источник в найденные чанки (только RAG);
  • keywords— доля ожидаемых ключевых слов, встретившихся в ответе;
  • judge   — оценка LLM-судьи 0..2 ответа против «ожидания» (та же модель).

Запуск (из корня jarvis, в venv с extra [rag]):
    ./.venv/bin/python examples/rag_eval/evaluate.py
    ./.venv/bin/python examples/rag_eval/evaluate.py --no-judge --limit 3
Требуется: запущенный Ollama, собранный индекс и ключ провайдера в .env.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# --- сделать пакеты jarvis импортируемыми при запуске «из файла» ---
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from app.system_prompt import build_system_prompt
from cli.config import (
    DEFAULT_EMBED_MODEL,
    DEFAULT_OLLAMA_URL,
    DEFAULT_PARAMS,
    default_model_for,
    load_env,
    load_rag_config,
    resolve_provider,
)
from cli.main import _build_client
from domain.working_memory import WorkingMemory
from infra.rag_retrieval import FaissOllamaRetrievalEngine


class _EmptyKnowledge:
    """Пустая долговременная память — чтобы no-RAG был честно «голым»."""
    def all_as_prompt(self) -> str:
        return ""
    def list_names(self): return []
    def load(self, name): return None
    def save(self, entry): pass


_JUDGE_SYSTEM = (
    "Ты — строгий проверяющий. Оцени ОТВЕТ относительно ЭТАЛОННОГО ОЖИДАНИЯ "
    "по шкале: 0 — неверно или не по теме, 1 — частично верно/неполно, "
    "2 — верно и полно. Верни РОВНО одну цифру: 0, 1 или 2."
)


def judge(client, params, question: str, expectation: str, answer: str) -> int:
    if not answer.strip():
        return 0
    msg = (f"ВОПРОС:\n{question}\n\nЭТАЛОННОЕ ОЖИДАНИЕ:\n{expectation}\n\n"
           f"ОТВЕТ МОДЕЛИ:\n{answer}\n\nОценка (0, 1 или 2):")
    try:
        reply = client.chat([{"role": "user", "content": msg}], params, _JUDGE_SYSTEM)
    except Exception as e:
        print(f"    [judge error: {e}]")
        return -1
    for ch in reply:
        if ch in "012":
            return int(ch)
    return -1


def keyword_coverage(answer: str, keywords: list) -> float:
    if not keywords:
        return 0.0
    low = answer.lower()
    hit = sum(1 for k in keywords if k.lower() in low)
    return hit / len(keywords)


def source_hit(chunks, expected_sources: list) -> bool:
    got = {c.source for c in chunks}
    for exp in expected_sources:
        if any(exp in g or g in exp for g in got):
            return True
    return False


def ask(client, params, question: str, system_prompt):
    try:
        return client.chat([{"role": "user", "content": question}], params, system_prompt)
    except Exception as e:
        return f"[ошибка запроса: {e}]"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-judge", action="store_true", help="не звать LLM-судью")
    ap.add_argument("--limit", type=int, default=0, help="ограничить число вопросов")
    ap.add_argument("--index-path", default="", help="каталог индекса (по умолчанию из .env)")
    ap.add_argument("--strategy", default="", help="стратегия чанкинга (fixed|structural)")
    ap.add_argument("--questions", default="questions.json",
                    help="файл с контрольными вопросами (относительно этой папки или абс. путь)")
    args = ap.parse_args()

    load_env(os.path.join(REPO_ROOT, ".env"))

    provider = resolve_provider(os.environ.get("LLM_PROVIDER", ""))
    params = dict(DEFAULT_PARAMS)
    params["model"] = default_model_for(provider)
    params["temperature"] = 0  # детерминизм для сравнения
    client = _build_client(provider)

    rag_cfg = load_rag_config()
    index_path = os.path.expanduser(args.index_path) if args.index_path else rag_cfg.index_path
    strategy = args.strategy or rag_cfg.strategy
    engine = FaissOllamaRetrievalEngine(
        index_path=index_path, strategy=strategy,
        embed_model=DEFAULT_EMBED_MODEL, ollama_url=DEFAULT_OLLAMA_URL,
    )
    if not engine.is_ready():
        print(f"[!] Индекс не готов: {index_path} "
              f"({strategy}.faiss/.meta.json). Собери индекс и проверь Ollama.")
        sys.exit(1)

    qpath = args.questions if os.path.isabs(args.questions) \
        else os.path.join(os.path.dirname(__file__), args.questions)
    with open(qpath, encoding="utf-8") as f:
        questions = json.load(f)
    if args.limit:
        questions = questions[:args.limit]

    print(f"Провайдер: {provider} · модель: {params['model']} · "
          f"индекс: {index_path} [{strategy}] (top_k={rag_cfg.top_k})\n")

    rows = []
    for q in questions:
        qtext, exp = q["question"], q["expectation"]
        print(f"── {q['id']} ──")
        print(f"Q: {qtext}")

        # no-RAG: пустой контекст
        sp_norag = build_system_prompt(None, WorkingMemory(), _EmptyKnowledge())
        a_norag = ask(client, params, qtext, sp_norag)

        # RAG: контекст из индекса
        chunks = engine.retrieve(qtext, top_k=rag_cfg.top_k)
        sp_rag = build_system_prompt(None, WorkingMemory(), _EmptyKnowledge(),
                                     retrieval_engine=engine, user_query=qtext,
                                     top_k=rag_cfg.top_k)
        a_rag = ask(client, params, qtext, sp_rag)

        hit = source_hit(chunks, q.get("expected_sources", []))
        kw_norag = keyword_coverage(a_norag, q.get("expected_keywords", []))
        kw_rag = keyword_coverage(a_rag, q.get("expected_keywords", []))
        j_norag = j_rag = None
        if not args.no_judge:
            j_norag = judge(client, params, qtext, exp, a_norag)
            j_rag = judge(client, params, qtext, exp, a_rag)

        srcs = ", ".join(sorted({c.source for c in chunks})) or "—"
        print(f"  retrieval: hit@{rag_cfg.top_k}={'ДА' if hit else 'нет'}  [{srcs}]")
        print(f"  keywords:  no-RAG={kw_norag:.0%}  RAG={kw_rag:.0%}")
        if not args.no_judge:
            print(f"  judge:     no-RAG={j_norag}  RAG={j_rag}")
        print()

        rows.append({"id": q["id"], "hit": hit,
                     "kw_norag": kw_norag, "kw_rag": kw_rag,
                     "j_norag": j_norag, "j_rag": j_rag})

    # ── сводка ───────────────────────────────────────────────────────────────
    n = len(rows)
    hit_rate = sum(1 for r in rows if r["hit"]) / n
    avg_kw_norag = sum(r["kw_norag"] for r in rows) / n
    avg_kw_rag = sum(r["kw_rag"] for r in rows) / n
    print("=" * 52)
    print("СВОДКА")
    print(f"  вопросов:                {n}")
    print(f"  retrieval hit@{rag_cfg.top_k}:        {hit_rate:.0%}")
    print(f"  keywords  no-RAG / RAG:  {avg_kw_norag:.0%} / {avg_kw_rag:.0%}")
    if not args.no_judge:
        vn = [r["j_norag"] for r in rows if r["j_norag"] is not None and r["j_norag"] >= 0]
        vr = [r["j_rag"] for r in rows if r["j_rag"] is not None and r["j_rag"] >= 0]
        if vn and vr:
            print(f"  judge     no-RAG / RAG:  "
                  f"{sum(vn)/len(vn):.2f} / {sum(vr)/len(vr):.2f}  (из 2.0)")
    print("=" * 52)


if __name__ == "__main__":
    main()
