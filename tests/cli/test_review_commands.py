"""Тесты обработчика /review: парсинг номера PR и вывод ревью."""
from __future__ import annotations

from dataclasses import dataclass, field

from cli.review_commands import handle_review
from domain.retrieval import RetrievedChunk
from domain.review import PrDiff


@dataclass
class _FakeEngine:
    ready: bool = True
    chunks: list = field(default_factory=list)

    def is_ready(self):
        return self.ready

    def retrieve(self, query, top_k=5):
        return list(self.chunks)


@dataclass
class _FakeDiffProvider:
    diff: str = "diff --git a/app/x.py b/app/x.py\n+code"
    files: list = field(default_factory=lambda: ["app/x.py"])
    fetched: list = field(default_factory=list)

    def fetch(self, pr):
        self.fetched.append(pr)
        return PrDiff(number=str(pr), diff=self.diff, files=list(self.files))


@dataclass
class _BoomDiffProvider:
    def fetch(self, pr):
        raise RuntimeError("gh not authed")


@dataclass
class _FakeClient:
    reply: str = "## 🐞 Потенциальные баги\n- баг в app/x.py"

    def chat(self, messages, params, system_prompt=None):
        return self.reply


def _chunks():
    return [RetrievedChunk(text="слой app", source="docs/architecture.md",
                           section="Слои", chunk_id="a#1", score=0.8)]


def test_no_arg_prints_usage(capsys):
    handle_review("/review", _FakeEngine(), _FakeDiffProvider(), _FakeClient(), {})
    out = capsys.readouterr().out
    assert "номер PR" in out
    assert "AI-ревью" not in out


def test_non_numeric_arg_warns(capsys):
    handle_review("/review abc", _FakeEngine(), _FakeDiffProvider(), _FakeClient(), {})
    out = capsys.readouterr().out
    assert "должен быть числом" in out


def test_valid_pr_prints_review_files_and_sources(capsys):
    provider = _FakeDiffProvider()
    handle_review("/review 12", _FakeEngine(chunks=_chunks()), provider,
                  _FakeClient(reply="## 🐞 Потенциальные баги\n- баг"), {})
    out = capsys.readouterr().out
    assert provider.fetched == ["12"]
    assert "AI-ревью PR #12" in out
    assert "Потенциальные баги" in out
    assert "app/x.py" in out                     # изменённые файлы
    assert "docs/architecture.md" in out         # источник RAG


def test_fetch_error_is_reported(capsys):
    handle_review("/review 5", _FakeEngine(), _BoomDiffProvider(), _FakeClient(), {})
    out = capsys.readouterr().out
    assert "Не удалось получить PR #5" in out
