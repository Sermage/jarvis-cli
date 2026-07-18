"""Тесты обработчика /help: развилка «статичная справка» vs «вопрос о проекте»."""
from __future__ import annotations

from dataclasses import dataclass, field

from cli.help_commands import handle_help
from domain.retrieval import RetrievedChunk


@dataclass
class _FakeEngine:
    ready: bool = True
    chunks: list = field(default_factory=list)

    def is_ready(self):
        return self.ready

    def retrieve(self, query, top_k=5):
        return list(self.chunks)


@dataclass
class _FakeGit:
    def current_branch(self):
        return "main"


@dataclass
class _FakeClient:
    reply: str = "Ответ по докам [1]."

    def chat(self, messages, params, system_prompt=None):
        return self.reply


def _chunks():
    return [RetrievedChunk(text="слои cli/app/domain/infra", source="docs/architecture.md",
                           section="Слои", chunk_id="a#1")]


def test_no_question_prints_static_help(capsys):
    handle_help("/help", _FakeEngine(), _FakeGit(), _FakeClient(), {})
    out = capsys.readouterr().out
    # признак статичной справки print_help()
    assert "/wm" in out and "/rag" in out
    assert "Справка по проекту" not in out


def test_question_answers_from_docs_with_branch_and_sources(capsys):
    handle_help("/help какие слои у проекта?", _FakeEngine(chunks=_chunks()),
                _FakeGit(), _FakeClient(reply="Проект слоистый."), {})
    out = capsys.readouterr().out
    assert "Справка по проекту:" in out and "Проект слоистый." in out
    assert "main" in out                       # git-ветка через MCP
    assert "docs/architecture.md" in out       # источник


def test_index_not_ready_warns(capsys):
    handle_help("/help вопрос", _FakeEngine(ready=False), _FakeGit(), _FakeClient(), {})
    out = capsys.readouterr().out
    assert "не готов" in out
    assert "Справка по проекту" not in out
