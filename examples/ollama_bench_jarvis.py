"""Бенчмарк Qwen 2.5 14b на реальных вопросах по коду jarvis-cli.

Запуск:
    .venv/bin/python3 examples/ollama_bench_jarvis.py

Сравнивает три конфигурации:
  baseline   — дефолт Ollama (num_ctx=2048, temperature не задана)
  code-opt   — num_ctx=8192, temperature=0.3  (рекомендуется для кода)
  code-ctx32 — num_ctx=32768, temperature=0.3 (полный контекст Qwen)

Вопросы берутся из реального кода jarvis-cli: понимание архитектуры,
разбор конкретных функций, поиск проблем, написание тестов, расширение кода.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

OLLAMA_URL = "http://localhost:11434"
MODEL      = "qwen2.5:14b"

SYSTEM_PROMPT = """\
Ты — опытный Python-разработчик. Отвечай кратко и конкретно.
Код пиши сразу, без предисловий. Если нужно объяснение — 1–3 предложения максимум.
Отвечай на русском."""

# ── реальный код из jarvis-cli (вставляется в вопросы) ─────────────────────

_OLLAMA_CLIENT = """\
class OllamaClient:
    def __init__(self, base_url, http_post=None, http_get=None, chat_timeout=120):
        self._base_url     = base_url.rstrip("/")
        self._chat_url     = self._base_url + "/v1/chat/completions"
        self._post         = http_post or requests.post
        self._get          = http_get  or requests.get
        self._chat_timeout = chat_timeout

    def chat(self, messages, params, system_prompt=None):
        body = self._build_body(messages, params, system_prompt)
        resp = self._post(self._chat_url,
                          headers={"Content-Type": "application/json"},
                          json=body, timeout=self._chat_timeout)
        resp.raise_for_status()
        msg = resp.json()["choices"][0]["message"]
        return msg.get("content") or ""

    def _build_body(self, messages, params, system_prompt):
        api_messages = messages
        if system_prompt:
            api_messages = [{"role": "system", "content": system_prompt}] + messages
        body = {"model": params["model"], "messages": api_messages}
        if params.get("temperature") is not None:
            body["temperature"] = params["temperature"]
        if params.get("max_tokens") is not None:
            body["max_tokens"] = params["max_tokens"]
        if params.get("num_ctx") is not None:
            body["options"] = {"num_ctx": params["num_ctx"]}
        return body"""

_GUARDED_CHAT = """\
def guarded_chat(client, messages, params, system_prompt, invariants, max_retries=1):
    reply = client.chat(messages, params, system_prompt)

    if invariants.is_empty():
        return GuardedResult(reply=reply)

    violations = invariants.check(reply)
    blocks = [v for v in violations if v.severity is InvariantSeverity.BLOCK]
    if not blocks:
        return GuardedResult(reply=reply, violations=tuple(violations))

    history = list(messages)
    last_violations = violations
    for attempt in range(1, max_retries + 1):
        history = history + [
            {"role": "assistant", "content": reply},
            {"role": "user",      "content": _feedback_text(blocks)},
        ]
        reply = client.chat(history, params, system_prompt)
        last_violations = invariants.check(reply)
        blocks = [v for v in last_violations if v.severity is InvariantSeverity.BLOCK]
        if not blocks:
            return GuardedResult(reply=reply, violations=tuple(last_violations),
                                 retries_used=attempt)

    return GuardedResult(reply=reply, violations=tuple(last_violations),
                         retries_used=max_retries, blocked=True)"""

_PORTS_LLMCLIENT = """\
class LLMClient(Protocol):
    def chat(self,
             messages: list,
             params: dict,
             system_prompt: Optional[str] = None) -> str: ...

