"""Доменные модели RAG-поиска.

Чистые данные без I/O: конфигурация retrieval и найденный чанк.
Само чтение индекса и обращение к эмбеддинг-модели — в infra
(`infra/rag_retrieval.py`) за портом `app.ports.RetrievalEngine`.
"""
from __future__ import annotations

from dataclasses import dataclass


# Доступные реранкеры второго этапа (см. infra/rerankers.py).
#   none      — только порог min_score, порядок из FAISS сохраняется;
#   heuristic — лексический реранк (косинус × совпадение терминов) + MMR, без модели;
#   llm       — cross-encoder на LLM: модель оценивает релевантность каждого чанка.
RERANKERS = ("none", "heuristic", "llm")


@dataclass
class RetrievalConfig:
    """Настройки RAG-режима.

    index_path — каталог с файлами индекса (`<strategy>.faiss` +
    `<strategy>.meta.json`), собранными отдельным пайплайном-индексатором.

    Второй этап (после поиска) настраивается тремя ручками:
      fetch_k   — сколько кандидатов достать из индекса ДО фильтрации/реранка;
      min_score — порог косинусной близости: чанки ниже отсекаются как нерелевантные;
      reranker  — как переупорядочить кандидатов (см. RERANKERS);
      rewrite   — переформулировать ли запрос через LLM перед поиском.
    top_k — сколько чанков остаётся ПОСЛЕ фильтра/реранка и уходит в промпт.
    """
    enabled: bool = False
    index_path: str = ""
    strategy: str = "structural"
    top_k: int = 5
    fetch_k: int = 20
    min_score: float = 0.0
    reranker: str = "heuristic"
    rewrite: bool = False


@dataclass
class RetrievedChunk:
    """Один найденный фрагмент базы с метаданными для цитирования."""
    text: str
    source: str = ""       # напр. "docs/classes.md"
    title: str = ""        # заголовок документа
    section: str = ""      # хлебные крошки раздела
    score: float = 0.0     # косинусная близость к запросу
    chunk_id: str = ""

    def location(self) -> str:
        """Человекочитаемая ссылка на источник для цитаты в ответе."""
        loc = self.section or self.title or self.source
        if self.source and self.source not in loc:
            return f"{self.source} · {loc}" if loc else self.source
        return loc or self.source
