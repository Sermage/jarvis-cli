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


_FULL_CTX_HEADER = (
    "[ИСХОДНЫЙ КОД РЕПОЗИТОРИЯ jarvis]\n"
    "Ниже приведён исходный код проекта (возможно, обрезан по лимиту). "
    "Отвечай на вопрос, опираясь на него, и ссылайся на файлы.\n\n"
)


def build_full_context(meta_path: str, budget_chars: int):
    """Склеить весь корпус из meta.json в один промпт до лимита символов.

    Возвращает (prompt, used_chars, total_chars, files_used, files_total).
    Файлы идут целиком (чанки одного файла лежат подряд); при переполнении
    лимита оставшиеся файлы отбрасываются — это и есть предел full-context.
    """
    with open(meta_path, encoding="utf-8") as f:
        metas = json.load(f)
    # собрать текст по файлам в порядке появления
    by_file, order = {}, []
    for m in metas:
        src = m.get("source", "?")
        if src not in by_file:
            by_file[src] = []
            order.append(src)
        by_file[src].append(m.get("text", ""))

    total = sum(len(t) for src in order for t in by_file[src])
    parts, used, files_used = [], 0, 0
    for src in order:
        block = f"### FILE: {src}\n" + "\n".join(by_file[src]) + "\n\n"
        if used + len(block) > budget_chars:
            break
        parts.append(block)
        used += len(block)
        files_used += 1
    return _FULL_CTX_HEADER + "".join(parts), used, total, files_used, len(order)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-judge", action="store_true", help="не звать LLM-судью")
    ap.add_argument("--limit", type=int, default=0, help="ограничить число вопросов")
    ap.add_argument("--index-path", default="", help="каталог индекса (по умолчанию из .env)")
    ap.add_argument("--strategy", default="", help="стратегия чанкинга (fixed|structural)")
    ap.add_argument("--questions", default="questions.json",
                    help="файл с контрольными вопросами (относительно этой папки или абс. путь)")
    ap.add_argument("--show-answers", action="store_true",
                    help="печатать полные ответы обоих режимов (вопрос → ответ)")
    ap.add_argument("--answer-chars", type=int, default=700,
                    help="обрезать печатаемый ответ до N символов (0 = не обрезать)")
    ap.add_argument("--baseline", choices=["empty", "full"], default="empty",
                    help="empty — вопрос без контекста; full — весь репозиторий в промпт")
    ap.add_argument("--context-chars", type=int, default=200000,
                    help="лимит символов full-context промпта (грубо ~64K токенов)")
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

    # Базовый режim: пустой промпт или весь репозиторий (full-context).
    base_label = "full-context" if args.baseline == "full" else "без RAG"
    full_ctx_prompt = None
    base_ctx_chars = 0
    if args.baseline == "full":
        meta_path = os.path.join(index_path, f"{strategy}.meta.json")
        full_ctx_prompt, used, total, fu, ft = build_full_context(meta_path, args.context_chars)
        base_ctx_chars = used
        trunc = "" if used >= total else f", ОБРЕЗАНО до {used*100//total}% ({fu}/{ft} файлов)"
        print(f"[baseline=full] корпус {total} симв (~{total//4} ток); "
              f"в промпт вошло {used} симв (~{used//4} ток){trunc}")

    print(f"Провайдер: {provider} · модель: {params['model']} · "
          f"индекс: {index_path} [{strategy}] (top_k={rag_cfg.top_k})\n")

    rows = []
    for q in questions:
        qtext, exp = q["question"], q["expectation"]
        print(f"── {q['id']} ──")
        print(f"Q: {qtext}")

        # baseline: пустой контекст или весь репозиторий
        if args.baseline == "full":
            sp_norag = full_ctx_prompt
        else:
            sp_norag = build_system_prompt(None, WorkingMemory(), _EmptyKnowledge())
        a_norag = ask(client, params, qtext, sp_norag)

        # RAG: контекст из индекса
        chunks = engine.retrieve(qtext, top_k=rag_cfg.top_k)
        rag_ctx_chars = sum(len(c.text) for c in chunks)
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

        if args.show_answers:
            def _trim(t):
                t = t.strip()
                if args.answer_chars and len(t) > args.answer_chars:
                    return t[:args.answer_chars].rstrip() + " …"
                return t
            print(f"\n  ── {base_label} "
                  f"(judge={j_norag if not args.no_judge else '—'}, kw={kw_norag:.0%}) ──")
            print("  " + _trim(a_norag).replace("\n", "\n  "))
            print(f"\n  ── с RAG "
                  f"(judge={j_rag if not args.no_judge else '—'}, kw={kw_rag:.0%}) ──")
            print("  " + _trim(a_rag).replace("\n", "\n  "))
            print()

        base_chars = base_ctx_chars if args.baseline == "full" else 0
        print(f"  retrieval: hit@{rag_cfg.top_k}={'ДА' if hit else 'нет'}  [{srcs}]")
        print(f"  context:   {base_label}={base_chars} симв  RAG={rag_ctx_chars} симв")
        print(f"  keywords:  {base_label}={kw_norag:.0%}  RAG={kw_rag:.0%}")
        if not args.no_judge:
            print(f"  judge:     {base_label}={j_norag}  RAG={j_rag}")
        print()

        rows.append({"id": q["id"], "hit": hit,
                     "kw_norag": kw_norag, "kw_rag": kw_rag,
                     "j_norag": j_norag, "j_rag": j_rag,
                     "base_chars": base_chars, "rag_chars": rag_ctx_chars})

    # ── сводка ───────────────────────────────────────────────────────────────
    n = len(rows)
    hit_rate = sum(1 for r in rows if r["hit"]) / n
    avg_kw_norag = sum(r["kw_norag"] for r in rows) / n
    avg_kw_rag = sum(r["kw_rag"] for r in rows) / n
    avg_base_ch = sum(r["base_chars"] for r in rows) / n
    avg_rag_ch = sum(r["rag_chars"] for r in rows) / n
    print("=" * 56)
    print(f"СВОДКА  (baseline = {base_label})")
    print(f"  вопросов:                     {n}")
    print(f"  retrieval hit@{rag_cfg.top_k}:             {hit_rate:.0%}")
    print(f"  context симв  {base_label} / RAG: {avg_base_ch:.0f} / {avg_rag_ch:.0f}  "
          f"(~{avg_base_ch/4:.0f} / ~{avg_rag_ch/4:.0f} ток)")
    print(f"  keywords      {base_label} / RAG: {avg_kw_norag:.0%} / {avg_kw_rag:.0%}")
    if not args.no_judge:
        vn = [r["j_norag"] for r in rows if r["j_norag"] is not None and r["j_norag"] >= 0]
        vr = [r["j_rag"] for r in rows if r["j_rag"] is not None and r["j_rag"] >= 0]
        if vn and vr:
            print(f"  judge (0-2)   {base_label} / RAG: "
                  f"{sum(vn)/len(vn):.2f} / {sum(vr)/len(vr):.2f}")
    print("=" * 56)


if __name__ == "__main__":
    main()
