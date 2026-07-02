"""Доменные модели RAG-поиска.

Чистые данные без I/O: конфигурация retrieval и найденный чанк.
Само чтение индекса и обращение к эмбеддинг-модели — в infra
(`infra/rag_retrieval.py`) за портом `app.ports.RetrievalEngine`.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RetrievalConfig:
    """Настройки RAG-режима.

    index_path — каталог с файлами индекса (`<strategy>.faiss` +
    `<strategy>.meta.json`), собранными отдельным пайплайном-индексатором.
    """
    enabled: bool = False
    index_path: str = ""
    strategy: str = "structural"
    top_k: int = 5


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