class ToolCallingLLMClient(LLMClient, Protocol):
    def chat_with_tools(self,
                        messages: list,
                        params: dict,
                        tools: list,
                        system_prompt: Optional[str] = None) -> dict: ..."""

_INVARIANT_CHECK = """\
@dataclass(frozen=True)
class Invariant:
    id: str
    title: str
    rule: str
    severity: InvariantSeverity = InvariantSeverity.BLOCK
    enabled: bool = True
    forbidden_patterns: tuple[str, ...] = field(default_factory=tuple)
    required_patterns:  tuple[str, ...] = field(default_factory=tuple)

    def check(self, text: str) -> list[Violation]:
        if not self.enabled:
            return []
        violations = []
        for pat in self.forbidden_patterns:
            try:
                if re.search(pat, text, re.IGNORECASE):
                    violations.append(Violation(
                        invariant_id=self.id, title=self.title,
                        reason=f"текст содержит запрещённый паттерн: {pat!r}",
                        severity=self.severity,
                    ))
            except re.error:
                pass
        for pat in self.required_patterns:
            try:
                if not re.search(pat, text, re.IGNORECASE):
                    violations.append(Violation(
                        invariant_id=self.id, title=self.title,
                        reason=f"текст не содержит обязательный паттерн: {pat!r}",
                        severity=self.severity,
                    ))
            except re.error:
                pass
        return violations"""

# ── вопросы ─────────────────────────────────────────────────────────────────

QUESTIONS = [
    # Q1: понимание конкретного метода
    {
        "id": "q1_build_body",
        "category": "понимание кода",
        "prompt": f"""\
Вот метод `_build_body` из класса `OllamaClient` в jarvis-cli:

```python
{_OLLAMA_CLIENT}
```

Объясни в 2–3 предложениях: что делает `_build_body` и почему `system_prompt` \
вставляется именно первым элементом, а не передаётся отдельным параметром API.""",
        "quality_checks": [
            ("объясняет назначение метода", lambda r: "тело" in r.lower() or "body" in r.lower() or "запрос" in r.lower()),
            ("упоминает system как первый message", lambda r: "первым" in r.lower() or "system" in r.lower() or "openai" in r.lower()),
            ("краткость (< 600 символов)", lambda r: len(r) < 600),
        ],
    },
    # Q2: поиск архитектурной проблемы
    {
        "id": "q2_guarded_mutation",
        "category": "анализ кода",
        "prompt": f"""\
Вот функция из `app/invariant_guard.py`:

```python
{_GUARDED_CHAT}
```

Найди потенциальную проблему в этом коде. Подсказка: обрати внимание на \
то, как обрабатывается `history` при нескольких retry.""",
        "quality_checks": [
            ("замечает рост history при каждом retry", lambda r: (
                "растёт" in r.lower() or "накапл" in r.lower() or
                "добавля" in r.lower() or "удваива" in r.lower() or
                "каждом" in r.lower() or "history" in r.lower()
            )),
            ("упоминает что history пересоздаётся каждый раз", lambda r: (
                "снова" in r.lower() or "заново" in r.lower() or
                "каждой" in r.lower() or "итерации" in r.lower() or
                "retry" in r.lower() or "попытк" in r.lower()
            )),
        ],
    },
    # Q3: написание теста
    {
        "id": "q3_write_test",
        "category": "написание теста",
        "prompt": f"""\
В jarvis-cli тесты пишутся через DI с фейковыми HTTP-транспортами. Пример:

```python
class _FakePost:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
    def __call__(self, url, headers=None, json=None, timeout=None):
        self.calls.append({{"url": url, "json": json}})
        return self._responses.pop(0)
```

Напиши pytest-тест, который проверяет: если в `OllamaClient.chat()` передать \
`params={{"model": "qwen2.5:14b", "num_ctx": 8192}}`, то в теле HTTP-запроса \
присутствует `"options": {{"num_ctx": 8192}}`.""",
        "quality_checks": [
            ("есть def test_", lambda r: "def test_" in r),
            ("создаёт _FakePost или аналог", lambda r: "fake" in r.lower() or "FakePost" in r or "mock" in r.lower()),
            ("проверяет options в теле запроса", lambda r: '"options"' in r or "options" in r),
            ("использует assert", lambda r: "assert" in r),
        ],
    },
    # Q4: расширение кода — добавить метод
    {
        "id": "q4_add_streaming",
        "category": "расширение кода",
        "prompt": f"""\
Вот класс `OllamaClient` из `infra/ollama_client.py`:

```python
{_OLLAMA_CLIENT}
```

