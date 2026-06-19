import os

import pytest

from cli.config import (
    ACTIVE_TASK_FILE,
    CHAT_URL,
    DEFAULT_PARAMS,
    HISTORY_DIR,
    KNOWLEDGE_DIR,
    MAX_SESSIONS,
    MODELS,
    OAUTH_URL,
    PROFILES_DIR,
    SCOPE,
    TASKS_DIR,
    WORKING_DIR,
    load_env,
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


def test_models_keys_are_string_digits():
    assert set(MODELS) == {"1", "2", "3", "4", "5", "6"}
    for k, (mid, label) in MODELS.items():
        assert isinstance(mid, str) and mid.startswith("GigaChat")
        assert label  # непустая


def test_default_params_shape():
    assert DEFAULT_PARAMS["model"] == "GigaChat"
    assert DEFAULT_PARAMS["temperature"] is None
    assert DEFAULT_PARAMS["max_tokens"] is None


def test_paths_under_user_jarvis_dir():
    for p in (HISTORY_DIR, PROFILES_DIR, WORKING_DIR, KNOWLEDGE_DIR, TASKS_DIR):
        assert "/.jarvis/" in p
    assert ACTIVE_TASK_FILE == os.path.join(TASKS_DIR, "active")


def test_endpoint_constants_are_strings():
    assert OAUTH_URL.startswith("https://")
    assert CHAT_URL.startswith("https://")
    assert isinstance(SCOPE, str) and SCOPE
    assert isinstance(MAX_SESSIONS, int) and MAX_SESSIONS > 0
