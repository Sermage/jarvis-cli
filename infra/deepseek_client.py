"""HTTP-клиент DeepSeek поверх `requests`.

DeepSeek предоставляет OpenAI-совместимый API: один POST на
`/chat/completions` с Bearer-токеном, без отдельного OAuth-обмена.
Зависимости (транспорт) инжектятся через конструктор для тестируемости.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

import requests


class DeepSeekClient:
    """Реализация LLMClient поверх DeepSeek API.

    Транспорт ожидается совместимым с `requests.post`:
    принимает `url, headers=..., json=..., timeout=...` и возвращает объект
    с методами `raise_for_status()` и `json()`.
    """

    def __init__(self,
                 api_key: str,
                 chat_url: str,
                 http_post: Optional[Callable[..., Any]] = None,
                 chat_timeout: int = 60):
        self._api_key      = api_key
        self._chat_url     = chat_url
        self._post         = http_post or requests.post
        self._chat_timeout = chat_timeout

    def chat(self,
             messages: list,
             params: dict,
             system_prompt: Optional[str] = None) -> str:
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
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type":  "application/json",
            },
            json=body,
            timeout=self._chat_timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]
