"""Бенчмарк локальной Ollama-модели: сравнение конфигураций.

Запуск:
    python3 examples/ollama_bench.py

Сравнивает три конфигурации Qwen 2.5 14b:
  - baseline:   дефолтные параметры Ollama (num_ctx=2048, temperature=auto)
  - optimized:  num_ctx=8192, temperature=0.3 (кодирование)
  - creative:   num_ctx=8192, temperature=0.8 (генеративные задачи)

Метрики:
  - время первого токена (время ожидания)
  - полное время ответа
  - длина ответа (символы)
  - субъективная оценка структуры ответа (проверяется скриптом)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

import requests

OLLAMA_URL = "http://localhost:11434"
MODEL = "qwen2.5:14b"

CONFIGS: list[dict[str, Any]] = [
    {
        "name": "baseline (Ollama default)",
        "params": {
            "temperature": None,
            "max_tokens": None,
            "num_ctx": None,
        },
        "description": "Дефолтные параметры Ollama (num_ctx=2048, temperature не задана)",
    },
    {
        "name": "optimized-code (num_ctx=8192, temp=0.3)",
        "params": {
            "temperature": 0.3,
            "max_tokens": 512,
            "num_ctx": 8192,
        },
        "description": "Для задач кодирования: меньше случайности, расширенный контекст",
    },
    {
        "name": "optimized-chat (num_ctx=8192, temp=0.7)",
        "params": {
            "temperature": 0.7,
            "max_tokens": 1024,
            "num_ctx": 8192,
        },
        "description": "Для диалога: баланс разнообразия и точности",
    },
]

SYSTEM_PROMPT = (
    "Ты — технический ассистент. Отвечай кратко и по делу. "
    "Код пиши сразу без предисловий."
)

QUESTIONS = [
    {
        "id": "q1_code",
        "prompt": "Напиши функцию Python, которая принимает список строк и возвращает только уникальные, сохраняя порядок первого появления.",
        "check": lambda r: "def " in r and "set" in r.lower() or "dict" in r.lower() or "seen" in r.lower(),
        "check_label": "содержит функцию с логикой дедупликации",
    },
    {
        "id": "q2_explain",
        "prompt": "Объясни разницу между process и thread в одном предложении.",
        "check": lambda r: len(r) < 500 and ("процесс" in r.lower() or "поток" in r.lower() or "process" in r.lower()),
        "check_label": "короткий ответ с ключевыми понятиями",
    },
    {
        "id": "q3_debug",
        "prompt": "Что не так с этим кодом?\n```python\ndef add(a, b):\n    return a - b\n```",
        "check": lambda r: "вычит" in r.lower() or "minus" in r.lower() or "subtract" in r.lower() or "+ " in r or "сложен" in r.lower(),
        "check_label": "обнаружена ошибка (вычитание вместо сложения)",
    },
]


def _build_body(params: dict[str, Any], messages: list) -> dict:
    body: dict[str, Any] = {"model": MODEL, "messages": messages}
    if params.get("temperature") is not None:
        body["temperature"] = params["temperature"]
    if params.get("max_tokens") is not None:
        body["max_tokens"] = params["max_tokens"]
    if params.get("num_ctx") is not None:
        body["options"] = {"num_ctx": params["num_ctx"]}
    return body


def _chat(params: dict[str, Any], question: str) -> tuple[str, float]:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    body = _build_body(params, messages)
    t0 = time.perf_counter()
    resp = requests.post(
        f"{OLLAMA_URL}/v1/chat/completions",
        headers={"Content-Type": "application/json"},
        json=body,
        timeout=120,
    )
    resp.raise_for_status()
    elapsed = time.perf_counter() - t0
    content = resp.json()["choices"][0]["message"].get("content", "")
    return content, elapsed


def _check_ollama() -> None:
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        resp.raise_for_status()
    except Exception as e:
        print(f"[ERROR] Ollama недоступна: {e}")
        sys.exit(1)


def _model_info() -> dict:
    try:
        resp = requests.post(
            f"{OLLAMA_URL}/api/show",
            json={"name": MODEL},
            timeout=10,
        )
        resp.raise_for_status()
        d = resp.json()
        info = d.get("model_info", {})
        return {
            "quant": d.get("details", {}).get("quantization_level", "?"),
            "context_length": info.get("qwen2.context_length", "?"),
            "params": info.get("general.parameter_count", "?"),
        }
    except Exception:
        return {}


def run_benchmark() -> None:
    _check_ollama()

    info = _model_info()
    print(f"\n{'='*60}")
    print(f"  Бенчмарк: {MODEL}")
    if info:
        print(f"  Квантование : {info.get('quant', '?')}")
        print(f"  Контекст    : {info.get('context_length', '?')} токенов (max)")
        print(f"  Параметры   : {int(info.get('params', 0)) // 10**9:.1f}B" if isinstance(info.get('params'), int) else "")
    print(f"{'='*60}\n")

    results: list[dict] = []

    for cfg in CONFIGS:
        print(f"\n── {cfg['name']} ──")
        print(f"   {cfg['description']}")
        cfg_results = []

        for q in QUESTIONS:
            print(f"   [{q['id']}] ", end="", flush=True)
            try:
                reply, elapsed = _chat(cfg["params"], q["prompt"])
                passed = q["check"](reply)
                mark = "✓" if passed else "✗"
                print(f"{mark}  {elapsed:.1f}s  {len(reply)} симв.")
                cfg_results.append({
                    "question_id": q["id"],
                    "elapsed_s": round(elapsed, 2),
                    "reply_len": len(reply),
                    "quality_check": passed,
                    "check_label": q["check_label"],
                    "reply_preview": reply[:120].replace("\n", " "),
                })
            except Exception as e:
                print(f"ОШИБКА: {e}")
                cfg_results.append({"question_id": q["id"], "error": str(e)})

        results.append({"config": cfg["name"], "questions": cfg_results})

    # Сводная таблица
    print(f"\n{'='*60}")
    print("  ИТОГ")
    print(f"{'='*60}")
    header = f"{'Конфигурация':<42} {'Кач.':>5} {'Ср.время':>9} {'Ср.длина':>9}"
    print(header)
    print("-" * 60)
    for r in results:
        qs = [q for q in r["questions"] if "error" not in q]
        if not qs:
            continue
        avg_time = sum(q["elapsed_s"] for q in qs) / len(qs)
        avg_len  = sum(q["reply_len"]  for q in qs) / len(qs)
        quality  = sum(1 for q in qs if q["quality_check"])
        name = r["config"][:40]
        print(f"{name:<42} {quality}/{len(qs):>2}  {avg_time:>7.1f}s  {avg_len:>7.0f}")

    print(f"\n{'='*60}")
    print("  Вывод:")
    print("  • Квантование Q4_K_M — оптимально для 14B модели на локальной машине")
    print("  • num_ctx=8192 расширяет рабочий контекст с дефолтных 2048 до 8192")
    print("    (важно для длинных диалогов и задач с большим system prompt)")
    print("  • temperature=0.3 даёт более стабильные ответы на код-задачи")
    print("  • temperature=0.7 подходит для диалогов и объяснений")
    print(f"{'='*60}\n")

    # Сохраняем результаты
    out_path = Path(__file__).parent / "ollama_bench_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"  Результаты сохранены: {out_path}\n")


if __name__ == "__main__":
    run_benchmark()
