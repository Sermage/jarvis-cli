"""Юнит-тесты use case AI-ревью PR — фейковые порты RetrievalEngine и LLMClient.

Без FAISS, gh и сети: проверяем сборку контекста из diff, вызов LLM и обработку
пустого diff.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.pr_review import review_pull_request
from domain.retrieval import RetrievedChunk

_DIFF = """diff --git a/app/foo.py b/app/foo.py
index 111..222 100644
--- a/app/foo.py
+++ b/app/foo.py
@@ -1,3 +1,5 @@
+def divide(a, b):
+    return a / b
"""


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
class _FakeClient:
    reply: str = "## 🐞 Потенциальные баги\n- деление на ноль в app/foo.py"
    last_system: str = ""
    last_messages: list = field(default_factory=list)
    calls: int = 0

    def chat(self, messages, params, system_prompt=None):
        self.calls += 1
        self.last_system = system_prompt or ""
        self.last_messages = messages
        return self.reply


def _chunks():
    return [RetrievedChunk(text="Слой app оркестрирует порты.",
                           source="docs/architecture.md", section="Слои",
                           chunk_id="a#1", score=0.9)]


def test_review_uses_diff_and_retrieval():
    eng = _FakeEngine(chunks=_chunks())
    client = _FakeClient()
    res = review_pull_request(_DIFF, ["app/foo.py"], eng, client, {}, top_k=4)

    assert res.text == client.reply
    assert res.files == ["app/foo.py"]
    assert res.sources and res.sources[0].source == "docs/architecture.md"
    assert res.used_context
    assert eng.last_top_k == 4
    # добавленный код и путь файла попали в поисковый запрос
    assert "divide" in eng.last_query and "app/foo.py" in eng.last_query
    # diff и контекст попали в сообщение к LLM
    ctx = client.last_messages[0]["content"]
    assert "divide" in ctx and "Слой app оркестрирует" in ctx


def test_system_prompt_requires_three_sections():
    client = _FakeClient()
    review_pull_request(_DIFF, ["app/foo.py"], _FakeEngine(chunks=_chunks()),
                        client, {})
    for section in ("Потенциальные баги", "Архитектурные проблемы", "Рекомендации"):
        assert section in client.last_system


def test_empty_diff_returns_hint_without_llm_call():
    client = _FakeClient()
    res = review_pull_request("", [], _FakeEngine(chunks=_chunks()), client, {})
    assert not res.used_context
    assert client.calls == 0
    assert client.last_messages == []


def test_engine_not_ready_skips_retrieval_but_still_reviews():
    eng = _FakeEngine(chunks=_chunks(), ready=False)
    client = _FakeClient()
    res = review_pull_request(_DIFF, ["app/foo.py"], eng, client, {})
    assert eng.last_query == ""          # ретрив пропущен
    assert client.calls == 1             # но ревью по diff сделано
    assert res.sources == []


def test_no_engine_still_reviews():
    client = _FakeClient()
    res = review_pull_request(_DIFF, ["app/foo.py"], None, client, {})
    assert client.calls == 1 and res.text == client.reply


def test_long_diff_is_clipped():
    client = _FakeClient()
    big = "diff --git a/x b/x\n" + "\n".join(f"+line {i}" for i in range(20000))
    review_pull_request(big, ["x"], None, client, {})
    ctx = client.last_messages[0]["content"]
    assert "diff обрезан по размеру" in ctx
