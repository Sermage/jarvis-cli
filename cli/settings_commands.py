"""CLI-обработчики /model, /temp, /tokens."""
from __future__ import annotations

from cli.ansi import BOLD, GREEN, RESET
from cli.config import MODELS


def choose_model(params: dict) -> None:
    print(f"\n{BOLD}Выберите модель:{RESET}")
    for k, (mid, label) in MODELS.items():
        marker = " ◀" if mid == params["model"] else ""
        print(f"  {k}. {label}{marker}")
    choice = input("Номер (Enter — оставить текущую): ").strip()
    if choice in MODELS:
        params["model"] = MODELS[choice][0]
        print(f"{GREEN}Модель: {params['model']}{RESET}")


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
