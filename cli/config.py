"""Загрузка .env и константы окружения для CLI.

Используется только composition root (cli/main.py) — больше нигде не
импортировать, чтобы не плодить скрытые зависимости от os.environ.
"""
from __future__ import annotations

import os


def load_env(env_path: str) -> None:
    """Подтянуть переменные из .env в os.environ (без перезаписи существующих)."""
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


# ── LLM-провайдеры ──────────────────────────────────────────────────────────

DEEPSEEK   = "deepseek"
GIGACHAT   = "gigachat"
PROVIDERS  = (DEEPSEEK, GIGACHAT)
DEFAULT_PROVIDER = DEEPSEEK

# ── DeepSeek ────────────────────────────────────────────────────────────────

DEEPSEEK_CHAT_URL = "https://api.deepseek.com/chat/completions"

DEEPSEEK_MODELS = {
    "1": ("deepseek-chat",     "DeepSeek-V3 (chat)"),
    "2": ("deepseek-reasoner", "DeepSeek-R1 (reasoner)"),
}

# ── GigaChat ────────────────────────────────────────────────────────────────

GIGACHAT_OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
GIGACHAT_CHAT_URL  = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
GIGACHAT_SCOPE     = "GIGACHAT_API_PERS"

GIGACHAT_MODELS = {
    "1": ("GigaChat",       "GigaChat (слабая)"),
    "2": ("GigaChat-Pro",   "GigaChat-Pro (средняя)"),
    "3": ("GigaChat-Max",   "GigaChat-Max (сильная)"),
    "4": ("GigaChat-2",     "GigaChat-2 (слабая, v2)"),
    "5": ("GigaChat-2-Pro", "GigaChat-2-Pro (средняя, v2)"),
    "6": ("GigaChat-2-Max", "GigaChat-2-Max (сильная, v2)"),
}

# ── провайдер → модели / дефолт ─────────────────────────────────────────────

MODELS_BY_PROVIDER = {
    DEEPSEEK: DEEPSEEK_MODELS,
    GIGACHAT: GIGACHAT_MODELS,
}

DEFAULT_MODEL_BY_PROVIDER = {
    DEEPSEEK: "deepseek-chat",
    GIGACHAT: "GigaChat",
}


def models_for(provider: str) -> dict:
    return MODELS_BY_PROVIDER[provider]


def default_model_for(provider: str) -> str:
    return DEFAULT_MODEL_BY_PROVIDER[provider]


def resolve_provider(env_value: str) -> str:
    """Нормализовать значение LLM_PROVIDER, упасть на default при пустом/неизвестном."""
    v = (env_value or "").strip().lower()
    return v if v in PROVIDERS else DEFAULT_PROVIDER


DEFAULT_PARAMS = {
    "model":       DEFAULT_MODEL_BY_PROVIDER[DEFAULT_PROVIDER],
    "temperature": None,
    "max_tokens":  None,
}

# ── пути под ~/.jarvis/ ─────────────────────────────────────────────────────

HISTORY_DIR      = os.path.expanduser("~/.jarvis/sessions")
PROFILES_DIR     = os.path.expanduser("~/.jarvis/profiles")
WORKING_DIR      = os.path.expanduser("~/.jarvis/working")
KNOWLEDGE_DIR    = os.path.expanduser("~/.jarvis/knowledge")
TASKS_DIR        = os.path.expanduser("~/.jarvis/tasks")
INVARIANTS_DIR   = os.path.expanduser("~/.jarvis/invariants")
MCP_DIR          = os.path.expanduser("~/.jarvis/mcp")
MCP_CONFIG_FILE  = os.path.join(MCP_DIR, "servers.json")
ACTIVE_TASK_FILE = os.path.join(TASKS_DIR, "active")
MAX_SESSIONS     = 20
