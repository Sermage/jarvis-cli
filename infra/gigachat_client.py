"""HTTP-клиент GigaChat поверх `requests`.

Инкапсулирует OAuth, токен-кэш и приведение тела ответа. Зависимости
(транспорт, часы) инжектятся через конструктор для тестируемости.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Callable, Optional

import requests


class RequestsGigaChatClient:
    """Реализация GigaChatClient поверх `requests`.

    Транспорт ожидается совместимым с `requests.post` сигнатурой:
    он должен принимать `url, headers=..., data=..., json=..., verify=..., timeout=...`
    и возвращать объект с методами `raise_for_status()` и `json()`.
    """

    def __init__(self,
                 auth_key: str,
                 oauth_url: str,
                 chat_url: str,
                 scope: str,
                 http_post: Optional[Callable[..., Any]] = None,
                 now: Optional[Callable[[], float]] = None,
                 token_expiry_buffer: int = 60,
                 verify_ssl: bool = False,
                 oauth_timeout: int = 15,
                 chat_timeout: int = 60):
        self._auth_key            = auth_key
        self._oauth_url           = oauth_url
        self._chat_url            = chat_url
        self._scope               = scope
        self._post                = http_post or requests.post
        self._now                 = now or time.time
        self._token_expiry_buffer = token_expiry_buffer
        self._verify_ssl          = verify_ssl
        self._oauth_timeout       = oauth_timeout
        self._chat_timeout        = chat_timeout

        # Кэш токена — состояние экземпляра, не модуля.
        self._token: Optional[str]  = None
        self._token_expires_at: float = 0.0

    # ── public API ───────────────────────────────────────────────────────────

    def chat(self,
             messages: list,
             params: dict,
             system_prompt: Optional[str] = None) -> str:
        token = self._get_token()
        api_messages = messages
        if system_prompt:
            api_messages = [{"role": "system", "content": system_prompt}] + messages
        body: dict = {"model": params["model"], "messages": api_messages}
        if params.get("temperature") is not None:
            body["temperature"] = params["temperature"]
        if params.get("max_tokens") is not None:
            body["max_tokens"] = params["max_tokens"]

        resp = self._post(
            self._chat_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type":  "application/json",
            },
            json=body,
            verify=self._verify_ssl,
            timeout=self._chat_timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    # ── internals ────────────────────────────────────────────────────────────

    def _get_token(self) -> str:
        if self._token and self._now() < self._token_expires_at - self._token_expiry_buffer:
            return self._token
        resp = self._post(
            self._oauth_url,
            headers={
                "Authorization": f"Basic {self._auth_key}",
                "RqUID":         str(uuid.uuid4()),
                "Content-Type":  "application/x-www-form-urlencoded",
            },
            data={"scope": self._scope},
            verify=self._verify_ssl,
            timeout=self._oauth_timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data["access_token"]
        # expires_at у Сбера в миллисекундах.
        self._token_expires_at = data["expires_at"] / 1000
        return self._token
