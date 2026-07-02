"""Реранкеры второго этапа RAG — реализации порта `app.ports.Reranker`.

Косинусный поиск по FAISS (bi-encoder) сжимает смысл чанка в один вектор ещё
до того, как увидел запрос, поэтому нужный фрагмент нередко оказывается ниже по
рангу. Реранкер пересматривает порядок кандидатов более точным сигналом:

    HeuristicReranker — без модели: косинус × лексическое совпадение терминов
                        + MMR (штраф за дубли). Детерминированный, бесплатный,
                        хорош для кода с точными идентификаторами.
    LLMReranker       — cross-encoder на LLM: модель видит запрос и фрагмент
                        вместе и оценивает релевантность. Точнее на перефразе,
                        но платный (один запрос к модели на весь список).

Обе реализации при пустом входе или ошибке ведут себя предсказуемо: возвращают
исходный порядок, а не падают, — реранк не должен ломать основной ответ.
"""
from __future__ import annotations

import re
from typing import Any, Callable, List, Optional

from domain.retrieval import RetrievedChunk

# Токены: латиница/кириллица/цифры/подчёркивание. Идентификаторы вида
# guarded_chat матчатся и целиком, и по частям (см. _tokens).
_TOKEN_RE = re.compile(r"[0-9a-zа-яё_]+", re.IGNORECASE)

# Мелкие незначащие слова — чтобы «как что в и на» не давали ложных совпадений.
_STOP = {
    "the", "a", "an", "of", "to", "in", "is", "and", "or", "how", "what", "for",
    "on", "with", "does", "do",
    "как", "что", "в", "и", "на", "с", "по", "для", "это", "же", "ли", "от", "до",
    "за", "из", "при", "где", "когда", "какой", "какие", "чем", "то", "так", "а",
    "но", "или", "если", "чтобы", "быть",
}


def _tokens(text: str) -> List[str]:
    """Значащие токены нижним регистром; snake_case дробится и на части."""
    out: List[str] = []
    for t in _TOKEN_RE.findall((text or "").lower()):
        if len(t) < 2 or t in _STOP:
            continue
        out.append(t)
        if "_" in t:  # guarded_chat → ещё и guarded, chat: помогает матчить идентификаторы
            out.extend(p for p in t.split("_") if len(p) >= 2)
    return out


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class HeuristicReranker:
    """Лексический реранк без модели: косинус × совпадение терминов + MMR.

    Релевантность = cosine_weight·cos̃ + lexical_weight·(доля терминов запроса,
    встретившихся в чанке), где cos̃ — косинус, перенормированный из [-1,1] в
    [0,1]. Затем MMR (Maximal Marginal Relevance) отбирает чанки жадно, штрафуя
    те, что дублируют уже выбранные (по пересечению токенов), — чтобы top_k был
    разнообразным, а не пятью пересказами одного абзаца. Полностью детерминирован.
    """

    def __init__(self,
                 cosine_weight: float = 0.65,
                 lexical_weight: float = 0.35,
                 mmr_lambda: float = 0.9):
        self._cw = cosine_weight
        self._lw = lexical_weight
        self._lam = mmr_lambda

    def rerank(self, query: str, chunks: List[RetrievedChunk]) -> List[RetrievedChunk]:
        if not chunks:
            return []
        q = set(_tokens(query))
        # (релевантность, множество токенов чанка, чанк)
        scored = []
        for ch in chunks:
            cset = set(_tokens(ch.text) + _tokens(ch.section) + _tokens(ch.source))
            lex = (len(q & cset) / len(q)) if q else 0.0
            cos = (ch.score + 1.0) / 2.0  # [-1,1] → [0,1]
            rel = self._cw * cos + self._lw * lex
            scored.append((rel, cset, ch))

        # MMR: жадно набираем, вычитая максимальное сходство с уже выбранными.
        selected = []
        remaining = list(scored)
        while remaining:
            best_i, best_val = 0, None
            for i, (rel, cset, _ch) in enumerate(remaining):
                sim = max((_jaccard(cset, s[1]) for s in selected), default=0.0)
                val = self._lam * rel - (1.0 - self._lam) * sim
                if best_val is None or val > best_val:
                    best_val, best_i = val, i
            selected.append(remaining.pop(best_i))
        return [s[2] for s in selected]


_RERANK_SYSTEM = (
    "Ты — точный ранжировщик релевантности для поисковой системы. Тебе дают "
    "ВОПРОС и пронумерованные ФРАГМЕНТЫ из базы. Оцени, насколько каждый "
    "фрагмент помогает ответить на вопрос, по шкале 0–10 (0 — не по теме, "
    "10 — прямо содержит ответ). Верни ТОЛЬКО строки вида `индекс: оценка`, "
    "по одной на фрагмент, без пояснений."
)

_SCORE_RE = re.compile(r"(\d+)\s*[:\-=]\s*(\d+(?:\.\d+)?)")


def _parse_scores(reply: str, n: int) -> List[float]:
    """Разобрать ответ модели в список оценок длины n (пропуски → 0.0)."""
    scores: List[Optional[float]] = [None] * n
    for m in _SCORE_RE.finditer(reply or ""):
        i = int(m.group(1))
        if 0 <= i < n:
            scores[i] = float(m.group(2))
    return [s if s is not None else 0.0 for s in scores]


class LLMReranker:
    """Cross-encoder-реранк через LLM: один запрос оценивает весь список.

    Фрагменты нумеруются и (обрезанные) вкладываются в один промпт — так это
    N-кратно дешевле, чем по вызову на чанк. Ответ парсится в оценки; при сбое
    парсинга или ошибке запроса возвращается исходный порядок (устойчивый
    fallback). Сортировка стабильна: при равных оценках порядок из поиска цел.
    """

    def __init__(self,
                 client: Any,
                 params: dict,
                 max_chunk_chars: int = 500):
        self._client = client
        self._params = params
        self._max = max_chunk_chars

    def rerank(self, query: str, chunks: List[RetrievedChunk]) -> List[RetrievedChunk]:
        if not chunks:
            return []
        lines = []
        for i, ch in enumerate(chunks):
            snippet = ch.text[:self._max].replace("\n", " ").strip()
            loc = ch.location()
            lines.append(f"[{i}] ({loc}) {snippet}" if loc else f"[{i}] {snippet}")
        prompt = (f"ВОПРОС:\n{query}\n\nФРАГМЕНТЫ:\n" + "\n".join(lines) +
                  f"\n\nОцени релевантность каждого из {len(chunks)} фрагментов "
                  "(строки `индекс: оценка`, 0–10):")
        try:
            reply = self._client.chat(
                [{"role": "user", "content": prompt}], self._params, _RERANK_SYSTEM)
        except Exception:
            return list(chunks)  # реранк не должен рушить ответ — отдаём как есть
        scores = _parse_scores(reply, len(chunks))
        order = sorted(range(len(chunks)), key=lambda i: (-scores[i], i))
        return [chunks[i] for i in order]
