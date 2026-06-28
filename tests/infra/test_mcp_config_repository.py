"""Тесты файлового хранилища конфига MCP-серверов."""
from __future__ import annotations

from domain.mcp import McpServerConfig
from infra.mcp_config_repository import FileMcpConfigRepository


def _repo(tmp_path):
    return FileMcpConfigRepository(file_path=str(tmp_path / "servers.json"))


def test_list_all_returns_empty_when_no_file(tmp_path):
    assert _repo(tmp_path).list_all() == []


def test_save_then_list_roundtrip(tmp_path):
    repo = _repo(tmp_path)
    repo.save(McpServerConfig(server_id="fs", command="echo"))
    repo.save(McpServerConfig(server_id="db", command="ls", args=("-la",)))
    items = repo.list_all()
    ids = [c.server_id for c in items]
    assert sorted(ids) == ["db", "fs"]


def test_save_replaces_existing_by_server_id(tmp_path):
    repo = _repo(tmp_path)
    repo.save(McpServerConfig(server_id="fs", command="old"))
    repo.save(McpServerConfig(server_id="fs", command="new"))
    items = repo.list_all()
    assert len(items) == 1
    assert items[0].command == "new"


def test_delete_removes_by_id(tmp_path):
    repo = _repo(tmp_path)
    repo.save(McpServerConfig(server_id="a", command="x"))
    repo.save(McpServerConfig(server_id="b", command="y"))
    repo.delete("a")
    assert [c.server_id for c in repo.list_all()] == ["b"]


def test_set_enabled_toggles_flag(tmp_path):
    repo = _repo(tmp_path)
    repo.save(McpServerConfig(server_id="fs", command="echo", enabled=True))
    repo.set_enabled("fs", False)
    cfg = repo.get("fs")
    assert cfg is not None and cfg.enabled is False


def test_get_returns_none_for_missing(tmp_path):
    assert _repo(tmp_path).get("nope") is None


def test_corrupt_file_treated_as_empty(tmp_path):
    path = tmp_path / "servers.json"
    path.write_text("not json", encoding="utf-8")
    assert FileMcpConfigRepository(file_path=str(path)).list_all() == []
