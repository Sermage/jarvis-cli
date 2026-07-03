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

from app.answer_check import check_answer, is_dont_know
from app.retrieval_pipeline import RetrievalPipeline
from app.system_prompt import build_system_prompt, format_rag_block
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
from domain.retrieval import RetrievalConfig
from domain.working_memory import WorkingMemory
from infra.query_rewriter import LLMQueryRewriter
from infra.rag_retrieval import FaissOllamaRetrievalEngine
from infra.rerankers import HeuristicReranker, LLMReranker


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


def ask_history(client, params, messages, system_prompt):
    """Спросить модель, передав ВСЮ историю диалога (а не один вопрос).

    Именно так работает реальный чат jarvis (cli/main.py): краткосрочная память
    едет списком messages, долговременная/рабочая — в system_prompt. Для проверки
    «не теряет ли цель» это принципиально — модель должна видеть весь диалог.
    """
    try:
        return client.chat(messages, params, system_prompt)
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


def _first_expected_rank(chunks, expected_sources) -> int:
    """1-based позиция первого чанка из ожидаемого источника (0 = не найден).

    Детерминированная метрика качества retrieval: не зависит от генерации
    ответа (в отличие от keywords/judge), поэтому надёжно ловит эффект реранка.
    """
    for i, c in enumerate(chunks, 1):
        if any(e in c.source or c.source in e for e in expected_sources):
            return i
    return 0


def run_compare_modes(args, client, params, base_engine, questions, top_k):
    """Сравнить ступени улучшенного RAG на одном наборе вопросов.

    Четыре режима поверх одного базового поиска:
      baseline — как исходный RAG: top_k напрямую, без фильтра/реранка/rewrite;
      +filter  — fetch_k кандидатов → порог min_score → top_k;
      +rerank  — то же + переупорядочивание выбранным реранкером;
      improved — то же + query rewrite перед поиском.

    Основная метрика — детерминированные hit@k и MRR (ранг ожидаемого источника):
    они изолируют качество retrieval от шума генерации. keywords/judge считаются
    только с флагом судьи (--no-judge выключает генерацию ответов вовсе — тогда
    прогон быстрый, бесплатный и полностью воспроизводимый).
    """
    K, F, T, RR = top_k, args.fetch_k, args.min_score, args.reranker
    judged = not args.no_judge
    rerankers = {"heuristic": HeuristicReranker(), "llm": LLMReranker(client, params)}
    rewriter = LLMQueryRewriter(client, params)

    def pipe(fetch_k, min_score, reranker, rewrite):
        cfg = RetrievalConfig(top_k=K, fetch_k=fetch_k, min_score=min_score,
                              reranker=reranker, rewrite=rewrite)
        return RetrievalPipeline(base_engine, cfg, rewriter=rewriter, rerankers=rerankers)

    modes = [
        ("baseline", pipe(K, 0.0, "none", False)),
        ("+filter",  pipe(F, T, "none", False)),
        (f"+rerank({RR})", pipe(F, T, RR, False)),
        ("improved", pipe(F, T, RR, True)),
    ]

    print(f"Модель: {params['model']} · реранкер: {RR} · "
          f"fetch_k={F} · порог={T} · top_k={K} · "
          f"судья: {'да' if judged else 'нет (только retrieval)'}\n")
    print(f"Режимы: baseline (top_k напрямую) → +filter (порог) → "
          f"+rerank → improved (+ query rewrite)\n")

    agg = {name: {"hit": 0, "rr": 0.0, "kw": 0.0, "j": [], "n_chunks": 0}
           for name, _ in modes}
    for q in questions:
        qtext, exp = q["question"], q["expectation"]
        exp_src = q.get("expected_sources", [])
        exp_kw = q.get("expected_keywords", [])
        print(f"── {q['id']} ──  {qtext}")
        for name, pl in modes:
            chunks = pl.retrieve(qtext, top_k=K)
            rank = _first_expected_rank(chunks, exp_src)
            hit = rank > 0
            a = agg[name]
            a["hit"] += int(hit); a["rr"] += (1.0 / rank if rank else 0.0)
            a["n_chunks"] += len(chunks)
            srcs = ", ".join(sorted({c.source for c in chunks})) or "—"
            line = (f"   {name:<16} hit@{K}={'Y' if hit else '·'}  "
                    f"rank={rank or '—':<2} chunks={len(chunks):<2}")
            if judged:
                answer = ask(client, params, qtext, format_rag_block(chunks) if chunks else None)
                kw = keyword_coverage(answer, exp_kw)
                j = judge(client, params, qtext, exp, answer)
                a["kw"] += kw
                if j >= 0:
                    a["j"].append(j)
                line += f"  kw={kw:>4.0%}  judge={j}"
            print(line + f"  [{srcs}]")
        print()

    n = len(questions)
    print("=" * 70)
    print(f"СВОДКА ПО РЕЖИМАМ  ({n} вопросов, реранкер={RR})")
    header = f"  {'режим':<16} {'hit@k':>6} {'MRR':>6} {'ср.чанков':>10}"
    if judged:
        header += f" {'keywords':>9} {'judge':>7}"
    print(header)
    for name, _ in modes:
        a = agg[name]
        row = (f"  {name:<16} {a['hit']/n:>6.0%} {a['rr']/n:>6.2f} "
               f"{a['n_chunks']/n:>10.1f}")
        if judged:
            jv = f"{sum(a['j'])/len(a['j']):.2f}" if a["j"] else "—"
            row += f" {a['kw']/n:>9.0%} {jv:>7}"
        print(row)
    print("=" * 70)


