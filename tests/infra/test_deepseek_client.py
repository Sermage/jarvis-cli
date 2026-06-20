"""Тесты DeepSeekClient с фейковым HTTP-транспортом."""
from __future__ import annotations

import pytest
import requests

from infra.deepseek_client import DeepSeekClient


class _FakeResponse:
    def __init__(self, json_data, status_code: int = 200):
        self._json = json_data
        self.status_code = status_code

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class _FakePost:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({
            "url":     url,
            "headers": headers or {},
            "json":    json,
            "timeout": timeout,
        })
        if not self._responses:
            raise AssertionError(f"Unexpected POST to {url}")
        return self._responses.pop(0)


def _client(http_post):
    return DeepSeekClient(
        api_key="K",
        chat_url="https://api.deepseek.com/chat/completions",
        http_post=http_post,
    )


def test_chat_calls_endpoint_with_bearer_token():
    fake = _FakePost([_FakeResponse({"choices": [{"message": {"content": "ответ"}}]})])
    client = _client(fake)
    reply = client.chat(
        [{"role": "user", "content": "привет"}],
        {"model": "deepseek-chat"},
    )
    assert reply == "ответ"
    assert len(fake.calls) == 1
    assert fake.calls[0]["url"] == "https://api.deepseek.com/chat/completions"
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer K"
    assert fake.calls[0]["headers"]["Content-Type"] == "application/json"
    assert fake.calls[0]["json"]["model"] == "deepseek-chat"


def test_chat_prepends_system_prompt_when_provided():
    fake = _FakePost([_FakeResponse({"choices": [{"message": {"content": "ok"}}]})])
    client = _client(fake)
    client.chat(
        [{"role": "user", "content": "q"}],
        {"model": "deepseek-chat"},
        system_prompt="системный",
    )
    sent = fake.calls[0]["json"]["messages"]
    assert sent[0] == {"role": "system", "content": "системный"}
    assert sent[1]["content"] == "q"


def test_chat_omits_optional_params_when_none():
    fake = _FakePost([_FakeResponse({"choices": [{"message": {"content": "ok"}}]})])
    client = _client(fake)
    client.chat(
        [{"role": "user", "content": "x"}],
        {"model": "m", "temperature": None, "max_tokens": None},
    )
    body = fake.calls[0]["json"]
    assert "temperature" not in body
    assert "max_tokens"  not in body


def test_chat_forwards_temperature_and_max_tokens():
    fake = _FakePost([_FakeResponse({"choices": [{"message": {"content": "ok"}}]})])
    client = _client(fake)
    client.chat(
        [{"role": "user", "content": "x"}],
        {"model": "m", "temperature": 0.3, "max_tokens": 500},
    )
    body = fake.calls[0]["json"]
    assert body["temperature"] == 0.3
    assert body["max_tokens"]  == 500


def test_chat_propagates_http_errors():
    fake = _FakePost([_FakeResponse({}, status_code=500)])
    client = _client(fake)
    with pytest.raises(requests.HTTPError):
        client.chat([{"role": "user", "content": "x"}], {"model": "m"})


def test_no_oauth_call_made():
    """В отличие от GigaChat, DeepSeek не делает отдельного OAuth-обмена."""
    fake = _FakePost([_FakeResponse({"choices": [{"message": {"content": "ok"}}]})])
    client = _client(fake)
    client.chat([{"role": "user", "content": "x"}], {"model": "m"})
    # Ровно один HTTP-запрос — сразу на /chat/completions.
    assert len(fake.calls) == 1
