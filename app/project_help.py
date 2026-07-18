"""Use case: ответить на вопрос о проекте по его документации.

`/help <вопрос>` — ассистент отвечает, опираясь на RAG-поиск по документации
проекта (README + папка docs + CLAUDE.md) и на текущий git-контекст, полученный
через MCP. Оркестрирует три порта — `RetrievalEngine`, `GitContextProvider`,
`LLMClient` — и ничего не знает об их реализациях (FAISS, mcp-server-git, HTTP).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.ports import GitContextProvider, LLMClient, RetrievalEngine
from domain.retrieval import RetrievedChunk

SYSTEM_PROMPT = (
    "Ты — ассистент по проекту jarvis-cli. Отвечай на вопрос ТОЛЬКО по "
    "приведённому контексту из документации проекта. "
    "Если контекста недостаточно — честно скажи об этом, не выдумывай. "
    "Указывай конкретные файлы/разделы, откуда взят ответ. "
    "Отвечай кратко и по делу, на языке вопроса."
)


@dataclass
class ProjectHelpResult:
    """Результат ответа на вопрос о проекте."""
    reply: str
    sources: list[RetrievedChunk] = field(default_factory=list)
    branch: Optional[str] = None
    used_context: bool = True


def _build_context(chunks: list[RetrievedChunk], branch: Optional[str]) -> str:
    parts: list[str] = []
    if branch:
        parts.append(f"[git] Текущая ветка проекта: {branch}")
    for i, c in enumerate(chunks, 1):
        loc = c.section or c.title or c.source
        header = f"[{i}] {c.source}" + (f" — {loc}" if loc and loc != c.source else "")
        parts.append(f"{header}\n{c.text.strip()}")
    return "\n\n".join(parts)


def answer_project_question(
    question: str,
    engine: RetrievalEngine,
    git: Optional[GitContextProvider],
    client: LLMClient,
    params: dict,
    top_k: int = 5,
) -> ProjectHelpResult:
    """Найти релевантные куски документации, подмешать git-ветку, спросить LLM."""
    question = question.strip()
    branch = None
    if git is not None:
        try:
            branch = git.current_branch()
        except Exception:
            branch = None  # git-контекст необязателен — /help работает и без него

    chunks: list[RetrievedChunk] = []
    if engine is not None and engine.is_ready():
        chunks = engine.retrieve(question, top_k=top_k)

    if not chunks and not branch:
        return ProjectHelpResult(
            reply="Не нашёл в документации проекта ничего по этому вопросу. "
                  "Проверь, что RAG-индекс собран и путь RAG_INDEX_PATH указывает "
                  "на него (см. /rag status).",
            sources=[],
            branch=branch,
            used_context=False,
        )

    context = _build_context(chunks, branch)
    user_msg = f"Контекст проекта:\n\n{context}\n\n---\nВопрос: {question}"
    messages = [{"role": "user", "content": user_msg}]

    aux = dict(params)
    aux.setdefault("temperature", 0.2)  # фактологичный ответ, минимум фантазии
    reply = client.chat(messages, aux, SYSTEM_PROMPT)

    return ProjectHelpResult(reply=reply, sources=chunks, branch=branch)
