"""Use case: AI-ассистент поддержки пользователей.

`/support <вопрос> [#T-1024]` — ассистент отвечает на вопрос о продукте,
опираясь на:

  • RAG по FAQ/документации (порт `RetrievalEngine`) — фактическая база
    ответов, чтобы не выдумывать;
  • контекст пользователя/тикета через MCP (тулы `support__*`, которые
    агент вызывает сам в tool-loop) — чтобы ответ учитывал тариф, способ
    входа, историю обращения.

Оркеструет два порта и ничего не знает об их реализациях (markdown-FAQ или
FAISS; JSON-стор тикетов или реальный CRM). Это прямой аналог
`app/project_help.py`, но FAQ вместо доков и тикеты вместо git.

Тикеты берутся именно через MCP-механизм (`SupportChat` = `ToolRouter`),
поэтому «агент сам выбирает инструмент». Если tool-loop недоступен
(провайдер без tool calling), `PlainChatAdapter` даёт деградацию: ответ
только по FAQ, без данных тикета.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol

from app.ports import LLMClient, RetrievalEngine
from domain.retrieval import RetrievedChunk

SYSTEM_PROMPT = (
    "Ты — ассистент службы поддержки продукта. Твоя задача — помочь "
    "пользователю.\n"
    "Правила:\n"
    "1. Отвечай по существу вопроса, опираясь на приведённый КОНТЕКСТ FAQ. "
    "Если в FAQ нет ответа — честно скажи об этом, не выдумывай факты о продукте.\n"
    "2. Если известен номер тикета или пользователя — ОБЯЗАТЕЛЬНО вызови тулы "
    "support__get_ticket / support__get_user, чтобы учесть контекст: тариф, "
    "способ входа, платформу, историю переписки. Ответ должен быть адресным, "
    "а не общим.\n"
    "3. При необходимости посмотри похожие обращения через support__search_tickets.\n"
    "4. Пиши кратко, по-человечески, на языке вопроса. Укажи, из какого раздела "
    "FAQ взят ответ. Если проблему нельзя решить по FAQ — предложи следующий шаг "
    "(эскалация, нужные данные)."
)


class SupportChat(Protocol):
    """Канал общения с моделью, умеющий tool-loop по MCP-тулам.

    Реализуется `app.tool_router.ToolRouter` (полноценный tool-loop) и
    `PlainChatAdapter` (деградация без тулов). Use case знает только про этот
    протокол, поэтому в тестах подменяется фейком.
    """

    def chat(self, messages: list, params: dict,
             system_prompt: Optional[str] = None,
             on_event=None): ...


@dataclass
class _PlainResult:
    reply: str
    trace: list = field(default_factory=list)
    truncated: bool = False


class PlainChatAdapter:
    """Оборачивает обычный LLMClient в интерфейс SupportChat (без тулов).

    Нужен, когда провайдер не умеет tool calling: ассистент всё равно
    отвечает по FAQ, просто не может подтянуть данные тикета.
    """

    def __init__(self, client: LLMClient):
        self._client = client

    def chat(self, messages: list, params: dict,
             system_prompt: Optional[str] = None, on_event=None) -> _PlainResult:
        reply = self._client.chat(messages, params, system_prompt)
        return _PlainResult(reply=reply)


@dataclass
class ToolUse:
    """Свёрнутый след одного обращения к тулу поддержки — для показа в UI."""
    server_id: str
    tool_name: str
    arguments: dict
    is_error: bool


@dataclass
class SupportAnswer:
    """Результат ответа ассистента поддержки."""
    reply: str
    sources: list[RetrievedChunk] = field(default_factory=list)
    tools_used: list[ToolUse] = field(default_factory=list)
    ticket_id: Optional[str] = None
    used_faq: bool = True
    truncated: bool = False


def _build_context(chunks: list[RetrievedChunk], ticket_hint: Optional[str]) -> str:
    parts: list[str] = []
    if ticket_hint:
        parts.append(
            f"[тикет] В вопросе упомянут тикет {ticket_hint}. "
            f"Сначала вызови support__get_ticket с ticket_id={ticket_hint}."
        )
    if chunks:
        parts.append("[FAQ] Релевантные разделы базы знаний:")
        for i, c in enumerate(chunks, 1):
            loc = c.section or c.title or c.source
            header = f"[{i}] {c.source}" + (f" — {loc}" if loc and loc != c.source else "")
            parts.append(f"{header}\n{c.text.strip()}")
    else:
        parts.append("[FAQ] По этому вопросу в базе ничего не нашлось.")
    return "\n\n".join(parts)


def answer_support_question(
    question: str,
    ticket_hint: Optional[str],
    engine: Optional[RetrievalEngine],
    support_chat: SupportChat,
    params: dict,
    top_k: int = 5,
    on_event=None,
) -> SupportAnswer:
    """Найти релевантный FAQ, дать агенту тулы тикетов, вернуть адресный ответ."""
    question = question.strip()

    chunks: list[RetrievedChunk] = []
    if engine is not None and engine.is_ready():
        chunks = engine.retrieve(question, top_k=top_k)

    context = _build_context(chunks, ticket_hint)
    user_msg = f"{context}\n\n---\nВопрос пользователя: {question}"
    messages = [{"role": "user", "content": user_msg}]

    aux = dict(params)
    aux.setdefault("temperature", 0.2)  # фактологичный ответ, минимум фантазии

    result = support_chat.chat(messages, aux, SYSTEM_PROMPT, on_event)

    tools_used = [
        ToolUse(
            server_id=inv.server_id,
            tool_name=inv.tool_name,
            arguments=inv.arguments,
            is_error=inv.is_error,
        )
        for inv in getattr(result, "trace", [])
    ]

    return SupportAnswer(
        reply=result.reply,
        sources=chunks,
        tools_used=tools_used,
        ticket_id=ticket_hint,
        used_faq=bool(chunks),
        truncated=getattr(result, "truncated", False),
    )
