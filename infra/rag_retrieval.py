"""RAG-поиск поверх FAISS-индекса + локальных эмбеддингов Ollama.

Реализует порт `app.ports.RetrievalEngine`. Индекс собирается отдельным
пайплайном-индексатором и лежит на диске парой файлов:

    <index_path>/<strategy>.faiss        FAISS IndexFlatIP (косинус на L2-норме)
    <index_path>/<strategy>.meta.json    [{"text","source","title","section",...}]

Зависимости `faiss` и `numpy` — тяжёлые и опциональные (extra `[rag]`),
поэтому импортируются лениво внутри методов: сам модуль импортируется без
них, а падаем понятной ошибкой только при реальном поиске.
Транспорт к Ollama (`http_post`) инжектится через конструктор — для тестов.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, List, Optional

import requests

from domain.retrieval import RetrievedChunk


class FaissOllamaRetrievalEngine:
    """Читает готовый FAISS-индекс и ищет по нему, эмбеддя запрос через Ollama."""

    def __init__(self,
                 index_path: str,
                 strategy: str = "structural",
                 embed_model: str = "bge-m3",
                 ollama_url: str = "http://localhost:11434",
                 http_post: Optional[Callable[..., Any]] = None,
                 timeout: int = 60):
        self._index_path = index_path
        self._strategy = strategy
        self._embed_model = embed_model
        self._ollama_url = ollama_url.rstrip("/")
        self._post = http_post or requests.post
        self._timeout = timeout
        self._index = None            # ленивая загрузка FAISS-индекса
        self._metas: List[dict] = []

    # ── пути к артефактам индекса ───────────────────────────────────────────
    def _faiss_file(self) -> str:
        return os.path.join(self._index_path, f"{self._strategy}.faiss")

    def _meta_file(self) -> str:
        return os.path.join(self._index_path, f"{self._strategy}.meta.json")

    def is_ready(self) -> bool:
        """Файлы индекса на месте и faiss/numpy импортируются."""
        if not (os.path.exists(self._faiss_file()) and os.path.exists(self._meta_file())):
            return False
        try:
            import faiss  # noqa: F401
            import numpy  # noqa: F401
        except ImportError:
            return False
        return True

    # ── загрузка индекса (один раз) ─────────────────────────────────────────
    def _ensure_loaded(self) -> None:
        if self._index is not None:
            return
        import faiss
        self._index = faiss.read_index(self._faiss_file())
        with open(self._meta_file(), encoding="utf-8") as f:
            self._metas = json.load(f)

    # ── эмбеддинг запроса через Ollama ──────────────────────────────────────
    def _embed(self, text: str):
        import numpy as np
        resp = self._post(
            f"{self._ollama_url}/api/embeddings",
            json={"model": self._embed_model, "prompt": text},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        vec = np.asarray(resp.json()["embedding"], dtype="float32").reshape(1, -1)
        norm = np.linalg.norm(vec, axis=1, keepdims=True)
        norm[norm == 0] = 1.0
        return vec / norm

    def retrieve(self, query: str, top_k: int = 5) -> list[RetrievedChunk]:
        self._ensure_loaded()
        qvec = self._embed(query)
        scores, idx = self._index.search(qvec, top_k)
        hits: list[RetrievedChunk] = []
        for score, i in zip(scores[0], idx[0]):
            if i < 0:
                continue
            m = self._metas[i]
            hits.append(RetrievedChunk(
                text=m.get("text", ""),
                source=m.get("source", ""),
                title=m.get("title", ""),
                section=m.get("section", ""),
                score=float(score),
                chunk_id=m.get("chunk_id", ""),
            ))
        return hits
