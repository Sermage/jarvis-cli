"""Тесты McpGitContextProvider: git-ветка через фейковый MCP-реестр."""
from __future__ import annotations

from dataclasses import dataclass, field

from domain.mcp import McpTool, ToolResult
from infra.mcp_git import McpGitContextProvider, parse_branch


def test_parse_branch_on_branch_form():
    assert parse_branch("On branch feature/help\nnothing to commit") == "feature/help"


def test_parse_branch_star_form():
    assert parse_branch("  main\n* develop\n  release/1.0") == "develop"


def test_parse_branch_none():
    assert parse_branch("no branch info here") is None
    assert parse_branch("") is None


@dataclass
class _FakeClient:
    server_id: str
    tools: list
    result: ToolResult
    calls: list = field(default_factory=list)

    def list_tools(self):
        return list(self.tools)

    def call_tool(self, name, args):
        self.calls.append((name, args))
        return self.result


@dataclass
class _FakeRegistry:
    _clients: dict  # server_id -> _FakeClient

    def clients(self):
        return list(self._clients.values())

    def get(self, server_id):
        return self._clients.get(server_id)

    def all_tools(self):
        out = []
        for c in self._clients.values():
            out.extend(c.tools)
        return out

    def shutdown(self):
        pass


def _tool(server_id, name):
    return McpTool(server_id=server_id, name=name, description="", input_schema={})


def test_prefers_git_status_and_passes_repo_path():
    client = _FakeClient(
        server_id="git",
        tools=[_tool("git", "git_status"), _tool("git", "git_log")],
        result=ToolResult(text="On branch main\nnothing to commit"),
    )
    reg = _FakeRegistry({"git": client})
    provider = McpGitContextProvider(reg, "/repo")

    assert provider.current_branch() == "main"
    assert client.calls == [("git_status", {"repo_path": "/repo"})]


def test_falls_back_to_git_branch_tool():
    client = _FakeClient(
        server_id="git",
        tools=[_tool("git", "git_branch")],
        result=ToolResult(text="  main\n* develop"),
    )
    reg = _FakeRegistry({"git": client})
    assert McpGitContextProvider(reg, "/repo").current_branch() == "develop"


def test_no_git_tool_returns_none():
    client = _FakeClient(server_id="fs", tools=[_tool("fs", "read_file")],
                         result=ToolResult(text="x"))
    reg = _FakeRegistry({"fs": client})
    assert McpGitContextProvider(reg, "/repo").current_branch() is None


def test_tool_error_returns_none():
    client = _FakeClient(
        server_id="git", tools=[_tool("git", "git_status")],
        result=ToolResult(text="fatal: not a repo", is_error=True),
    )
    reg = _FakeRegistry({"git": client})
    assert McpGitContextProvider(reg, "/repo").current_branch() is None


def test_empty_registry_returns_none():
    assert McpGitContextProvider(_FakeRegistry({}), "/repo").current_branch() is None
