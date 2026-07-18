"""Use case: AI-ревью пул-реквеста по его diff + RAG-контексту проекта.

Ассистент получает diff и список изменённых файлов, подмешивает релевантные
фрагменты документации и кода (RAG), и выдаёт структурированное ревью:
потенциальные баги, архитектурные проблемы, рекомендации.

Оркестрирует два порта — `RetrievalEngine` и `LLMClient` — и ничего не знает
об их реализациях (FAISS/Ollama, HTTP, gh). Diff приходит уже готовым (его
достаёт `DiffProvider` в composition root), поэтому use case чист и тестируется
на фейках.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.ports import LLMClient, RetrievalEngine
from domain.retrieval import RetrievedChunk

SYSTEM_PROMPT = (
    "Ты — старший инженер, делающий код-ревью пул-реквеста в проекте jarvis-cli. "
    "Проект придерживается слоистой архитектуры (cli → app → domain → infra), "
    "внедрения зависимостей через конструктор и обязательных тестов на фейках. "
    "Анализируй ТОЛЬКО присланный diff, опираясь на приведённый контекст из "
    "документации и кода проекта. Не выдумывай изменений, которых нет в diff. "
    "Ответ дай на русском строго в таком формате из трёх разделов Markdown:\n"
    "## 🐞 Потенциальные баги\n"
    "## 🏛 Архитектурные проблемы\n"
    "## 💡 Рекомендации\n"
    "В каждом пункте ссылайся на конкретный файл (и по возможности строку). "
    "Если в разделе замечаний нет — напиши «— замечаний нет». "
    "Будь конкретным и кратким, без общих слов."
)

# Diff может быть огромным; ограничиваем, чтобы не раздувать промпт и не упираться
# в контекст модели. Ревьюим «голову» изменений — обычно самое важное сверху.
_MAX_DIFF_CHARS = 12000

_EMPTY_REVIEW = ("В пул-реквесте нет изменений для ревью "
                 "(пустой diff и список файлов).")


@dataclass
class ReviewResult:
    """Результат AI-ревью пул-реквеста."""
    text: str
    sources: list[RetrievedChunk] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    used_context: bool = True


def _added_lines(diff: str, limit: int = 40) -> list[str]:
    """Содержательные добавленные строки diff (без заголовков `+++`)."""
    out: list[str] = []
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            body = line[1:].strip()
            if body:
                out.append(body)
        if len(out) >= limit:
            break
    return out


def _retrieval_query(changed_files: list[str], diff: str) -> str:
    """Собрать поисковый запрос к RAG из путей файлов и добавленного кода."""
    parts: list[str] = []
    if changed_files:
        parts.append("Изменённые файлы: " + ", ".join(changed_files))
    added = _added_lines(diff)
    if added:
        parts.append("\n".join(added))
    return "\n".join(parts).strip()


def _build_context(chunks: list[RetrievedChunk]) -> str:
    parts: list[str] = []
    for i, c in enumerate(chunks, 1):
        loc = c.section or c.title or c.source
        header = f"[{i}] {c.source}" + (f" — {loc}" if loc and loc != c.source else "")
        parts.append(f"{header}\n{c.text.strip()}")
    return "\n\n".join(parts)


def _clip_diff(diff: str) -> str:
    if len(diff) <= _MAX_DIFF_CHARS:
        return diff
    return diff[:_MAX_DIFF_CHARS] + "\n… (diff обрезан по размеру)"


def review_pull_request(
    diff: str,
    changed_files: list[str],
    engine: Optional[RetrievalEngine],
    client: LLMClient,
    params: dict,
    top_k: int = 5,
) -> ReviewResult:
    """Найти по diff релевантный контекст проекта и сгенерировать текст ревью."""
    changed_files = list(changed_files or [])
    diff = diff or ""

    if not diff.strip() and not changed_files:
        return ReviewResult(
            text=_EMPTY_REVIEW,
            sources=[],
            files=changed_files,
            used_context=False,
        )

    chunks: list[RetrievedChunk] = []
    if engine is not None and engine.is_ready():
        query = _retrieval_query(changed_files, diff)
        if query:
            chunks = engine.retrieve(query, top_k=top_k)

    context = _build_context(chunks) if chunks else "(контекст проекта не найден)"
    files_line = ", ".join(changed_files) if changed_files else "(список файлов недоступен)"
    user_msg = (
        f"Контекст проекта (документация и код):\n\n{context}\n\n"
        f"---\nИзменённые файлы: {files_line}\n\n"
        f"---\nDiff пул-реквеста:\n\n{_clip_diff(diff)}"
    )
    messages = [{"role": "user", "content": user_msg}]

    aux = dict(params)
    aux.setdefault("temperature", 0.2)  # фактологичный разбор, минимум фантазии
    text = client.chat(messages, aux, SYSTEM_PROMPT)

    return ReviewResult(text=text, sources=chunks, files=changed_files)
