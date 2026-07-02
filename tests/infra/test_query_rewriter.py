"""Юнит-тесты LLMQueryRewriter на фейковом клиенте."""
from infra.query_rewriter import LLMQueryRewriter


class FakeClient:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def chat(self, messages, params, system_prompt=None):
        self.calls.append((messages, params, system_prompt))
        return self.reply


def test_rewrite_returns_model_line():
    r = LLMQueryRewriter(FakeClient("guarded_chat retry loop проверка инвариантов"), {})
    out = r.rewrite("как проверяются инварианты")
    assert "guarded_chat" in out


def test_rewrite_takes_first_nonempty_line_and_strips_quotes():
    r = LLMQueryRewriter(FakeClient('\n  "переформулированный запрос"  \nлишнее'), {})
    assert r.rewrite("вопрос") == "переформулированный запрос"


def test_rewrite_empty_falls_back_to_original():
    assert LLMQueryRewriter(FakeClient("   "), {}).rewrite("вопрос") == "вопрос"


def test_rewrite_none_reply_falls_back():
    assert LLMQueryRewriter(FakeClient(None), {}).rewrite("вопрос") == "вопрос"


def test_rewrite_error_falls_back():
    class Boom:
        def chat(self, *a, **k):
            raise RuntimeError("x")

    assert LLMQueryRewriter(Boom(), {}).rewrite("вопрос") == "вопрос"


def test_rewrite_passes_query_as_user_message():
    client = FakeClient("ok")
    LLMQueryRewriter(client, {"model": "m"}).rewrite("мой вопрос")
    messages, params, system_prompt = client.calls[0]
    assert messages[0]["content"] == "мой вопрос"
    assert system_prompt  # системный промпт задан
