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


def test_unknown_subcommand(capsys):
    cfg = RetrievalConfig(enabled=False)
    handle_rag("/rag frobnicate", cfg, FakeEngine())
    out = capsys.readouterr().out
    assert "on" in out and "off" in out
    assert cfg.enabled is False
