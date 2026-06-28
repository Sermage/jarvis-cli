"""Тесты StdioMcpRegistry с фейковыми клиентами."""
from __future__ import annotations

from dataclasses import dataclass, field

from domain.mcp import McpServerConfig, McpTool, ToolResult
from infra.mcp_registry import StdioMcpRegistry


@dataclass
class _FakeClient:
    server_id: str
    tools: list = field(default_factory=list)
    started: bool = False
    closed: bool = False
    start_should_fail: bool = False

    def start(self):
        if self.start_should_fail:
            raise RuntimeError("cant start")
        self.started = True

    def list_tools(self):
        return list(self.tools)

    def call_tool(self, name, args):
        return ToolResult(text="")

    def close(self):
        self.closed = True


@dataclass
class _FakeRepo:
    items: list

    def list_all(self):
        return list(self.items)

    def get(self, server_id):
        for c in self.items:
            if c.server_id == server_id:
                return c
        return None

    def save(self, cfg): pass
    def delete(self, server_id): pass
    def set_enabled(self, server_id, enabled): pass


def _cfg(server_id, enabled=True):
    return McpServerConfig(server_id=server_id, command="x", enabled=enabled)


def test_start_all_brings_up_only_enabled_servers():
    fs = _FakeClient(server_id="fs", tools=[McpTool("fs", "ls", "", {})])
    db = _FakeClient(server_id="db", tools=[McpTool("db", "q", "", {})])

    def factory(cfg):
        return {"fs": fs, "db": db}[cfg.server_id]

    repo = _FakeRepo([_cfg("fs"), _cfg("db", enabled=False)])
    reg = StdioMcpRegistry(repo, factory=factory)
    reg.start_all()
    assert fs.started
    assert not db.started
    assert [c.server_id for c in reg.clients()] == ["fs"]


def test_failures_recorded_when_start_raises():
    bad = _FakeClient(server_id="bad", start_should_fail=True)

    def factory(cfg):
        return bad

    reg = StdioMcpRegistry(_FakeRepo([_cfg("bad")]), factory=factory)
    reg.start_all()
    assert reg.clients() == []
    assert reg.failures()[0][0] == "bad"
    assert bad.closed, "при провале старта клиент должен быть закрыт, чтобы не висел"


def test_all_tools_aggregates_across_servers():
    a = _FakeClient(server_id="a", tools=[McpTool("a", "t1", "", {}),
                                          McpTool("a", "t2", "", {})])
    b = _FakeClient(server_id="b", tools=[McpTool("b", "t3", "", {})])
    reg = StdioMcpRegistry(_FakeRepo([_cfg("a"), _cfg("b")]),
                           factory=lambda cfg: {"a": a, "b": b}[cfg.server_id])
    reg.start_all()
    names = [(t.server_id, t.name) for t in reg.all_tools()]
    assert sorted(names) == [("a", "t1"), ("a", "t2"), ("b", "t3")]


def test_get_returns_client_by_server_id():
    fs = _FakeClient(server_id="fs")
    reg = StdioMcpRegistry(_FakeRepo([_cfg("fs")]), factory=lambda cfg: fs)
    reg.start_all()
    assert reg.get("fs") is fs
    assert reg.get("missing") is None


def test_shutdown_closes_all_clients():
    fs = _FakeClient(server_id="fs")
    db = _FakeClient(server_id="db")
    reg = StdioMcpRegistry(_FakeRepo([_cfg("fs"), _cfg("db")]),
                           factory=lambda cfg: {"fs": fs, "db": db}[cfg.server_id])
    reg.start_all()
    reg.shutdown()
    assert fs.closed and db.closed
    assert reg.clients() == []
