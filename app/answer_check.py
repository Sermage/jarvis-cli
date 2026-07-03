"""Проверка структуры RAG-ответа: источники + цитаты + режим «не знаю».

Чистые функции без I/O — валидируют текст ответа модели против найденных
чанков. Используются и eval-харнессом (examples/rag_eval), и юнит-тестами.

Обязательный формат ответа задаёт `app/system_prompt.py::_RAG_HEADER`:

    <ответ со ссылками [i]>

    Источники:
    - [i] source · раздел (chunk_id)

    Цитаты:
    - [i] «дословный фрагмент из чанка»

Проверяем три вещи (все детерминированно):
  • has_sources    — есть непустая секция «Источники»;
  • has_citations  — есть хотя бы одна цитата;
  • citations_grounded — КАЖДАЯ цитата дословно встречается в тексте одного из
    найденных чанков (ловит выдуманные цитаты — без LLM-судьи и его шума).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

# Кавычки, в которые модель оборачивает цитату: «ёлочки», "прямые", „лапки".
_QUOTE_RE = re.compile(r"[«\"„]\s*(.+?)\s*[»\"“]", re.DOTALL)

# Заголовок секции терпит markdown-обрамление: `Источники:`, `**Источники:**`,
# `### Источники`, `- Цитаты` — модель по-разному его украшает.
_WORD_RE = re.compile(r"[0-9a-zа-яё_]+", re.IGNORECASE)

_SOURCES_HEADER_RE = re.compile(r"^[\s*#>_.-]*источники[\s*:_.-]*$",
                                re.IGNORECASE | re.MULTILINE)
_CITATIONS_HEADER_RE = re.compile(r"^[\s*#>_.-]*цитаты[\s*:_.-]*$",
                                  re.IGNORECASE | re.MULTILINE)

# Формулировки отказа для режима «не знаю».
_DONT_KNOW_RE = re.compile(
    r"не\s+знаю|нет\s+релевантн|не\s+нашл|нет\s+информаци|"
    r"не\s+могу\s+ответить|отсутству\w*\s+.{0,20}контекст",
    re.IGNORECASE,
)


def _normalize(text: str) -> str:
    """Схлопнуть пробелы/переносы и привести к нижнему регистру для сравнения."""
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _quote_grounded(quote: str, haystacks: List[str]) -> bool:
    """Дословно ли цитата встречается в одном из чанков.

    Цитата может содержать многоточие-эллипсис «А … Б» — это легитимная
    выдержка с пропуском. Тогда требуем, чтобы КАЖДЫЙ значащий фрагмент дословно
    нашёлся в контексте (строго: реконструкция/перефраз всё равно не пройдёт).
    """
    frags = [_normalize(f).strip(" .") for f in re.split(r"\s*(?:…|\.{3})\s*", quote)]
    frags = [f for f in frags if len(f) >= 8]     # обрывки короче 8 симв. не проверяем
    if not frags:                                  # цитата без значащих фрагментов
        needle = _normalize(quote).rstrip(" .…")
        return bool(needle) and any(needle in h for h in haystacks)
    return all(any(f in h for h in haystacks) for f in frags)


def _content_overlap(quote: str, chunks_tokens: List[set]) -> float:
    """Доля значащих токенов цитаты, встретившихся в одном из чанков.

    Мягкий сигнал (в отличие от дословного grounded): отличает РЕКОНСТРУКЦИЮ
    реального контента (высокое пересечение, но не буквальная копия) от
    настоящей выдумки (низкое пересечение).
    """
    q = set(t for t in _WORD_RE.findall(quote.lower()) if len(t) >= 2)
    if not q:
        return 0.0
    return max((len(q & ct) / len(q) for ct in chunks_tokens), default=0.0)


def _section_body(answer: str, start_re: re.Pattern, end_re: re.Pattern) -> str:
    """Текст между заголовком start_re и следующим заголовком end_re (или концом)."""
    m = start_re.search(answer or "")
    if not m:
        return ""
    rest = answer[m.end():]
    end = end_re.search(rest)
    return rest[:end.start()] if end else rest


def extract_quotes(text: str) -> List[str]:
    """Достать содержимое всех кавычек-цитат из блока."""
    return [q.strip() for q in _QUOTE_RE.findall(text or "") if q.strip()]


@dataclass
class AnswerCheck:
    has_sources: bool = False
    has_citations: bool = False
    citations_grounded: bool = False   # все цитаты дословно (или по фрагментам) есть в чанках
    n_citations: int = 0
    n_grounded: int = 0                # дословно обоснованных цитат
    n_from_context: int = 0            # цитат с высоким пересечением токенов (реконструкции)
    ungrounded: List[str] = field(default_factory=list)  # не дословные цитаты
    fabricated: List[str] = field(default_factory=list)   # низкое пересечение → выдумка

    @property
    def ok(self) -> bool:
        """Ответ полностью соответствует требованиям: источники + грунт-цитаты."""
        return self.has_sources and self.has_citations and self.citations_grounded

    @property
    def no_fabrication(self) -> bool:
        """Нет выдуманных цитат: каждая либо дословна, либо взята из контекста."""
        return self.has_citations and not self.fabricated


def check_answer(answer: str, chunks: list, overlap_threshold: float = 0.7) -> AnswerCheck:
    """Проверить, что ответ содержит источники и обоснованные цитаты."""
    sources_body = _section_body(answer, _SOURCES_HEADER_RE, _CITATIONS_HEADER_RE)
    citations_body = _section_body(answer, _CITATIONS_HEADER_RE, _SOURCES_HEADER_RE)

    has_sources = bool(sources_body.strip())
    quotes = extract_quotes(citations_body)

    haystacks = [_normalize(getattr(c, "text", "")) for c in chunks]
    chunks_tokens = [set(t for t in _WORD_RE.findall(h) if len(t) >= 2) for h in haystacks]
    ungrounded, fabricated = [], []
    n_grounded = n_from_context = 0
    for q in quotes:
        if _quote_grounded(q, haystacks):
            n_grounded += 1
            n_from_context += 1
        else:
            ungrounded.append(q)
            if _content_overlap(q, chunks_tokens) >= overlap_threshold:
                n_from_context += 1     # реконструкция реального контента
            else:
                fabricated.append(q)    # содержимого нет в контексте — выдумка

    return AnswerCheck(
        has_sources=has_sources,
        has_citations=bool(quotes),
        citations_grounded=bool(quotes) and not ungrounded,
        n_citations=len(quotes),
        n_grounded=n_grounded,
        n_from_context=n_from_context,
        ungrounded=ungrounded,
        fabricated=fabricated,
    )


def is_dont_know(answer: str) -> bool:
    """Ответ является честным отказом «не знаю»?"""
    return bool(_DONT_KNOW_RE.search(answer or ""))
