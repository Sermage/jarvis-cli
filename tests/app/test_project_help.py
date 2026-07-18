"""Юнит-тесты use case /help — ответ о проекте по документации + git-ветка.

Все порты (RetrievalEngine, GitContextProvider, LLMClient) подменены фейками —
без FAISS, MCP и сети.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.project_help import answer_project_question
from domain.retrieval import RetrievedChunk


@dataclass
class _FakeEngine:
    chunks: list = field(default_factory=list)
    ready: bool = True
    last_query: str = ""
    last_top_k: int = 0

    def is_ready(self):
        return self.ready

    def retrieve(self, query, top_k=5):
        self.last_query = query
        self.last_top_k = top_k
        return list(self.chunks)


@dataclass
class _FakeGit:
    branch = "main"

    def current_branch(self):
        return self.branch


class _BoomGit:
    def current_branch(self):
        raise RuntimeError("mcp down")


@dataclass
class _FakeClient:
    reply: str = "Проект разбит на слои cli/app/domain/infra [1]."
    last_system: str = ""
    last_messages: list = field(default_factory=list)

    def chat(self, messages, params, system_prompt=None):
        self.last_system = system_prompt or ""
        self.last_messages = messages
        return self.reply


def _chunks():
    return [
        RetrievedChunk(text="cli/ app/ domain/ infra/ — четыре слоя.",
                       source="docs/architecture.md", section="Слои", chunk_id="a#1"),
    ]


def test_answer_uses_retrieval_and_git_branch():
    eng = _FakeEngine(chunks=_chunks())
    client = _FakeClient()
    res = answer_project_question("какие слои?", eng, _FakeGit(), client, {}, top_k=3)

    assert res.reply == client.reply
    assert res.branch == "main"
    assert res.sources and res.sources[0].source == "docs/architecture.md"
    assert eng.last_top_k == 3
    # git-ветка и текст чанка попали в контекст запроса к LLM
    ctx = client.last_messages[0]["content"]
    assert "main" in ctx and "четыре слоя" in ctx


def test_git_failure_is_tolerated():
    res = answer_project_question("вопрос", _FakeEngine(chunks=_chunks()),
                                  _BoomGit(), _FakeClient(), {})
    assert res.branch is None
    assert res.reply  # ответ всё равно сформирован по докам


def test_no_git_provider_still_answers():
    res = answer_project_question("вопрос", _FakeEngine(chunks=_chunks()),
                                  None, _FakeClient(), {})
    assert res.branch is None and res.used_context


def test_empty_index_and_no_branch_returns_hint_without_llm_call():
    client = _FakeClient(reply="НЕ ДОЛЖНО ВЫЗЫВАТЬСЯ")
    eng = _FakeEngine(chunks=[], ready=True)

    class _NoBranch:
        def current_branch(self):
            return None

    res = answer_project_question("вопрос", eng, _NoBranch(), client, {})
    assert not res.used_context
    assert res.reply != client.reply  # LLM не звался — вернулась подсказка
    assert client.last_messages == []


def test_engine_not_ready_skips_retrieval_but_git_still_used():
    eng = _FakeEngine(chunks=_chunks(), ready=False)
    client = _FakeClient()
    res = answer_project_question("вопрос", eng, _FakeGit(), client, {})
    # ретрив пропущен (движок не готов), но ветка есть → отвечаем по git-контексту
    assert eng.last_query == "" and res.branch == "main"
    assert "main" in client.last_messages[0]["content"]
