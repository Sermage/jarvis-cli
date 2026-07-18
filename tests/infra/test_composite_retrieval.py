"""Тесты CompositeRetrievalEngine: слияние результатов двух индексов (доки+код)."""
from __future__ import annotations

from dataclasses import dataclass, field

from domain.retrieval import RetrievedChunk
from infra.rag_retrieval import CompositeRetrievalEngine


@dataclass
class _FakeEngine:
    ready: bool
    hits: list = field(default_factory=list)
    last_top_k: int = 0

    def is_ready(self):
        return self.ready

    def retrieve(self, query, top_k=5):
        self.last_top_k = top_k
        return list(self.hits)


def _chunk(src, score):
    return RetrievedChunk(text=src, source=src, score=score)


def test_merges_and_sorts_by_score():
    docs = _FakeEngine(True, [_chunk("docs.md", 0.5), _chunk("docs2.md", 0.9)])
    code = _FakeEngine(True, [_chunk("code.py", 0.7)])
    comp = CompositeRetrievalEngine([docs, code])

    hits = comp.retrieve("q", top_k=2)
    assert [h.source for h in hits] == ["docs2.md", "code.py"]  # 0.9, 0.7 (0.5 отсечён top_k)


def test_skips_not_ready_engines():
    docs = _FakeEngine(False, [_chunk("docs.md", 0.9)])
    code = _FakeEngine(True, [_chunk("code.py", 0.4)])
    comp = CompositeRetrievalEngine([docs, code])

    hits = comp.retrieve("q", top_k=5)
    assert [h.source for h in hits] == ["code.py"]
    assert docs.last_top_k == 0  # не опрашивался


def test_is_ready_if_any_subengine_ready():
    assert CompositeRetrievalEngine([_FakeEngine(False), _FakeEngine(True)]).is_ready()
    assert not CompositeRetrievalEngine([_FakeEngine(False), _FakeEngine(False)]).is_ready()
    assert not CompositeRetrievalEngine([]).is_ready()
