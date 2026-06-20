"""CLI-обработчики /model, /temp, /tokens, /provider."""
from __future__ import annotations

from cli.ansi import BOLD, DIM, GREEN, RESET
from cli.config import PROVIDERS, models_for


def choose_model(params: dict, provider: str) -> None:
    models = models_for(provider)
    print(f"\n{BOLD}Выберите модель ({provider}):{RESET}")
    for k, (mid, label) in models.items():
        marker = " ◀" if mid == params["model"] else ""
        print(f"  {k}. {label}{marker}")
    choice = input("Номер (Enter — оставить текущую): ").strip()
    if choice in models:
        params["model"] = models[choice][0]
        print(f"{GREEN}Модель: {params['model']}{RESET}")


def choose_provider(current: str) -> str:
    """Показать список провайдеров и вернуть выбранный (или текущий)."""
    print(f"\n{BOLD}Выберите провайдера LLM:{RESET}")
    items = list(PROVIDERS)
    for i, name in enumerate(items, 1):
        marker = " ◀" if name == current else ""
        print(f"  {i}. {name}{marker}")
    choice = input("Номер (Enter — оставить текущего): ").strip()
    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= len(items):
            return items[idx - 1]
    print(f"{DIM}Провайдер не изменён.{RESET}")
    return current


def set_temperature(params: dict) -> None:
    val = input("temperature (0.0–2.0, Enter — auto): ").strip()
    if val == "":
        params["temperature"] = None
    else:
        params["temperature"] = float(val)


def set_max_tokens(params: dict) -> None:
    val = input("max_tokens (целое число, Enter — auto): ").strip()
    if val == "":
        params["max_tokens"] = None
    else:
        params["max_tokens"] = int(val)