Добавь метод `chat_stream(messages, params, system_prompt=None)`, который \
вызывает тот же endpoint с `"stream": true` и возвращает генератор строк \
(каждый chunk — строка-дельта контента). Используй `resp.iter_lines()`.""",
        "quality_checks": [
            ("есть def chat_stream", lambda r: "def chat_stream" in r or "def stream" in r.lower()),
            ("передаёт stream=True в body", lambda r: '"stream"' in r or "stream" in r.lower()),
            ("использует iter_lines или iter_content", lambda r: "iter_lines" in r or "iter_content" in r),
            ("есть yield — это генератор", lambda r: "yield" in r),
        ],
    },
    # Q5: вопрос по архитектуре
    {
        "id": "q5_protocol_vs_abc",
        "category": "архитектура",
        "prompt": f"""\
В `app/ports.py` все интерфейсы определены через `typing.Protocol`:

```python
{_PORTS_LLMCLIENT}
```

В чём принципиальное отличие `Protocol` от `ABC` (AbstractBaseClass) \
в контексте jarvis-cli? Почему здесь предпочтён именно `Protocol`?""",
        "quality_checks": [
            ("упоминает structural subtyping / duck typing", lambda r: (
                "структурн" in r.lower() or "duck" in r.lower() or
                "subtyping" in r.lower() or "явно наследов" in r.lower() or
                "не нужно наследов" in r.lower()
            )),
            ("упоминает тесты / фейки", lambda r: (
                "тест" in r.lower() or "фейк" in r.lower() or "fake" in r.lower() or
                "mock" in r.lower() or "подмен" in r.lower()
            )),
        ],
    },
    # Q6: отладка — найти баг в invariant.check
    {
        "id": "q6_invariant_bug",
        "category": "отладка",
        "prompt": f"""\
Вот метод `Invariant.check()` из `domain/invariant.py`:

```python
{_INVARIANT_CHECK}
```

