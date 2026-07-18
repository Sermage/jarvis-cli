#!/usr/bin/env python3
"""AI-ревью пул-реквеста — composition root для CI (GitHub Action).

Запуск:
    python3 review_pr.py <номер PR> [--no-comment]

Собирает RAG по двум индексам (документация + код), LLM-клиент и gh-провайдеры,
гоняет use case `review_pull_request`, печатает ревью и по умолчанию публикует
его комментарием в PR. `--no-comment` — только напечатать, ничего не постить.

Провайдер LLM берётся из LLM_PROVIDER (окружение раннера или .env рядом со
скриптом), ключи — из окружения, как и в основном CLI. `gh` должен быть
авторизован (в CI — через GH_TOKEN).
"""
from __future__ import annotations

import os
import sys

from app.pr_review import review_pull_request
from cli.config import (
    DEFAULT_EMBED_MODEL,
    DEFAULT_OLLAMA_URL,
    DEFAULT_PARAMS,
    code_index_path,
    default_model_for,
    load_env,
    load_rag_config,
    resolve_provider,
)
from cli.main import _build_client
from infra.pr_diff import GhDiffProvider
from infra.rag_retrieval import CompositeRetrievalEngine, FaissOllamaRetrievalEngine
from infra.review_publisher import GhReviewPublisher


def _format_comment(result) -> str:
    """Оформить ревью как Markdown-комментарий к PR."""
    lines = ["## 🤖 AI-ревью", "", result.text.strip()]
    if result.sources:
        locs: list[str] = []
        for c in result.sources:
            loc = c.section or c.title or c.source
            label = loc if loc == c.source else f"{c.source} — {loc}"
            if label not in locs:
                locs.append(label)
        lines += ["", "---", "_Контекст (RAG): " + " · ".join(locs) + "_"]
    lines += ["", "_Сгенерировано jarvis-cli · review_pr.py_"]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    positional = [a for a in argv if not a.startswith("-")]
    flags = {a for a in argv if a.startswith("-")}
    if not positional:
        print("Использование: python3 review_pr.py <номер PR> [--no-comment]",
              file=sys.stderr)
        return 2
    pr = positional[0]

    # .env лежит рядом с этим скриптом (в CI обычно отсутствует — тогда ключи
    # приходят из окружения раннера).
    load_env(os.path.join(os.path.dirname(os.path.realpath(__file__)), ".env"))

    provider = resolve_provider(os.environ.get("LLM_PROVIDER", ""))
    params = dict(DEFAULT_PARAMS)
    params["model"] = default_model_for(provider)
    client = _build_client(provider)

    rag_config = load_rag_config()
    docs_engine = FaissOllamaRetrievalEngine(
        index_path=rag_config.index_path, strategy=rag_config.strategy,
        embed_model=DEFAULT_EMBED_MODEL, ollama_url=DEFAULT_OLLAMA_URL)
    code_engine = FaissOllamaRetrievalEngine(
        index_path=code_index_path(), strategy=rag_config.strategy,
        embed_model=DEFAULT_EMBED_MODEL, ollama_url=DEFAULT_OLLAMA_URL)
    engine = CompositeRetrievalEngine([docs_engine, code_engine])

    diff_provider = GhDiffProvider()

    print(f"Достаю diff PR #{pr} через gh…", file=sys.stderr)
    pr_diff = diff_provider.fetch(pr)
    print(f"Изменено файлов: {len(pr_diff.files)}. Генерирую ревью "
          f"(provider={provider}, RAG={'on' if engine.is_ready() else 'off'})…",
          file=sys.stderr)

    result = review_pull_request(pr_diff.diff, pr_diff.files, engine, client,
                                 params, top_k=rag_config.top_k)

    body = _format_comment(result)
    print(body)

    if "--no-comment" not in flags:
        GhReviewPublisher().publish(pr, body)
        print(f"\nРевью опубликовано комментарием в PR #{pr}.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
