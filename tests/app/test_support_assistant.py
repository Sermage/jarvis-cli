"""Юнит-тесты use case /support — ответ по FAQ + контекст тикета через MCP.

Порты (RetrievalEngine, SupportChat) подменены фейками: без FAISS, MCP и сети.
Проверяем сборку контекста, проброс FAQ и тикета в модель и агрегацию следа
tool-loop в результат.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.support_assistant import (
    PlainChatAdapter,
    SupportAnswer,
    answer_support_question,
)
from domain.retrieval import RetrievedChunk


@dataclass
class _FakeEngine:
    chunks: list = field(default_factory=list)
    ready: bool = True
    last_query: str = ""

    def is_ready(self):
        return self.ready

    def retrieve(self, query, top_k=5):
        self.last_query = query
        return list(self.chunks)


@dataclass
class _Inv:
    server_id: str
    tool_name: str
    arguments: dict
    is_error: bool = False


@dataclass
class _FakeResult:
    reply: str
    trace: list = field(default_factory=list)
    truncated: bool = False


@dataclass
class _FakeChat:
    """Фейковый SupportChat: возвращает заданный результат, записывает вход."""
    result: _FakeResult
    last_messages: list = field(default_factory=list)
    last_system: str = ""

    def chat(self, messages, params, system_prompt=None, on_event=None):
        self.last_messages = messages
        self.last_system = system_prompt or ""
        return self.result


def _chunks():
    return [
        RetrievedChunk(text="Вход через Google доступен на Business. На Free — 403.",
                       source="auth.md", section="Вход через Google"),
    ]


def test_faq_context_and_question_reach_the_model():
    engine = _FakeEngine(chunks=_chunks())
    chat = _FakeChat(_FakeResult(reply="На тарифе Free SSO недоступен."))
    answer_support_question("Почему не работает вход?", None, engine, chat, {})
    user_msg = chat.last_messages[0]["content"]
    assert "Вход через Google" in user_msg          # FAQ подмешан
    assert "Почему не работает вход?" in user_msg    # вопрос на месте
    assert engine.last_query == "Почему не работает вход?"


def test_ticket_hint_instructs_tool_call():
    engine = _FakeEngine(chunks=_chunks())
    chat = _FakeChat(_FakeResult(reply="ok"))
    answer_support_question("Не могу войти", "T-1024", engine, chat, {})
    user_msg = chat.last_messages[0]["content"]
    assert "T-1024" in user_msg
    assert "support__get_ticket" in user_msg  # явная подсказка агенту


def test_returns_sources_and_ticket_id():
    engine = _FakeEngine(chunks=_chunks())
    chat = _FakeChat(_FakeResult(reply="ответ"))
    ans = answer_support_question("вопрос", "T-1024", engine, chat, {})
    assert isinstance(ans, SupportAnswer)
    assert ans.reply == "ответ"
    assert ans.ticket_id == "T-1024"
    assert ans.used_faq is True
    assert ans.sources and ans.sources[0].source == "auth.md"


def test_tool_trace_is_aggregated():
    engine = _FakeEngine(chunks=[])
    result = _FakeResult(
        reply="готово",
        trace=[
            _Inv("support", "get_ticket", {"ticket_id": "T-1024"}),
            _Inv("support", "get_user", {"user_id": "U-100"}, is_error=True),
        ],
    )
    ans = answer_support_question("q", "T-1024", engine, _FakeChat(result), {})
    assert [t.tool_name for t in ans.tools_used] == ["get_ticket", "get_user"]
    assert ans.tools_used[1].is_error is True


def test_no_faq_hits_marks_used_faq_false():
    chat = _FakeChat(_FakeResult(reply="не нашёл в FAQ"))
    ans = answer_support_question("экзотика", None, _FakeEngine(chunks=[]), chat, {})
    assert ans.used_faq is False
    assert "ничего не нашлось" in chat.last_messages[0]["content"]


def test_engine_not_ready_is_tolerated():
    engine = _FakeEngine(chunks=_chunks(), ready=False)
    chat = _FakeChat(_FakeResult(reply="ok"))
    ans = answer_support_question("вопрос", None, engine, chat, {})
    assert ans.sources == []          # поиск не запускался
    assert ans.reply == "ok"


def test_truncated_flag_propagates():
    chat = _FakeChat(_FakeResult(reply="...", truncated=True))
    ans = answer_support_question("q", None, _FakeEngine(), chat, {})
    assert ans.truncated is True


def test_system_prompt_mentions_faq_and_tools():
    chat = _FakeChat(_FakeResult(reply="ok"))
    answer_support_question("q", None, _FakeEngine(), chat, {})
    assert "FAQ" in chat.last_system
    assert "support__get_ticket" in chat.last_system


# ── PlainChatAdapter (деградация без tool-loop) ──────────────────────────────

@dataclass
class _FakeLLM:
    reply: str = "ответ по FAQ"
    last_system: str = ""

    def chat(self, messages, params, system_prompt=None):
        self.last_system = system_prompt or ""
        return self.reply


def test_plain_adapter_answers_from_faq_without_tools():
    llm = _FakeLLM()
    engine = _FakeEngine(chunks=_chunks())
    ans = answer_support_question("вопрос", None, engine, PlainChatAdapter(llm), {})
    assert ans.reply == "ответ по FAQ"
    assert ans.tools_used == []       # тулов нет — деградация
    assert ans.sources                # но FAQ подмешан
