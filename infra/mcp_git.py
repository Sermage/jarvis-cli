"""Получение git-ветки проекта через MCP-сервер (`mcp-server-git`).

Реализует порт `app.ports.GitContextProvider`. Находит среди запущенных
MCP-серверов подходящий git-тул, вызывает его и вытаскивает имя текущей ветки
из текстового ответа. Ничего не парсит из локального git напрямую — данные
идут именно через MCP, как требует задание.

Регистрация сервера в чате:
    /mcp add git uvx mcp-server-git --repository ~/AndroidStudioProjects/jarvis-cli
"""
from __future__ import annotations

import re
from typing import Optional

from app.ports import McpRegistry

# Порядок предпочтения тулов: git_status печатает «On branch <name>»,
# git_branch — список веток с «* <name>» у текущей.
_TOOL_PREFERENCE = ("git_status", "git_branch")

_ON_BRANCH = re.compile(r"On branch (\S+)")
_CURRENT_BRANCH_MARK = re.compile(r"^\*\s+(\S+)", re.MULTILINE)


def parse_branch(text: str) -> Optional[str]:
    """Достать имя ветки из вывода git_status или git_branch."""
    if not text:
        return None
    m = _ON_BRANCH.search(text)
    if m:
        return m.group(1)
    m = _CURRENT_BRANCH_MARK.search(text)
    if m:
        return m.group(1)
    return None


class McpGitContextProvider:
    """Берёт текущую git-ветку у первого доступного git-MCP-сервера."""

    def __init__(self, registry: McpRegistry, repo_path: str):
        self._registry = registry
        self._repo_path = repo_path

    def _find_tool(self):
        """Вернуть (client, tool_name) для наиболее подходящего git-тула."""
        tools = self._registry.all_tools()
        by_name = {t.name: t for t in tools}
        chosen = None
        for pref in _TOOL_PREFERENCE:
            if pref in by_name:
                chosen = by_name[pref]
                break
        if chosen is None:  # ни status/branch — берём любой git-тул как запасной
            for t in tools:
                if t.name.startswith("git") or "git" in t.server_id.lower():
                    chosen = t
                    break
        if chosen is None:
            return None
        client = self._registry.get(chosen.server_id)
        return (client, chosen.name) if client is not None else None

    def current_branch(self) -> Optional[str]:
        found = self._find_tool()
        if found is None:
            return None
        client, tool_name = found
        result = client.call_tool(tool_name, {"repo_path": self._repo_path})
        if result.is_error:
            return None
        return parse_branch(result.text)
