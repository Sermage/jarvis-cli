"""RAG-поиск по FAQ/документации поддержки без внешних зависимостей.

Реализует порт `app.ports.RetrievalEngine` (те же `retrieve`/`is_ready`),
поэтому подставляется в use case и в `RetrievalPipeline` вместо
FAISS+ollama движка. Здесь — лёгкий лексический поиск по markdown-файлам:
каталог `docs/support-faq/*.md` разбивается на чанки по заголовкам `##`,
а близость к запросу считается как перекрытие терминов (с bias на
совпадения в заголовке раздела). Ноль внешних зависимостей — feature
работает «из коробки» и детерминированно тестируется.

Для «настоящего» векторного RAG движок взаимозаменяем: тот же порт
реализует `infra/rag_retrieval.py::FaissOllamaRetrievalEngine` — достаточно
собрать FAISS-индекс по этим же .md и подсунуть его в composition root.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from domain.retrieval import RetrievedChunk

# Слова, которые не несут смысла для лексического матчинга (рус/англ стоп-лист).
_STOP = {
    "и", "в", "во", "не", "на", "по", "с", "со", "а", "но", "же", "как", "что",
    "это", "у", "к", "о", "об", "за", "из", "для", "то", "так", "или", "бы",
    "почему", "мой", "моя", "мне", "меня", "я", "ты", "вы", "он", "она",
    "the", "a", "an", "is", "are", "to", "of", "in", "on", "for", "and", "or",
    "why", "how", "what", "my", "i", "it", "do", "does",
}
_WORD_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9_]+")


def _tokens(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(text) if len(w) > 1]


def _terms(text: str) -> set[str]:
    return {w for w in _tokens(text) if w not in _STOP}


class _FaqChunk:
    """Разобранный раздел FAQ + предпосчитанное множество терминов."""

    __slots__ = ("text", "source", "title", "section", "terms", "title_terms")

    def __init__(self, text: str, source: str, title: str, section: str):
        self.text = text
        self.source = source
        self.title = title
        self.section = section
        self.terms = _terms(text)
        self.title_terms = _terms(f"{title} {section}")


class MarkdownFaqRetrievalEngine:
    """Лексический RAG по каталогу markdown-файлов FAQ. Реализует RetrievalEngine."""

    def __init__(self, faq_dir: str, min_overlap: int = 1):
        self._dir = Path(faq_dir).expanduser()
        self._min_overlap = min_overlap
        self._chunks: Optional[list[_FaqChunk]] = None

    # ── RetrievalEngine ───────────────────────────────────────────────────────

    def is_ready(self) -> bool:
        try:
            return self._dir.is_dir() and bool(self._load())
        except OSError:
            return False

    def retrieve(self, query: str, top_k: int = 5) -> list[RetrievedChunk]:
        chunks = self._load()
        if not chunks:
            return []
        q_terms = _terms(query)
        if not q_terms:
            return []
        scored: list[tuple[float, _FaqChunk]] = []
        for ch in chunks:
            overlap = len(q_terms & ch.terms)
            # Совпадение в заголовке раздела ценнее совпадения в теле — и само
            # по себе достаточно, чтобы раздел прошёл порог (термин из вопроса
            # может стоять только в заголовке документа/раздела).
            title_hits = len(q_terms & ch.title_terms)
            if overlap + title_hits < self._min_overlap:
                continue
            score = overlap + 2.0 * title_hits
            # Нормируем на длину запроса — грубый аналог косинуса в [0..~1+].
            score = score / (len(q_terms) + 1e-9)
            scored.append((score, ch))
        scored.sort(key=lambda p: p[0], reverse=True)
        out: list[RetrievedChunk] = []
        for score, ch in scored[:top_k]:
            out.append(RetrievedChunk(
                text=ch.text,
                source=ch.source,
                title=ch.title,
                section=ch.section,
                score=round(float(score), 4),
            ))
        return out

    # ── загрузка/парсинг ──────────────────────────────────────────────────────

    def _load(self) -> list[_FaqChunk]:
        if self._chunks is not None:
            return self._chunks
        chunks: list[_FaqChunk] = []
        if self._dir.is_dir():
            for md in sorted(self._dir.rglob("*.md")):
                try:
                    raw = md.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                rel = md.name
                chunks.extend(self._split(raw, rel))
        self._chunks = chunks
        return chunks

    @staticmethod
    def _split(raw: str, source: str) -> list[_FaqChunk]:
        """Разбить markdown на разделы: заголовок `## ...` начинает новый чанк."""
        lines = raw.splitlines()
        title = ""
        section = ""
        buf: list[str] = []
        out: list[_FaqChunk] = []

        def flush():
            body = "\n".join(buf).strip()
            if body:
                out.append(_FaqChunk(text=body, source=source, title=title, section=section))

        for line in lines:
            if line.startswith("# ") and not line.startswith("## "):
                # H1 — заголовок документа; тело до первого H2 тоже сохраняем.
                flush()
                buf = []
                title = line[2:].strip()
                section = ""
            elif line.startswith("## "):
                flush()
                buf = []
                section = line[3:].strip()
            else:
                buf.append(line)
        flush()
        return out
