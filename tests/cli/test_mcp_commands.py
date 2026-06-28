"""Тесты CLI-обработчика /mcp — парсинг подкоманд и флагов."""
from __future__ import annotations

from dataclasses import dataclass, field

from cli.mcp_commands import handle_mcp
from domain.mcp import McpServerConfig


@dataclass
class _FakeRepo:
    items: list = field(default_factory=list)

    def list_all(self):
        return list(self.items)

    def get(self, server_id):
        for c in self.items:
            if c.server_id == server_id:
                return c
        return None

    def save(self, cfg):
        self.items = [c for c in self.items if c.server_id != cfg.server_id] + [cfg]

    def delete(self, server_id):
        self.items = [c for c in self.items if c.server_id != server_id]

    def set_enabled(self, server_id, enabled):
        new = []
        for c in self.items:
            new.append(c if c.server_id != server_id else McpServerConfig(
                server_id=c.server_id, command=c.command, args=c.args,
                env=c.env, cwd=c.cwd, enabled=enabled, transport=c.transport,
                url=c.url, headers=c.headers,
            ))
        self.items = new


# ── add (stdio) ───────────────────────────────────────────────────────────────


def test_add_stdio_saves_command_and_args(capsys):
    repo = _FakeRepo()
    handle_mcp("/mcp add fs python3 server.py --root /tmp", repo, registry=None)
    cfg = repo.get("fs")
    assert cfg is not None
    assert cfg.transport == "stdio"
    assert cfg.command == "python3"
    assert cfg.args == ("server.py", "--root", "/tmp")


def test_add_refuses_duplicate_id(capsys):
    repo = _FakeRepo(items=[McpServerConfig(server_id="fs", command="x")])
    handle_mcp("/mcp add fs python3 other.py", repo, registry=None)
    out = capsys.readouterr().out
    assert "уже существует" in out


# ── add --http ────────────────────────────────────────────────────────────────


def test_add_http_with_single_header():
    repo = _FakeRepo()
    handle_mcp(
        '/mcp add tinvest --http http://host/mcp --header "Authorization: Bearer abc"',
        repo, registry=None,
    )
    cfg = repo.get("tinvest")
    assert cfg is not None
    assert cfg.transport == "http"
    assert cfg.url == "http://host/mcp"
    assert cfg.headers == {"Authorization": "Bearer abc"}
    # Stdio-поля не выставлены.
    assert cfg.command == ""
    assert cfg.args == ()


def test_add_http_with_multiple_headers():
    repo = _FakeRepo()
    handle_mcp(
        '/mcp add api --http https://x/y '
        '--header "Authorization: Bearer t" '
        '--header "X-Trace: 1"',
        repo, registry=None,
    )
    cfg = repo.get("api")
    assert cfg.headers == {"Authorization": "Bearer t", "X-Trace": "1"}


def test_add_http_requires_url(capsys):
    repo = _FakeRepo()
    handle_mcp("/mcp add t --http", repo, registry=None)
    assert repo.get("t") is None
    out = capsys.readouterr().out
    assert "--http" in out


def test_add_http_rejects_malformed_header(capsys):
    repo = _FakeRepo()
    handle_mcp('/mcp add t --http http://x --header "noColon"', repo, registry=None)
    assert repo.get("t") is None
    out = capsys.readouterr().out
    assert "Key: Value" in out


# ── delete / enable / disable ─────────────────────────────────────────────────


def test_rm_removes_existing_server(capsys):
    repo = _FakeRepo(items=[McpServerConfig(server_id="fs", command="x")])
    handle_mcp("/mcp rm fs", repo, registry=None)
    assert repo.get("fs") is None


def test_rm_unknown_does_not_crash(capsys):
    repo = _FakeRepo()
    handle_mcp("/mcp rm ghost", repo, registry=None)
    out = capsys.readouterr().out
    assert "не найден" in out


def test_disable_toggles_enabled_flag():
    repo = _FakeRepo(items=[McpServerConfig(server_id="fs", command="x", enabled=True)])
    handle_mcp("/mcp disable fs", repo, registry=None)
    assert repo.get("fs").enabled is False


def test_enable_toggles_back():
    repo = _FakeRepo(items=[McpServerConfig(server_id="fs", command="x", enabled=False)])
    handle_mcp("/mcp enable fs", repo, registry=None)
    assert repo.get("fs").enabled is True


# ── list ──────────────────────────────────────────────────────────────────────


def test_list_shows_transport_marker(capsys):
    repo = _FakeRepo(items=[
        McpServerConfig(server_id="fs", command="python3", args=("a",)),
        McpServerConfig(server_id="t", transport="http", url="http://x"),
    ])
    handle_mcp("/mcp list", repo, registry=None)
    out = capsys.readouterr().out
    assert "[stdio]" in out
    assert "[http]"  in out
    assert "fs" in out and "t" in out
