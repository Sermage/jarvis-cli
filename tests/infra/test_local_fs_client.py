"""Тесты LocalFilesystemClient — встроенного источника файловых тулов.

Проверяем на временном каталоге (tmp_path):
- чтение/листинг/поиск по нескольким файлам;
- sandbox: выход за корень запрещён;
- запись через confirm: подтверждено пишет, отклонено — нет;
- diff в результате и в аргументах confirm.
"""
from __future__ import annotations

import pytest

from infra.local_fs_client import LocalFilesystemClient, PathEscapeError


@pytest.fixture
def project(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "service.py").write_text(
        "def call_api():\n    return api_client.get('/x')\n", encoding="utf-8")
    (tmp_path / "app" / "other.py").write_text(
        "x = 1\napi_client.post('/y')\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Project\napi docs here\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("api_client secret\n", encoding="utf-8")
    return tmp_path


def _client(root, confirm=None):
    c = LocalFilesystemClient(root=str(root), confirm=confirm)
    c.start()
    return c


# ── чтение / листинг ──────────────────────────────────────────────────────────

def test_list_dir_skips_vcs_dirs(project):
    res = _client(project).call_tool("list_dir", {"path": "."})
    assert not res.is_error
    assert "app/" in res.text
    assert "README.md" in res.text
    assert ".git" not in res.text  # служебный каталог отфильтрован


def test_read_file_returns_content(project):
    res = _client(project).call_tool("read_file", {"path": "app/service.py"})
    assert not res.is_error
    assert "def call_api" in res.text


def test_read_missing_file_is_error(project):
    res = _client(project).call_tool("read_file", {"path": "nope.py"})
    assert res.is_error


# ── поиск по нескольким файлам ────────────────────────────────────────────────

def test_search_finds_across_files(project):
    res = _client(project).call_tool("search", {"query": "api_client"})
    assert not res.is_error
    assert "app/service.py:" in res.text
    assert "app/other.py:" in res.text
    # .git отфильтрован при обходе — секрет из .git/config не всплывает
    assert ".git/config" not in res.text


def test_search_glob_filters_by_extension(project):
    res = _client(project).call_tool("search", {"query": "api", "glob": "*.md"})
    assert "README.md:" in res.text
    assert "service.py" not in res.text


def test_search_regex(project):
    res = _client(project).call_tool(
        "search", {"query": r"api_client\.(get|post)", "regex": True})
    assert "service.py:" in res.text
    assert "other.py:" in res.text


def test_search_no_match(project):
    res = _client(project).call_tool("search", {"query": "zzz_not_here"})
    assert not res.is_error
    assert "не найдено" in res.text.lower()


# ── sandbox ───────────────────────────────────────────────────────────────────

def test_escape_root_is_error(project):
    res = _client(project).call_tool("read_file", {"path": "../../../etc/passwd"})
    assert res.is_error
    assert "вне корня" in res.text or "нет такого" in res.text.lower()


def test_write_escape_root_is_error(project):
    res = _client(project).call_tool(
        "write_file", {"path": "../evil.txt", "content": "x"})
    assert res.is_error


# ── запись через confirm ──────────────────────────────────────────────────────

def test_write_creates_file_when_confirmed(project):
    seen = {}
    def confirm(rel, diff):
        seen["rel"], seen["diff"] = rel, diff
        return True
    res = _client(project, confirm).call_tool(
        "write_file", {"path": "docs/ADR-001.md", "content": "# ADR 1\nbody\n"})
    assert not res.is_error
    assert (project / "docs" / "ADR-001.md").read_text() == "# ADR 1\nbody\n"
    assert seen["rel"] == "docs/ADR-001.md"
    assert "+# ADR 1" in seen["diff"]  # diff показан до записи


def test_write_declined_does_not_touch_file(project):
    before = (project / "README.md").read_text()
    res = _client(project, confirm=lambda rel, diff: False).call_tool(
        "write_file", {"path": "README.md", "content": "# hacked\n"})
    assert not res.is_error  # отказ — не ошибка, а информация модели
    assert "отклон" in res.text.lower()
    assert (project / "README.md").read_text() == before  # файл не тронут


def test_write_modifies_existing_and_reports_diff(project):
    res = _client(project, confirm=lambda r, d: True).call_tool(
        "write_file", {"path": "README.md", "content": "# Project\nNEW LINE\n"})
    assert not res.is_error
    assert "обновлён" in res.text
    assert "+NEW LINE" in res.text
    assert "-api docs here" in res.text


def test_write_no_change_is_noop(project):
    same = (project / "README.md").read_text()
    called = {"n": 0}
    def confirm(rel, diff):
        called["n"] += 1
        return True
    res = _client(project, confirm).call_tool(
        "write_file", {"path": "README.md", "content": same})
    assert "Изменений нет" in res.text
    assert called["n"] == 0  # confirm не дёргается, если diff пуст


# ── протокол ──────────────────────────────────────────────────────────────────

def test_lists_expected_tools(project):
    names = {t.name for t in _client(project).list_tools()}
    assert names == {"list_dir", "read_file", "search", "write_file"}


def test_start_rejects_non_dir(tmp_path):
    f = tmp_path / "afile"
    f.write_text("x")
    with pytest.raises(RuntimeError):
        LocalFilesystemClient(root=str(f)).start()
