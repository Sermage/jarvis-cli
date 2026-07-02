"""Многоступенчатый RAG-пайплайн поверх базового поиска.

Разворачивает второй этап между поиском и LLM, оставаясь при этом обычным
`RetrievalEngine` (те же `retrieve`/`is_ready`), — поэтому `build_system_prompt`
и порт не меняются: он просто получает «умный» движок вместо голого поиска.

    retrieve(query):
      1. rewrite   — переформулировать запрос (если включено и есть rewriter)
      2. fetch     — базовый поиск вернёт fetch_k кандидатов (с запасом)
      3. filter    — отсечь чанки со score < min_score (порог релевантности)
      4. rerank    — переупорядочить оставшихся выбранным реранкером
      5. cut       — оставить top_k

Настройки читаются из `RetrievalConfig` в момент вызова, поэтому команды
`/rag` могут менять их на лету (порог, fetch_k, реранкер, rewrite) без
пересборки пайплайна. Реранкеры и rewriter внедряются в конструктор
(в composition root), т.к. LLM-варианты требуют клиента модели.
"""
from __future__ import annotations

from typing import Optional

from app.ports import QueryRewriter, Reranker, RetrievalEngine
from domain.retrieval import RetrievalConfig, RetrievedChunk


class RetrievalPipeline:
    """Компонует базовый поиск с rewrite/фильтром/реранком. Реализует RetrievalEngine."""

    def __init__(self,
                 base_engine: RetrievalEngine,
                 config: RetrievalConfig,
                 rewriter: Optional[QueryRewriter] = None,
                 rerankers: Optional[dict] = None):
        self._base = base_engine
        self._config = config
        self._rewriter = rewriter
        self._rerankers = rerankers or {}  # {"heuristic": Reranker, "llm": Reranker}

    def is_ready(self) -> bool:
        return self._base.is_ready()

    def retrieve(self, query: str, top_k: Optional[int] = None) -> list[RetrievedChunk]:
        cfg = self._config
        k = top_k if top_k is not None else cfg.top_k

        # 1. rewrite
        q = query
        if cfg.rewrite and self._rewriter is not None:
            q = self._rewriter.rewrite(query) or query

        # 2. fetch (берём максимум из fetch_k и итогового k — реранку нужен запас)
        hits = self._base.retrieve(q, top_k=max(cfg.fetch_k, k))

        # 3. filter по порогу
        if cfg.min_score > 0:
            hits = [h for h in hits if h.score >= cfg.min_score]

        # 4. rerank
        reranker: Optional[Reranker] = self._rerankers.get(cfg.reranker)
        if reranker is not None:
            hits = reranker.rerank(q, hits)

        # 5. cut до top_k
        return hits[:k]
