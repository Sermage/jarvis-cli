"""Тесты RequestsGigaChatClient с фейковым HTTP-транспортом."""
from __future__ import annotations

import pytest
import requests

from infra.gigachat_client import RequestsGigaChatClient


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
    """Фейковый транспорт, отвечающий заранее заданными ответами."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, url, headers=None, data=None, json=None, verify=None, timeout=None):
        self.calls.append({
            "url":     url,
            "headers": headers or {},
            "data":    data,
            "json":    json,
            "verify":  verify,
            "timeout": timeout,
        })
        if not self._responses:
            raise AssertionError(f"Unexpected POST to {url}")
        return self._responses.pop(0)


def _client(http_post, *, now=None, expiry_buffer=60):
    return RequestsGigaChatClient(
        auth_key="AUTH",
        oauth_url="https://oauth.test/token",
        chat_url="https://chat.test/v1/completions",
        scope="GIGACHAT_API_PERS",
        http_post=http_post,
        now=now or (lambda: 1000.0),
        token_expiry_buffer=expiry_buffer,
        verify_ssl=False,
    )


def test_chat_obtains_token_then_calls_chat_endpoint():
    fake = _FakePost([
        _FakeResponse({"access_token": "T1", "expires_at": 2_000_000}),
        _FakeResponse({"choices": [{"message": {"content": "ответ"}}]}),
    ])
    client = _client(fake)
    reply = client.chat([{"role": "user", "content": "привет"}], {"model": "GigaChat"})
    assert reply == "ответ"
    assert len(fake.calls) == 2
    assert fake.calls[0]["url"] == "https://oauth.test/token"
    assert fake.calls[0]["data"] == {"scope": "GIGACHAT_API_PERS"}
    assert fake.calls[0]["headers"]["Authorization"] == "Basic AUTH"
    assert "RqUID" in fake.calls[0]["headers"]
    assert fake.calls[1]["url"] == "https://chat.test/v1/completions"
    assert fake.calls[1]["headers"]["Authorization"] == "Bearer T1"
    assert fake.calls[1]["json"]["model"] == "GigaChat"


def test_chat_reuses_cached_token_for_subsequent_calls():
    fake = _FakePost([
        _FakeResponse({"access_token": "T1", "expires_at": 2_000_000}),
        _FakeResponse({"choices": [{"message": {"content": "1"}}]}),
        _FakeResponse({"choices": [{"message": {"content": "2"}}]}),
    ])
    client = _client(fake, now=lambda: 1000.0)
    client.chat([{"role": "user", "content": "a"}], {"model": "m"})
    client.chat([{"role": "user", "content": "b"}], {"model": "m"})
    # Один OAuth + два чата.
    assert [c["url"] for c in fake.calls] == [
        "https://oauth.test/token",
        "https://chat.test/v1/completions",
        "https://chat.test/v1/completions",
    ]


def test_chat_refreshes_token_when_expired():
    fake = _FakePost([
        _FakeResponse({"access_token": "T1", "expires_at": 2_000_000}),  # истечёт в 2000.0
        _FakeResponse({"choices": [{"message": {"content": "1"}}]}),
        _FakeResponse({"access_token": "T2", "expires_at": 3_000_000}),
        _FakeResponse({"choices": [{"message": {"content": "2"}}]}),
    ])
    # Первый chat: токена нет, now() не дёргается. Второй: проверка истечения — now() > 2000.
    client = _client(fake, now=lambda: 2050.0, expiry_buffer=0)
    assert client.chat([{"role": "user", "content": "a"}], {"model": "m"}) == "1"
    assert client.chat([{"role": "user", "content": "b"}], {"model": "m"}) == "2"
    assert fake.calls[2]["url"] == "https://oauth.test/token"
    assert fake.calls[3]["headers"]["Authorization"] == "Bearer T2"


def test_chat_prepends_system_prompt_when_provided():
    fake = _FakePost([
        _FakeResponse({"access_token": "T1", "expires_at": 2_000_000}),
        _FakeResponse({"choices": [{"message": {"content": "ok"}}]}),
    ])
    client = _client(fake)
    client.chat(
        [{"role": "user", "content": "запрос"}],
        {"model": "m"},
        system_prompt="системный",
    )
    sent = fake.calls[1]["json"]["messages"]
    assert sent[0] == {"role": "system", "content": "системный"}
    assert sent[1]["content"] == "запрос"


def test_chat_omits_optional_params_when_none():
    fake = _FakePost([
        _FakeResponse({"access_token": "T1", "expires_at": 2_000_000}),
        _FakeResponse({"choices": [{"message": {"content": "ok"}}]}),
    ])
    client = _client(fake)
    client.chat([{"role": "user", "content": "x"}],
                {"model": "m", "temperature": None, "max_tokens": None})
    body = fake.calls[1]["json"]
    assert "temperature" not in body
    assert "max_tokens"  not in body


def test_chat_forwards_temperature_and_max_tokens():
    fake = _FakePost([
        _FakeResponse({"access_token": "T1", "expires_at": 2_000_000}),
        _FakeResponse({"choices": [{"message": {"content": "ok"}}]}),
    ])
    client = _client(fake)
    client.chat([{"role": "user", "content": "x"}],
                {"model": "m", "temperature": 0.3, "max_tokens": 500})
    body = fake.calls[1]["json"]
    assert body["temperature"] == 0.3
    assert body["max_tokens"]  == 500


def test_chat_propagates_http_errors():
    fake = _FakePost([
        _FakeResponse({"access_token": "T1", "expires_at": 2_000_000}),
        _FakeResponse({}, status_code=500),
    ])
    client = _client(fake)
    with pytest.raises(requests.HTTPError):
        client.chat([{"role": "user", "content": "x"}], {"model": "m"})


def test_oauth_failure_propagates():
    fake = _FakePost([_FakeResponse({}, status_code=401)])
    client = _client(fake)
    with pytest.raises(requests.HTTPError):
        client.chat([{"role": "user", "content": "x"}], {"model": "m"})


def test_no_token_reuse_across_clients():
    """Состояние токена — поле экземпляра, не модульное."""
    fake_a = _FakePost([
        _FakeResponse({"access_token": "A", "expires_at": 2_000_000}),
        _FakeResponse({"choices": [{"message": {"content": "a"}}]}),
    ])
    fake_b = _FakePost([
        _FakeResponse({"access_token": "B", "expires_at": 2_000_000}),
        _FakeResponse({"choices": [{"message": {"content": "b"}}]}),
    ])
    _client(fake_a).chat([{"role": "user", "content": "x"}], {"model": "m"})
    _client(fake_b).chat([{"role": "user", "content": "x"}], {"model": "m"})
    assert fake_a.calls[1]["headers"]["Authorization"] == "Bearer A"
    assert fake_b.calls[1]["headers"]["Authorization"] == "Bearer B"
