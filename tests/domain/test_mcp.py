"""Тесты доменных утилит из domain/mcp.py."""
from __future__ import annotations

from domain.mcp import (
    McpServerConfig,
    make_qualified_tool_name,
    sanitize_name_part,
    split_qualified_tool_name,
)


def test_sanitize_name_part_replaces_disallowed_chars():
    assert sanitize_name_part("my server.1") == "my_server_1"


def test_sanitize_name_part_does_not_collapse_valid_chars():
    assert sanitize_name_part("ABC_xyz-09") == "ABC_xyz-09"


def test_sanitize_name_part_replaces_cyrillic_with_underscore():
    # Кириллица не в [A-Za-z0-9_-] — должна стать "_".
    assert sanitize_name_part("привет") == "_" * len("привет")


def test_make_qualified_tool_name_uses_double_underscore_separator():
    assert make_qualified_tool_name("fs", "read_file") == "fs__read_file"


def test_make_qualified_tool_name_truncates_to_64():
    name = make_qualified_tool_name("a" * 50, "b" * 50)
    assert len(name) == 64


def test_split_qualified_tool_name_roundtrip():
    full = make_qualified_tool_name("srv", "do_thing")
    assert split_qualified_tool_name(full) == ("srv", "do_thing")


def test_split_qualified_tool_name_without_separator():
    assert split_qualified_tool_name("plain") == ("", "plain")


def test_mcp_server_config_roundtrip():
    cfg = McpServerConfig(
        server_id = "fs",
        command   = "npx",
        args      = ("-y", "@modelcontextprotocol/server-filesystem", "/tmp"),
        env       = {"FOO": "bar"},
        cwd       = "/var/work",
        enabled   = False,
    )
    restored = McpServerConfig.from_dict(cfg.to_dict())
    assert restored == cfg


def test_mcp_server_config_defaults():
    cfg = McpServerConfig.from_dict({"server_id": "x", "command": "echo"})
    assert cfg.enabled is True
    assert cfg.transport == "stdio"
    assert cfg.args == ()
    assert cfg.env == {}
