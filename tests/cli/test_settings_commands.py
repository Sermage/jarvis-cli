"""Тесты choose_model с динамическим списком моделей Ollama."""
from __future__ import annotations

import pytest

from cli.settings_commands import choose_model


class _OllamaClientStub:
    def __init__(self, models: list[str]):
        self._models = models

    def list_models(self) -> list[str]:
        return self._models

    def chat(self, *a, **kw) -> str:
        return ""


def test_choose_model_ollama_dynamic_list(monkeypatch):
    params = {"model": "qwen2.5:14b"}
    client = _OllamaClientStub(["qwen2.5:14b", "llama3.1:8b"])
    monkeypatch.setattr("builtins.input", lambda _: "2")
    choose_model(params, "ollama", llm_client=client)
    assert params["model"] == "llama3.1:8b"


def test_choose_model_ollama_enter_keeps_current(monkeypatch):
    params = {"model": "qwen2.5:14b"}
    client = _OllamaClientStub(["qwen2.5:14b", "llama3.1:8b"])
    monkeypatch.setattr("builtins.input", lambda _: "")
    choose_model(params, "ollama", llm_client=client)
    assert params["model"] == "qwen2.5:14b"


def test_choose_model_ollama_no_models_prints_warning(monkeypatch, capsys):
    params = {"model": "qwen2.5:14b"}
    client = _OllamaClientStub([])
    choose_model(params, "ollama", llm_client=client)
    out = capsys.readouterr().out
    assert "не отвечает" in out or "нет установленных" in out


def test_choose_model_ollama_without_client_falls_back_to_static(monkeypatch):
    params = {"model": "qwen2.5:14b"}
    monkeypatch.setattr("builtins.input", lambda _: "")
    # Без клиента — статический список из config
    choose_model(params, "ollama", llm_client=None)
    assert params["model"] == "qwen2.5:14b"


def test_choose_model_deepseek_uses_static_list(monkeypatch):
    params = {"model": "deepseek-chat"}
    monkeypatch.setattr("builtins.input", lambda _: "2")
    choose_model(params, "deepseek")
    assert params["model"] == "deepseek-reasoner"
