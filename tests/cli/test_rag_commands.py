from cli.rag_commands import handle_rag
from domain.retrieval import RetrievalConfig


class FakeEngine:
    def __init__(self, ready=True):
        self._ready = ready
    def is_ready(self):
        return self._ready
    def retrieve(self, query, top_k=5):
        return []


def test_on_enables_when_ready():
    cfg = RetrievalConfig(enabled=False, index_path="/x", strategy="structural", top_k=5)
    handle_rag("/rag on", cfg, FakeEngine(ready=True))
    assert cfg.enabled is True


def test_on_refused_when_not_ready():
    cfg = RetrievalConfig(enabled=False)
    handle_rag("/rag on", cfg, FakeEngine(ready=False))
    assert cfg.enabled is False


def test_on_refused_when_engine_none():
    cfg = RetrievalConfig(enabled=False)
    handle_rag("/rag on", cfg, None)
    assert cfg.enabled is False


def test_off_disables():
    cfg = RetrievalConfig(enabled=True)
    handle_rag("/rag off", cfg, FakeEngine())
    assert cfg.enabled is False


def test_status_does_not_change_state(capsys):
    cfg = RetrievalConfig(enabled=True, index_path="/idx", strategy="structural", top_k=7)
    handle_rag("/rag", cfg, FakeEngine())
    out = capsys.readouterr().out
    assert cfg.enabled is True
    assert "/idx" in out
    assert "7" in out


def test_status_shows_pipeline_stages(capsys):
    cfg = RetrievalConfig(enabled=True, fetch_k=20, min_score=0.45,
                          reranker="llm", rewrite=True, top_k=5)
    handle_rag("/rag status", cfg, FakeEngine())
    out = capsys.readouterr().out
    assert "20" in out and "0.45" in out and "llm" in out


def test_reranker_valid_sets_mode():
    cfg = RetrievalConfig(reranker="heuristic")
    handle_rag("/rag reranker llm", cfg, FakeEngine())
    assert cfg.reranker == "llm"


def test_reranker_invalid_keeps_mode():
    cfg = RetrievalConfig(reranker="heuristic")
    handle_rag("/rag reranker bogus", cfg, FakeEngine())
    assert cfg.reranker == "heuristic"


def test_rewrite_on_off():
    cfg = RetrievalConfig(rewrite=False)
    handle_rag("/rag rewrite on", cfg, FakeEngine())
    assert cfg.rewrite is True
    handle_rag("/rag rewrite off", cfg, FakeEngine())
    assert cfg.rewrite is False


def test_threshold_sets_min_score():
    cfg = RetrievalConfig(min_score=0.0)
    handle_rag("/rag threshold 0.45", cfg, FakeEngine())
    assert cfg.min_score == 0.45


def test_threshold_invalid_keeps_value():
    cfg = RetrievalConfig(min_score=0.3)
    handle_rag("/rag threshold abc", cfg, FakeEngine())
    assert cfg.min_score == 0.3


def test_fetchk_and_topk():
    cfg = RetrievalConfig(fetch_k=20, top_k=5)
    handle_rag("/rag fetchk 30", cfg, FakeEngine())
    handle_rag("/rag topk 8", cfg, FakeEngine())
    assert cfg.fetch_k == 30 and cfg.top_k == 8


def test_fetchk_rejects_below_min():
    cfg = RetrievalConfig(fetch_k=20)
    handle_rag("/rag fetchk 0", cfg, FakeEngine())
    assert cfg.fetch_k == 20


def test_unknown_subcommand(capsys):
    cfg = RetrievalConfig(enabled=False)
    handle_rag("/rag frobnicate", cfg, FakeEngine())
    out = capsys.readouterr().out
    assert "on" in out and "off" in out
    assert cfg.enabled is False
