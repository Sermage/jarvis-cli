"""HTTP-клиент Ollama поверх `requests`.

Ollama предоставляет OpenAI-совместимый endpoint:
    POST http://localhost:11434/v1/chat/completions
Авторизация не нужна — сервер локальный.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

import requests


class OllamaClient:
    """Реализация LLMClient поверх Ollama OpenAI-совместимого API."""

    def __init__(self,
                 base_url: str,
                 http_post: Optional[Callable[..., Any]] = None,
                 http_get: Optional[Callable[..., Any]] = None,
                 chat_timeout: int = 120):
        self._base_url  = base_url.rstrip("/")
        self._chat_url  = self._base_url + "/v1/chat/completions"
        self._post      = http_post or requests.post
        self._get       = http_get  or requests.get
        self._chat_timeout = chat_timeout

    def chat(self,
             messages: list,
             params: dict,
             system_prompt: Optional[str] = None) -> str:
        body = self._build_body(messages, params, system_prompt)
        resp = self._post(
            self._chat_url,
            headers={"Content-Type": "application/json"},
            json=body,
            timeout=self._chat_timeout,
        )
        resp.raise_for_status()
        msg = resp.json()["choices"][0]["message"]
        return msg.get("content") or ""

    def list_models(self) -> list[str]:
        """Вернуть имена chat-моделей, установленных в Ollama.

        Embedding-модели (bge-*, nomic-*) отфильтровываются — они не годятся
        для чата. При недоступности сервера возвращает пустой список.
        """
        _EMBED_PREFIXES = ("bge-", "nomic-", "mxbai-embed", "snowflake-arctic")
        try:
            resp = self._get(self._base_url + "/api/tags", timeout=5)
            resp.raise_for_status()
            models = resp.json().get("models", [])
            return [
                m["name"] for m in models
                if not any(m["name"].startswith(p) for p in _EMBED_PREFIXES)
            ]
        except Exception:
            return []

    def _build_body(self,
                    messages: list,
                    params: dict,
                    system_prompt: Optional[str]) -> dict:
        api_messages = messages
        if system_prompt:
            api_messages = [{"role": "system", "content": system_prompt}] + messages
        body: dict = {"model": params["model"], "messages": api_messages}
        if params.get("temperature") is not None:
            body["temperature"] = params["temperature"]
        if params.get("max_tokens") is not None:
            body["max_tokens"] = params["max_tokens"]
        # num_ctx расширяет контекстное окно выше дефолтных 2048 Ollama.
        # Передаётся в Ollama-специфичный блок options (не часть OpenAI-spec).
        if params.get("num_ctx") is not None:
            body["options"] = {"num_ctx": params["num_ctx"]}
        return body