def run_check_citations(args, client, params, base_engine, questions, top_k):
    """Проверить обязательные источники/цитаты + режим «не знаю» на N вопросах.

    Для каждого вопроса строится реальный prompt пути jarvis (с порогом min_score
    и обязательным форматом ответа), модель отвечает, и ответ проверяется
    ДЕТЕРМИНИРОВАННО (app/answer_check.py):
      • on-topic (найдены чанки) — есть источники, есть цитаты, каждая цитата
        дословно встречается в найденных чанках (grounded — ловит галлюцинации);
      • no-context (порог отсёк всё) — ассистент обязан сказать «не знаю» и не
        выдумывать источники.
    Вопрос с пустым expected_sources считается заведомо off-topic (ждём «не знаю»).
    """
    K, F, T = top_k, args.fetch_k, args.min_score
    rerankers = {"heuristic": HeuristicReranker(), "llm": LLMReranker(client, params)}
    rewriter = LLMQueryRewriter(client, params)
    cfg = RetrievalConfig(top_k=K, fetch_k=F, min_score=T,
                          reranker=args.reranker, rewrite=False)
    pipe = RetrievalPipeline(base_engine, cfg, rewriter=rewriter, rerankers=rerankers)

    print("Проверка: обязательные источники + цитаты + режим «не знаю»")
    print(f"Модель: {params['model']} · порог={T} · fetch_k={F} · top_k={K} · "
          f"реранкер={args.reranker}\n")

    def _trim(t):
        t = (t or "").strip()
        if args.answer_chars and len(t) > args.answer_chars:
            return t[:args.answer_chars].rstrip() + " …"
        return t

    on = {"n": 0, "src": 0, "cit": 0, "grounded": 0, "no_fab": 0}
    noctx = {"n": 0, "refused": 0}
    for q in questions:
        qtext = q["question"]
        intended_offtopic = not q.get("expected_sources", [])
        chunks = pipe.retrieve(qtext, top_k=K)
        sp = build_system_prompt(None, WorkingMemory(), _EmptyKnowledge(),
                                 retrieval_engine=pipe, user_query=qtext, top_k=K)
        answer = ask(client, params, qtext, sp)

        tag = "off-topic" if intended_offtopic else "on-topic "
        print(f"── {q['id']} ── [{tag}]  {qtext}")

        if not chunks:
            # Слабый контекст → обязателен отказ «не знаю».
            refused = is_dont_know(answer)
            chk = check_answer(answer, chunks)
            noctx["n"] += 1
            noctx["refused"] += int(refused and not chk.has_sources)
            verdict = "✔ сказал «не знаю»" if refused else "✗ НЕ отказался"
            extra = "  ⚠ привёл источники!" if chk.has_sources else ""
            print(f"   контекст пуст (порог {T}) → {verdict}{extra}")
        else:
            chk = check_answer(answer, chunks)
            on["n"] += 1
            on["src"] += int(chk.has_sources)
            on["cit"] += int(chk.has_citations)
            on["grounded"] += int(chk.citations_grounded)
            on["no_fab"] += int(chk.no_fabrication)
            srcs = ", ".join(sorted({c.source for c in chunks}))
            print(f"   источники={'✔' if chk.has_sources else '✗'}  "
                  f"цитаты={'✔' if chk.has_citations else '✗'} ({chk.n_citations})  "
                  f"дословно={chk.n_grounded}/{chk.n_citations}  "
                  f"из контекста={chk.n_from_context}/{chk.n_citations}  [{srcs}]")
            if chk.fabricated:
                print(f"   ⚠ выдумка (нет в контексте): {chk.fabricated}")
            elif chk.ungrounded:
                print(f"   ~ реконструкции (смысл из контекста, не дословно): {chk.ungrounded}")
        if args.show_answers:
            print("   " + _trim(answer).replace("\n", "\n   "))
        print()

    print("=" * 70)
    print("СВОДКА ПРОВЕРКИ ЦИТИРОВАНИЯ")
    if on["n"]:
        print(f"  on-topic вопросов: {on['n']}")
        print(f"    с источниками:            {on['src']}/{on['n']} ({on['src']/on['n']:.0%})")
        print(f"    с цитатами:               {on['cit']}/{on['n']} ({on['cit']/on['n']:.0%})")
        print(f"    цитаты дословны:          {on['grounded']}/{on['n']} ({on['grounded']/on['n']:.0%})")
        print(f"    без выдумок (из контекста): {on['no_fab']}/{on['n']} ({on['no_fab']/on['n']:.0%})")
    if noctx["n"]:
        print(f"  no-context вопросов: {noctx['n']}")
        print(f"    корректный отказ «не знаю»: {noctx['refused']}/{noctx['n']} "
              f"({noctx['refused']/noctx['n']:.0%})")
    print("=" * 70)