Допустим, у инварианта заданы и `forbidden_patterns`, и `required_patterns`. \
Если текст нарушает оба — что вернёт `check()`? Есть ли здесь проблема, \
и если да — как исправить?""",
        "quality_checks": [
            ("замечает что возвращает оба нарушения / список", lambda r: (
                "список" in r.lower() or "оба" in r.lower() or
                "несколько" in r.lower() or "все" in r.lower() or
                "violations" in r.lower()
            )),
            ("даёт корректный ответ что это не баг / или указывает реальную проблему", lambda r: len(r) > 80),
        ],
    },
]

CONFIGS = [
    {
        "name": "baseline",
        "label": "baseline (num_ctx=2048, temp=auto)",
        "params": {"temperature": None, "max_tokens": None, "num_ctx": None},
    },
    {
        "name": "code-opt",
        "label": "code-opt (num_ctx=8192, temp=0.3)",
        "params": {"temperature": 0.3, "max_tokens": 1024, "num_ctx": 8192},
    },
    {
        "name": "code-ctx32",
        "label": "code-ctx32 (num_ctx=32768, temp=0.3)",
        "params": {"temperature": 0.3, "max_tokens": 1024, "num_ctx": 32768},
    },
]


# ── HTTP ─────────────────────────────────────────────────────────────────────

def _chat(params: dict, question: str) -> tuple[str, float]:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": question},
    ]
    body: dict = {"model": MODEL, "messages": messages}
    if params.get("temperature") is not None:
        body["temperature"] = params["temperature"]
    if params.get("max_tokens") is not None:
        body["max_tokens"] = params["max_tokens"]
    if params.get("num_ctx") is not None:
        body["options"] = {"num_ctx": params["num_ctx"]}

    t0 = time.perf_counter()
    resp = requests.post(
        f"{OLLAMA_URL}/v1/chat/completions",
        headers={"Content-Type": "application/json"},
        json=body,
        timeout=180,
    )
    resp.raise_for_status()
    elapsed = time.perf_counter() - t0
    content = resp.json()["choices"][0]["message"].get("content", "")
    return content, elapsed


def _check_ollama() -> None:
    try:
        requests.get(f"{OLLAMA_URL}/api/tags", timeout=5).raise_for_status()
    except Exception as e:
        print(f"[ERROR] Ollama недоступна: {e}")
        sys.exit(1)


# ── вывод ────────────────────────────────────────────────────────────────────

def _score_bar(passed: int, total: int) -> str:
    filled = round(passed / total * 10) if total else 0
    return "█" * filled + "░" * (10 - filled)


def run_benchmark() -> None:
    _check_ollama()

    print(f"\n{'='*68}")
    print(f"  Бенчмарк jarvis-cli: {MODEL}")
    print(f"  {len(QUESTIONS)} реальных вопроса по коду · {len(CONFIGS)} конфигурации")
    print(f"{'='*68}")

    all_results: list[dict] = []

    for cfg in CONFIGS:
        print(f"\n\n{'─'*68}")
        print(f"  КОНФИГУРАЦИЯ: {cfg['label']}")
        print(f"{'─'*68}")

        cfg_data: dict = {"config": cfg["label"], "questions": []}

        for q in QUESTIONS:
            print(f"\n  [{q['id']}] {q['category'].upper()}")
            print(f"  Вопрос: {q['prompt'][:80].replace(chr(10),' ')}…")

            try:
                reply, elapsed = _chat(cfg["params"], q["prompt"])
            except Exception as e:
                print(f"  ОШИБКА: {e}")
                cfg_data["questions"].append({"id": q["id"], "error": str(e)})
                continue

            checks_results = []
            for label, fn in q["quality_checks"]:
                passed = fn(reply)
                checks_results.append({"label": label, "passed": passed})
                mark = "✓" if passed else "✗"
                print(f"    {mark} {label}")

            score  = sum(1 for c in checks_results if c["passed"])
            total  = len(checks_results)
            bar    = _score_bar(score, total)
            print(f"  Итог: [{bar}] {score}/{total}  |  время: {elapsed:.1f}s  |  {len(reply)} симв.")
            print(f"  Ответ (первые 200 символов):")
            print(f"    {reply[:200].replace(chr(10), ' / ')}")

            cfg_data["questions"].append({
                "id":          q["id"],
                "category":    q["category"],
                "elapsed_s":   round(elapsed, 2),
                "reply_len":   len(reply),
                "score":       score,
                "total":       total,
                "checks":      checks_results,
                "reply":       reply,
            })

        all_results.append(cfg_data)

    # ── сводная таблица ──────────────────────────────────────────────────────
    print(f"\n\n{'='*68}")
    print("  ИТОГОВАЯ ТАБЛИЦА")
    print(f"{'='*68}")
    print(f"{'Конфигурация':<38} {'Качество':>10} {'Ср.время':>9} {'Ср.длина':>9}")
    print(f"{'─'*68}")

    for r in all_results:
        qs = [q for q in r["questions"] if "error" not in q]
        if not qs:
            continue
        total_score = sum(q["score"] for q in qs)
        total_checks = sum(q["total"] for q in qs)
        avg_time    = sum(q["elapsed_s"] for q in qs) / len(qs)
        avg_len     = sum(q["reply_len"]  for q in qs) / len(qs)
        bar = _score_bar(total_score, total_checks)
        name = r["config"][:36]
        print(f"{name:<38} [{bar}] {total_score:>2}/{total_checks:<2}  {avg_time:>6.1f}s  {avg_len:>7.0f}")

    # ── анализ по категориям ─────────────────────────────────────────────────
    print(f"\n{'─'*68}")
    print("  КАЧЕСТВО ПО КАТЕГОРИЯМ (лучшая конфигурация)")
    print(f"{'─'*68}")
    categories = {q["category"] for q in QUESTIONS}
    for cat in sorted(categories):
        best_cfg = ""
        best_pct = -1
        for r in all_results:
            qs = [q for q in r["questions"] if q.get("category") == cat and "error" not in q]
            if not qs:
                continue
            pct = sum(q["score"] for q in qs) / sum(q["total"] for q in qs)
            if pct > best_pct:
                best_pct, best_cfg = pct, r["config"][:30]
        print(f"  {cat:<20} → {best_cfg}  ({best_pct*100:.0f}%)")

    print(f"\n{'='*68}\n")

    # ── сохранить ────────────────────────────────────────────────────────────
    out_path = Path(__file__).parent / "ollama_bench_jarvis_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"  Полные результаты: {out_path}\n")


if __name__ == "__main__":
    run_benchmark()
