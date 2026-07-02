"""Юнит-тесты RetrievalPipeline на фейковых базовом движке/реранкере/rewriter."""
from app.retrieval_pipeline import RetrievalPipeline
from domain.retrieval import RetrievalConfig, RetrievedChunk


class FakeBase:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []

    def is_ready(self):
        return True

    def retrieve(self, query, top_k=5):
        self.calls.append((query, top_k))
        return list(self.chunks[:top_k])


def _c(name, score):
    return RetrievedChunk(text=name, source=f"{name}.md", score=score)


def test_fetches_fetch_k_then_cuts_to_top_k():
    base = FakeBase([_c("a", 0.9), _c("b", 0.8), _c("c", 0.7)])
    cfg = RetrievalConfig(top_k=2, fetch_k=10, min_score=0.0, reranker="none")
    out = RetrievalPipeline(base, cfg).retrieve("q", top_k=2)
    assert base.calls[0][1] == 10           # из индекса достали fetch_k
    assert [c.source for c in out] == ["a.md", "b.md"]  # обрезали до top_k


def test_min_score_filters_low_scores():
    base = FakeBase([_c("a", 0.9), _c("b", 0.4), _c("c", 0.2)])
    cfg = RetrievalConfig(top_k=5, fetch_k=10, min_score=0.5, reranker="none")
    out = RetrievalPipeline(base, cfg).retrieve("q")
    assert [c.source for c in out] == ["a.md"]


def test_min_score_zero_keeps_all():
    base = FakeBase([_c("a", 0.9), _c("b", 0.1)])
    cfg = RetrievalConfig(top_k=5, fetch_k=10, min_score=0.0, reranker="none")
    out = RetrievalPipeline(base, cfg).retrieve("q")
    assert len(out) == 2


def test_rewrite_applied_when_enabled():
    base = FakeBase([_c("a", 0.9)])
    cfg = RetrievalConfig(top_k=1, fetch_k=5, reranker="none", rewrite=True)

    class RW:
        def rewrite(self, q):
            return q + " EXPANDED"

    RetrievalPipeline(base, cfg, rewriter=RW()).retrieve("hello")
    assert "EXPANDED" in base.calls[0][0]


def test_rewrite_skipped_when_disabled():
    base = FakeBase([_c("a", 0.9)])
    cfg = RetrievalConfig(top_k=1, fetch_k=5, reranker="none", rewrite=False)

    class RW:
        def rewrite(self, q):
            return "SHOULD NOT BE USED"

    RetrievalPipeline(base, cfg, rewriter=RW()).retrieve("hello")
    assert base.calls[0][0] == "hello"


def test_reranker_reorders():
    base = FakeBase([_c("a", 0.9), _c("b", 0.8)])
    cfg = RetrievalConfig(top_k=2, fetch_k=5, reranker="heuristic")

    class RR:
        def rerank(self, q, chunks):
            return list(reversed(chunks))

    out = RetrievalPipeline(base, cfg, rerankers={"heuristic": RR()}).retrieve("q")
    assert [c.source for c in out] == ["b.md", "a.md"]


def test_reranker_none_keeps_order():
    base = FakeBase([_c("a", 0.9), _c("b", 0.8)])
    cfg = RetrievalConfig(top_k=2, fetch_k=5, reranker="none")

    class RR:
        def rerank(self, q, chunks):
            return list(reversed(chunks))

    # reranker="none" отсутствует в словаре → реранк не применяется
    out = RetrievalPipeline(base, cfg, rerankers={"heuristic": RR()}).retrieve("q")
    assert [c.source for c in out] == ["a.md", "b.md"]


def test_config_read_live():
    """Изменение конфига применяется к следующему запросу без пересборки."""
    base = FakeBase([_c("a", 0.9), _c("b", 0.4)])
    cfg = RetrievalConfig(top_k=5, fetch_k=10, min_score=0.0, reranker="none")
    pipe = RetrievalPipeline(base, cfg)
    assert len(pipe.retrieve("q")) == 2
    cfg.min_score = 0.5     # /rag threshold 0.5 на лету
    assert [c.source for c in pipe.retrieve("q")] == ["a.md"]


def test_is_ready_delegates_to_base():
    class NotReady(FakeBase):
        def is_ready(self):
            return False

    cfg = RetrievalConfig()
    assert RetrievalPipeline(NotReady([]), cfg).is_ready() is False
    assert RetrievalPipeline(FakeBase([]), cfg).is_ready() is True