def run_conversation(args, client, params, base_engine, scenarios, top_k):
    """Прогнать длинные диалоги через реальный чат-пайплайн jarvis.

    Для каждого сценария:
      • рабочая память (task state) заводится из goal + constraints + notes;
      • история диалога `messages` растёт от хода к ходу и целиком передаётся
        модели (краткосрочная память), рабочая память + RAG-контекст — в
        system prompt (`build_system_prompt`, тот же путь, что в cli/main.py);
      • некоторые ходы дозаписывают в WM уточнения/термины (эмуляция /wm) —
        проверяем, что они не теряются к финальному probe.

    На каждом ходе детерминированно (app/conversation_check + app/answer_check):
      • цель (wm.task) инъектирована в промпт → память не вытеснена RAG-ом;
      • on-topic  → есть источники и цитаты;
      • off-topic → честное «не знаю» без источников;
      • probe     → ассистент вспомнил цель/ограничения (доля терминов ≥ порога).
    """
    from app.conversation_check import evaluate_turn

    K, F, T = top_k, args.fetch_k, args.min_score
    rerankers = {"heuristic": HeuristicReranker(), "llm": LLMReranker(client, params)}
    rewriter = LLMQueryRewriter(client, params)
    cfg = RetrievalConfig(top_k=K, fetch_k=F, min_score=T,
                          reranker=args.reranker, rewrite=False)
    pipe = RetrievalPipeline(base_engine, cfg, rewriter=rewriter, rerankers=rerankers)

    print("Проверка: мини-чат с RAG + источники + память задачи (длинные диалоги)")
    print(f"Модель: {params['model']} · порог={T} · fetch_k={F} · top_k={K} · "
          f"реранкер={args.reranker}\n")

    def _trim(t):
        t = (t or "").strip()
        if args.answer_chars and len(t) > args.answer_chars:
            return t[:args.answer_chars].rstrip() + " …"
        return t

    grand = {"turns": 0, "passed": 0, "goal": 0,
             "on": 0, "on_src": 0, "off": 0, "off_ok": 0, "probe": 0, "probe_ok": 0}

    for scen in scenarios:
        wm = WorkingMemory(task=scen["goal"],
                           context=dict(scen.get("constraints", {})),
                           notes=list(scen.get("notes", [])))
        messages: list = []
        print("=" * 70)
        print(f"СЦЕНАРИЙ {scen['id']}: {scen['title']}")
        print(f"  цель: {wm.task}")
        print(f"  ограничения: {', '.join(f'{k}={v}' for k, v in wm.context.items())}\n")

        s = {"turns": 0, "passed": 0, "goal": 0}
        for turn in scen["turns"]:
            # Эмуляция ручного /wm: ход может зафиксировать термин/уточнение.
            if turn.get("set_ctx"):
                wm.context.update(turn["set_ctx"])
            if turn.get("set_note"):
                wm.notes.append(turn["set_note"])

            u = turn["user"]
            messages.append({"role": "user", "content": u})
            sp = build_system_prompt(None, wm, _EmptyKnowledge(),
                                     retrieval_engine=pipe, user_query=u, top_k=K)
            chunks = pipe.retrieve(u, top_k=K)
            answer = ask_history(client, params, messages, sp)
            messages.append({"role": "assistant", "content": answer})

            v = evaluate_turn(turn, answer, chunks, sp, wm)
            s["turns"] += 1
            s["passed"] += int(v.passed)
            s["goal"] += int(v.goal_injected)
            grand["turns"] += 1
            grand["passed"] += int(v.passed)
            grand["goal"] += int(v.goal_injected)

            mark = "✔" if v.passed else "✗"
            goal = "цель✔" if v.goal_injected else "цель✗"
            print(f"── {v.turn_id} [{v.kind}] {goal} ── {u}")
            if v.kind == "off-topic":
                grand["off"] += 1
                grand["off_ok"] += int(v.passed)
                verdict = "сказал «не знаю»" if v.refused else "НЕ отказался"
                extra = "  ⚠ привёл источники!" if v.answer_check and v.answer_check.has_sources else ""
                print(f"   {mark} {verdict}{extra}")
            elif v.kind == "probe":
                grand["probe"] += 1
                grand["probe_ok"] += int(v.passed)
                print(f"   {mark} вспомнил {v.recall:.0%} терминов цели/ограничений")
            else:  # on-topic
                grand["on"] += 1
                chk = v.answer_check
                if not v.hit_context:
                    print(f"   {mark} контекст пуст → "
                          f"{'«не знаю» (ок)' if v.refused else 'нет отказа (провал)'}")
                else:
                    grand["on_src"] += int(bool(chk and chk.has_sources and chk.has_citations))
                    srcs = ", ".join(sorted({c.source for c in chunks}))
                    print(f"   {mark} источники={'✔' if chk.has_sources else '✗'}  "
                          f"цитаты={'✔' if chk.has_citations else '✗'} ({chk.n_citations})  "
                          f"дословно={chk.n_grounded}/{chk.n_citations}  [{srcs}]")
                    if chk.fabricated:
                        print(f"   ⚠ выдумка: {chk.fabricated}")
            if args.show_answers:
                print("   " + _trim(answer).replace("\n", "\n   "))
            print()

        print(f"  ИТОГ {scen['id']}: ходов {s['turns']}, прошло {s['passed']}/{s['turns']} "
              f"({s['passed']/s['turns']:.0%}), цель в промпте {s['goal']}/{s['turns']} "
              f"({s['goal']/s['turns']:.0%})\n")

    print("=" * 70)
    print("СВОДКА ПО ДИАЛОГАМ")
    g = grand
    print(f"  всего ходов:              {g['turns']}")
    print(f"  прошло проверку:          {g['passed']}/{g['turns']} ({g['passed']/g['turns']:.0%})")
    print(f"  цель не потеряна (в промпте): {g['goal']}/{g['turns']} ({g['goal']/g['turns']:.0%})")
    if g["on"]:
        print(f"  on-topic с источниками:   {g['on_src']}/{g['on']} ({g['on_src']/g['on']:.0%})")
    if g["off"]:
        print(f"  off-topic → «не знаю»:    {g['off_ok']}/{g['off']} ({g['off_ok']/g['off']:.0%})")
    if g["probe"]:
        print(f"  probe → цель вспомнена:   {g['probe_ok']}/{g['probe']} ({g['probe_ok']/g['probe']:.0%})")
    print("=" * 70)


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
    ap.add_argument("--compare-modes", action="store_true",
                    help="сравнить ступени RAG: baseline → +filter → +rerank → improved")
    ap.add_argument("--check-citations", action="store_true",
                    help="проверить обязательные источники/цитаты + режим «не знаю»")
    ap.add_argument("--conversation", action="store_true",
                    help="прогнать длинные диалоги (мини-чат + память задачи + источники)")
    ap.add_argument("--scenarios", default="scenarios.json",
                    help="файл со сценариями диалогов (для --conversation)")
    ap.add_argument("--reranker", choices=["heuristic", "llm"], default="heuristic",
                    help="реранкер для режимов +rerank/improved (--compare-modes)")
    ap.add_argument("--fetch-k", type=int, default=20,
                    help="сколько кандидатов брать до фильтра/реранка (--compare-modes)")
    ap.add_argument("--min-score", type=float, default=0.4,
                    help="порог отсечения по близости для +filter/+rerank (--compare-modes)")
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

    # Режим сравнения ступеней улучшенного RAG (второй этап задания).
    if args.compare_modes:
        run_compare_modes(args, client, params, engine, questions, rag_cfg.top_k)
        return

    # Проверка обязательных источников/цитат + режима «не знаю» (третий этап).
    if args.check_citations:
        run_check_citations(args, client, params, engine, questions, rag_cfg.top_k)
        return

    # Мини-чат: длинные диалоги с памятью задачи + источниками (четвёртый этап).
    if args.conversation:
        spath = args.scenarios if os.path.isabs(args.scenarios) \
            else os.path.join(os.path.dirname(__file__), args.scenarios)
        with open(spath, encoding="utf-8") as f:
            scenarios = json.load(f)
        if args.limit:
            scenarios = scenarios[:args.limit]
        run_conversation(args, client, params, engine, scenarios, rag_cfg.top_k)
        return

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
