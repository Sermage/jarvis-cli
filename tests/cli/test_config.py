import os

import pytest

from cli.config import (
    ACTIVE_TASK_FILE,
    DEEPSEEK,
    DEEPSEEK_CHAT_URL,
    DEEPSEEK_MODELS,
    DEFAULT_MODEL_BY_PROVIDER,
    DEFAULT_PARAMS,
    DEFAULT_PROVIDER,
    GIGACHAT,
    GIGACHAT_CHAT_URL,
    GIGACHAT_MODELS,
    GIGACHAT_OAUTH_URL,
    GIGACHAT_SCOPE,
    HISTORY_DIR,
    KNOWLEDGE_DIR,
    MAX_SESSIONS,
    MODELS_BY_PROVIDER,
    PROFILES_DIR,
    PROVIDERS,
    TASKS_DIR,
    WORKING_DIR,
    default_model_for,
    load_env,
    models_for,
    resolve_provider,
)


@pytest.fixture
def isolated_env(monkeypatch):
    """Откатываем os.environ между тестами."""
    snapshot = dict(os.environ)
    yield monkeypatch
    os.environ.clear()
    os.environ.update(snapshot)


def test_load_env_populates_missing_keys(tmp_path, isolated_env):
    f = tmp_path / ".env"
    f.write_text("FOO=hello\nBAR=world\n", encoding="utf-8")
    isolated_env.delenv("FOO", raising=False)
    isolated_env.delenv("BAR", raising=False)

    load_env(str(f))

    assert os.environ["FOO"] == "hello"
    assert os.environ["BAR"] == "world"


def test_load_env_does_not_override_existing(tmp_path, isolated_env):
    f = tmp_path / ".env"
    f.write_text("FOO=from-file\n", encoding="utf-8")
    isolated_env.setenv("FOO", "from-shell")

    load_env(str(f))

    assert os.environ["FOO"] == "from-shell"


def test_load_env_ignores_comments_and_blank_lines(tmp_path, isolated_env):
    f = tmp_path / ".env"
    f.write_text("# комментарий\n\nKEY=value\n# другой\n", encoding="utf-8")
    isolated_env.delenv("KEY", raising=False)
    load_env(str(f))
    assert os.environ["KEY"] == "value"


def test_load_env_silently_skips_missing_file(tmp_path):
    # Не должно бросать.
    load_env(str(tmp_path / "no-such-file"))


def test_load_env_strips_whitespace_around_kv(tmp_path, isolated_env):
    f = tmp_path / ".env"
    f.write_text("  KEY  =  spaced  \n", encoding="utf-8")
    isolated_env.delenv("KEY", raising=False)
    load_env(str(f))
    assert os.environ["KEY"] == "spaced"


def test_providers_listing_includes_all_providers():
    from cli.config import OLLAMA
    assert set(PROVIDERS) == {DEEPSEEK, GIGACHAT, OLLAMA}
    assert DEFAULT_PROVIDER == DEEPSEEK


def test_gigachat_models_keys_are_string_digits():
    assert set(GIGACHAT_MODELS) == {"1", "2", "3", "4", "5", "6"}
    for k, (mid, label) in GIGACHAT_MODELS.items():
        assert isinstance(mid, str) and mid.startswith("GigaChat")
        assert label  # непустая


def test_deepseek_models_have_chat_and_reasoner():
    ids = {mid for (mid, _label) in DEEPSEEK_MODELS.values()}
    assert "deepseek-chat" in ids
    assert "deepseek-reasoner" in ids


def test_models_for_returns_provider_specific_list():
    assert models_for(DEEPSEEK) is MODELS_BY_PROVIDER[DEEPSEEK]
    assert models_for(GIGACHAT) is MODELS_BY_PROVIDER[GIGACHAT]


def test_default_model_for_matches_table():
    assert default_model_for(DEEPSEEK) == DEFAULT_MODEL_BY_PROVIDER[DEEPSEEK]
    assert default_model_for(GIGACHAT) == DEFAULT_MODEL_BY_PROVIDER[GIGACHAT]


def test_resolve_provider_normalizes_known_and_falls_back():
    assert resolve_provider("deepseek")  == DEEPSEEK
    assert resolve_provider("  GigaChat ") == GIGACHAT
    assert resolve_provider("")           == DEFAULT_PROVIDER
    assert resolve_provider("openai")     == DEFAULT_PROVIDER


def test_default_params_default_to_deepseek():
    assert DEFAULT_PARAMS["model"] == "deepseek-chat"
    assert DEFAULT_PARAMS["temperature"] is None
    assert DEFAULT_PARAMS["max_tokens"] is None


def test_paths_under_user_jarvis_dir():
    for p in (HISTORY_DIR, PROFILES_DIR, WORKING_DIR, KNOWLEDGE_DIR, TASKS_DIR):
        assert "/.jarvis/" in p
    assert ACTIVE_TASK_FILE == os.path.join(TASKS_DIR, "active")


def test_endpoint_constants_are_strings():
    assert DEEPSEEK_CHAT_URL.startswith("https://")
    assert GIGACHAT_OAUTH_URL.startswith("https://")
    assert GIGACHAT_CHAT_URL.startswith("https://")
    assert isinstance(GIGACHAT_SCOPE, str) and GIGACHAT_SCOPE
    assert isinstance(MAX_SESSIONS, int) and MAX_SESSIONS > 0
