"""Тесты разбора RAG-настроек из окружения (cli.config.load_rag_config)."""
import pytest

from cli.config import (
    DEFAULT_RAG_FETCH_K,
    DEFAULT_RAG_RERANKER,
    DEFAULT_RAG_TOP_K,
    load_rag_config,
)

_RAG_VARS = ["RAG_ENABLED", "RAG_INDEX_PATH", "RAG_STRATEGY", "RAG_TOP_K",
             "RAG_FETCH_K", "RAG_MIN_SCORE", "RAG_RERANKER", "RAG_REWRITE"]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for v in _RAG_VARS:
        monkeypatch.delenv(v, raising=False)


def test_defaults_when_env_empty():
    cfg = load_rag_config()
    assert cfg.enabled is False
    assert cfg.top_k == DEFAULT_RAG_TOP_K
    assert cfg.fetch_k == DEFAULT_RAG_FETCH_K
    assert cfg.min_score == 0.0
    assert cfg.reranker == DEFAULT_RAG_RERANKER
    assert cfg.rewrite is False


def test_reads_all_fields(monkeypatch):
    monkeypatch.setenv("RAG_ENABLED", "true")
    monkeypatch.setenv("RAG_TOP_K", "8")
    monkeypatch.setenv("RAG_FETCH_K", "30")
    monkeypatch.setenv("RAG_MIN_SCORE", "0.45")
    monkeypatch.setenv("RAG_RERANKER", "llm")
    monkeypatch.setenv("RAG_REWRITE", "yes")
    cfg = load_rag_config()
    assert cfg.enabled is True
    assert cfg.top_k == 8
    assert cfg.fetch_k == 30
    assert cfg.min_score == 0.45
    assert cfg.reranker == "llm"
    assert cfg.rewrite is True


def test_invalid_reranker_falls_back(monkeypatch):
    monkeypatch.setenv("RAG_RERANKER", "bogus")
    assert load_rag_config().reranker == DEFAULT_RAG_RERANKER


def test_invalid_numbers_fall_back(monkeypatch):
    monkeypatch.setenv("RAG_TOP_K", "abc")
    monkeypatch.setenv("RAG_MIN_SCORE", "xyz")
    cfg = load_rag_config()
    assert cfg.top_k == DEFAULT_RAG_TOP_K
    assert cfg.min_score == 0.0
