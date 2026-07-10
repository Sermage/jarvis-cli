"""Тесты OllamaClient с фейковым HTTP-транспортом."""
from __future__ import annotations

import pytest
import requests

from infra.ollama_client import OllamaClient


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
        self.calls.append({"url": url, "headers": headers or {}, "json": json})
        if not self._responses:
            raise AssertionError(f"Unexpected POST to {url}")
        return self._responses.pop(0)


def _ok(content: str = "ответ"):
    return _FakeResponse({"choices": [{"message": {"content": content}}]})


def _client(fake_post, base_url: str = "http://localhost:11434"):
    return OllamaClient(base_url=base_url, http_post=fake_post)


def test_chat_calls_v1_endpoint():
    fake = _FakePost([_ok()])
    _client(fake).chat([{"role": "user", "content": "привет"}], {"model": "qwen2.5:14b"})
    assert fake.calls[0]["url"] == "http://localhost:11434/v1/chat/completions"


def test_chat_no_authorization_header():
    fake = _FakePost([_ok()])
    _client(fake).chat([{"role": "user", "content": "x"}], {"model": "m"})
    assert "Authorization" not in fake.calls[0]["headers"]


def test_chat_returns_content():
    fake = _FakePost([_ok("результат")])
    reply = _client(fake).chat([{"role": "user", "content": "x"}], {"model": "m"})
    assert reply == "результат"


def test_chat_prepends_system_prompt():
    fake = _FakePost([_ok()])
    _client(fake).chat(
        [{"role": "user", "content": "q"}],
        {"model": "m"},
        system_prompt="системный",
    )
    sent = fake.calls[0]["json"]["messages"]
    assert sent[0] == {"role": "system", "content": "системный"}
    assert sent[1]["content"] == "q"


def test_chat_omits_none_params():
    fake = _FakePost([_ok()])
    _client(fake).chat(
        [{"role": "user", "content": "x"}],
        {"model": "m", "temperature": None, "max_tokens": None, "num_ctx": None},
    )
    body = fake.calls[0]["json"]
    assert "temperature" not in body
    assert "max_tokens" not in body
    assert "options" not in body


def test_chat_forwards_temperature_and_max_tokens():
    fake = _FakePost([_ok()])
    _client(fake).chat(
        [{"role": "user", "content": "x"}],
        {"model": "m", "temperature": 0.7, "max_tokens": 256},
    )
    body = fake.calls[0]["json"]
    assert body["temperature"] == 0.7
    assert body["max_tokens"] == 256


def test_chat_forwards_num_ctx_via_options():
    fake = _FakePost([_ok()])
    _client(fake).chat(
        [{"role": "user", "content": "x"}],
        {"model": "m", "num_ctx": 8192},
    )
    body = fake.calls[0]["json"]
    assert body["options"] == {"num_ctx": 8192}


def test_chat_omits_options_when_num_ctx_is_none():
    fake = _FakePost([_ok()])
    _client(fake).chat(
        [{"role": "user", "content": "x"}],
        {"model": "m", "num_ctx": None},
    )
    body = fake.calls[0]["json"]
    assert "options" not in body


def test_chat_propagates_http_error():
    fake = _FakePost([_FakeResponse({}, status_code=500)])
    with pytest.raises(requests.HTTPError):
        _client(fake).chat([{"role": "user", "content": "x"}], {"model": "m"})


def test_base_url_trailing_slash_stripped():
    fake = _FakePost([_ok()])
    OllamaClient(base_url="http://localhost:11434/", http_post=fake).chat(
        [{"role": "user", "content": "x"}], {"model": "m"}
    )
    assert fake.calls[0]["url"] == "http://localhost:11434/v1/chat/completions"


def test_custom_base_url():
    fake = _FakePost([_ok()])
    OllamaClient(base_url="http://192.168.1.10:11434", http_post=fake).chat(
        [{"role": "user", "content": "x"}], {"model": "m"}
    )
    assert fake.calls[0]["url"] == "http://192.168.1.10:11434/v1/chat/completions"


# ── list_models ───────────────────────────────────────────────────────────────


class _FakeGet:
    def __init__(self, json_data, status_code: int = 200):
        self._json = json_data
        self.status_code = status_code
        self.calls: list[str] = []

    def __call__(self, url, timeout=None):
        self.calls.append(url)
        return _FakeResponse(self._json, self.status_code)


def test_list_models_returns_chat_model_names():
    fake_get = _FakeGet({"models": [
        {"name": "qwen2.5:14b"},
        {"name": "llama3.1:8b"},
    ]})
    client = OllamaClient(base_url="http://localhost:11434", http_get=fake_get)
    assert client.list_models() == ["qwen2.5:14b", "llama3.1:8b"]
    assert fake_get.calls[0] == "http://localhost:11434/api/tags"


def test_list_models_filters_embedding_models():
    fake_get = _FakeGet({"models": [
        {"name": "qwen2.5:14b"},
        {"name": "bge-m3:latest"},
        {"name": "nomic-embed-text:latest"},
        {"name": "mxbai-embed-large:latest"},
    ]})
    client = OllamaClient(base_url="http://localhost:11434", http_get=fake_get)
    assert client.list_models() == ["qwen2.5:14b"]


def test_list_models_returns_empty_on_connection_error():
    def _fail(url, timeout=None):
        raise ConnectionError("сервер недоступен")

    client = OllamaClient(base_url="http://localhost:11434", http_get=_fail)
    assert client.list_models() == []


def test_list_models_returns_empty_on_http_error():
    fake_get = _FakeGet({}, status_code=500)
    client = OllamaClient(base_url="http://localhost:11434", http_get=fake_get)
    assert client.list_models() == []
