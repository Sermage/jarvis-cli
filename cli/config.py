"""Загрузка .env и константы окружения для CLI.

Используется только composition root (cli/main.py) — больше нигде не
импортировать, чтобы не плодить скрытые зависимости от os.environ.
"""
from __future__ import annotations

import os

from domain.retrieval import RERANKERS, RetrievalConfig


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
OLLAMA     = "ollama"
PROVIDERS  = (DEEPSEEK, GIGACHAT, OLLAMA)
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

# ── Ollama ──────────────────────────────────────────────────────────────────

OLLAMA_BASE_URL = "http://localhost:11434"

OLLAMA_MODELS = {
    "1": ("qwen2.5:14b",  "Qwen 2.5 14B (локально)"),
    "2": ("qwen2.5:7b",   "Qwen 2.5 7B (локально)"),
    "3": ("llama3.1:8b",  "Llama 3.1 8B (локально)"),
    "4": ("gemma3:12b",   "Gemma 3 12B (локально)"),
    "5": ("phi4:14b",     "Phi-4 14B (локально)"),
}

# ── провайдер → модели / дефолт ─────────────────────────────────────────────

MODELS_BY_PROVIDER = {
    DEEPSEEK: DEEPSEEK_MODELS,
    GIGACHAT: GIGACHAT_MODELS,
    OLLAMA:   OLLAMA_MODELS,
}

DEFAULT_MODEL_BY_PROVIDER = {
    DEEPSEEK: "deepseek-chat",
    GIGACHAT: "GigaChat",
    OLLAMA:   "qwen2.5:14b",
}


def models_for(provider: str) -> dict:
    return MODELS_BY_PROVIDER[provider]


def default_model_for(provider: str) -> str:
    return DEFAULT_MODEL_BY_PROVIDER[provider]


def resolve_provider(env_value: str) -> str:
    """Нормализовать значение LLM_PROVIDER, упасть на default при пустом/неизвестном."""
    v = (env_value or "").strip().lower()
    # «local» — удобный псевдоним для ollama
    if v == "local":
        return OLLAMA
    return v if v in PROVIDERS else DEFAULT_PROVIDER


DEFAULT_PARAMS = {
    "model":       DEFAULT_MODEL_BY_PROVIDER[DEFAULT_PROVIDER],
    "temperature": None,
    "max_tokens":  None,
    "num_ctx":     None,
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

# ── RAG ─────────────────────────────────────────────────────────────────────

DEFAULT_RAG_INDEX_PATH = os.path.expanduser("~/rag-kotlin/index")
DEFAULT_RAG_STRATEGY   = "structural"
DEFAULT_RAG_TOP_K      = 5
DEFAULT_RAG_FETCH_K    = 20
DEFAULT_RAG_MIN_SCORE  = 0.0
DEFAULT_RAG_RERANKER   = "heuristic"
DEFAULT_RAG_REWRITE    = False
DEFAULT_EMBED_MODEL    = "bge-m3"
DEFAULT_OLLAMA_URL     = "http://localhost:11434"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _env_bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "да")


def load_rag_config() -> RetrievalConfig:
    """Собрать конфиг RAG из окружения (.env уже должен быть подгружен)."""
    index_path = os.path.expanduser(
        os.environ.get("RAG_INDEX_PATH", "").strip() or DEFAULT_RAG_INDEX_PATH
    )
    strategy = os.environ.get("RAG_STRATEGY", "").strip() or DEFAULT_RAG_STRATEGY
    reranker = (os.environ.get("RAG_RERANKER", "").strip().lower() or DEFAULT_RAG_RERANKER)
    if reranker not in RERANKERS:
        reranker = DEFAULT_RAG_RERANKER
    return RetrievalConfig(
        enabled=_env_bool("RAG_ENABLED"),
        index_path=index_path,
        strategy=strategy,
        top_k=_env_int("RAG_TOP_K", DEFAULT_RAG_TOP_K),
        fetch_k=_env_int("RAG_FETCH_K", DEFAULT_RAG_FETCH_K),
        min_score=_env_float("RAG_MIN_SCORE", DEFAULT_RAG_MIN_SCORE),
        reranker=reranker,
        rewrite=_env_bool("RAG_REWRITE") if os.environ.get("RAG_REWRITE") else DEFAULT_RAG_REWRITE,
    )
