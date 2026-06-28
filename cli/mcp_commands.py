"""CLI-обработчик /mcp — управление подключёнными MCP-серверами.

Команды:
  /mcp                 — список настроенных серверов и их статус
  /mcp list            — то же самое
  /mcp tools           — все тулы, обнаруженные на запущенных серверах
  /mcp add <id> <cmd> [args...]  — добавить сервер (stdio)
  /mcp rm <id>         — удалить сервер из конфига
  /mcp enable <id>     — включить
  /mcp disable <id>    — выключить

После `add` / `rm` / `enable` / `disable` нужно перезапустить чат, чтобы
поднять/закрыть подпроцесс — статус «(требуется рестарт)» это покажет.
"""
from __future__ import annotations

import shlex
from typing import Optional

from app.ports import McpConfigRepository, McpRegistry
from cli.ansi import BOLD, CYAN, DIM, GREEN, MAGENTA, RESET, YELLOW
from domain.mcp import McpServerConfig


def handle_mcp(cmd_str: str,
               repo: McpConfigRepository,
               registry: Optional[McpRegistry]) -> None:
    try:
        parts = shlex.split(cmd_str)
    except ValueError as e:
        print(f"{YELLOW}  Не разобрал команду: {e}{RESET}")
        return

    sub = parts[1].lower() if len(parts) > 1 else "list"

    if sub == "list":
        _print_servers(repo, registry)

    elif sub == "tools":
        _print_tools(registry)

    elif sub == "add":
        _handle_add(parts[2:], repo)

    elif sub in ("rm", "remove", "delete"):
        if len(parts) < 3:
            print(f"{YELLOW}  Использование: /mcp rm <id>{RESET}")
            return
        sid = parts[2]
        if repo.get(sid) is None:
            print(f"{YELLOW}  Сервер «{sid}» не найден в конфиге.{RESET}")
            return
        repo.delete(sid)
        print(f"{GREEN}  Удалён сервер «{sid}». Перезапусти чат, чтобы погасить подпроцесс.{RESET}")

    elif sub in ("enable", "disable"):
        if len(parts) < 3:
            print(f"{YELLOW}  Использование: /mcp {sub} <id>{RESET}")
            return
        sid = parts[2]
        if repo.get(sid) is None:
            print(f"{YELLOW}  Сервер «{sid}» не найден.{RESET}")
            return
        repo.set_enabled(sid, sub == "enable")
        print(f"{GREEN}  Сервер «{sid}» {'включён' if sub == 'enable' else 'выключен'}. "
              f"Перезапусти чат, чтобы изменения применились.{RESET}")

    else:
        print(f"{YELLOW}  Подкоманды /mcp: list · tools · add · rm · enable · disable{RESET}")


def _handle_add(args: list, repo: McpConfigRepository) -> None:
    """Принимает оба формата:
        /mcp add <id> <command> [args...]                      (stdio)
        /mcp add <id> --http <url> [--header "K: V"]...        (http)
    """
    if not args:
        _print_add_usage()
        return
    server_id = args[0]
    rest      = args[1:]
    if not rest:
        _print_add_usage()
        return
    if repo.get(server_id) is not None:
        print(f"{YELLOW}  Сервер «{server_id}» уже существует. Сначала /mcp rm.{RESET}")
        return

    if rest[0] == "--http":
        cfg = _parse_http_args(server_id, rest[1:])
        if cfg is None:
            return
        repo.save(cfg)
        print(f"{GREEN}  Добавлен HTTP MCP-сервер «{server_id}» → {cfg.url}{RESET}")
    else:
        command = rest[0]
        cmd_args = tuple(rest[1:])
        cfg = McpServerConfig(server_id=server_id, command=command, args=cmd_args)
        repo.save(cfg)
        print(f"{GREEN}  Добавлен сервер «{server_id}» → {command} "
              f"{' '.join(cmd_args)}{RESET}")
    print(f"{DIM}  Перезапусти чат, чтобы поднять/подключить сервер.{RESET}")


def _parse_http_args(server_id: str, args: list) -> "McpServerConfig | None":
    if not args:
        print(f"{YELLOW}  /mcp add <id> --http <url> [--header \"K: V\"]...{RESET}")
        return None
    url = args[0]
    if url.startswith("--"):
        print(f"{YELLOW}  После --http должен идти URL, а не {url!r}{RESET}")
        return None
    headers: dict = {}
    i = 1
    while i < len(args):
        flag = args[i]
        if flag == "--header":
            if i + 1 >= len(args):
                print(f"{YELLOW}  Флаг --header требует значение «Key: Value».{RESET}")
                return None
            kv = args[i + 1]
            if ":" not in kv:
                print(f"{YELLOW}  --header ожидает формат «Key: Value», получено {kv!r}.{RESET}")
                return None
            key, _, value = kv.partition(":")
            headers[key.strip()] = value.strip()
            i += 2
        else:
            print(f"{YELLOW}  Неизвестный флаг {flag!r}.{RESET}")
            return None
    return McpServerConfig(
        server_id = server_id,
        transport = "http",
        url       = url,
        headers   = headers,
    )


def _print_add_usage() -> None:
    print(f"{YELLOW}  Использование:{RESET}")
    print(f"    /mcp add <id> <command> [args...]              {DIM}(stdio){RESET}")
    print(f"    /mcp add <id> --http <url> [--header \"K: V\"]   {DIM}(http){RESET}")
    print(f"{DIM}  Примеры:{RESET}")
    print(f"{DIM}    /mcp add fs npx -y @modelcontextprotocol/server-filesystem ~/tmp{RESET}")
    print(f"{DIM}    /mcp add tinvest --http http://host/mcp --header \"Authorization: Bearer xxx\"{RESET}")


def _print_servers(repo: McpConfigRepository, registry: Optional[McpRegistry]) -> None:
    items = repo.list_all()
    if not items:
        print(f"{DIM}  MCP-серверы не настроены. /mcp add <id> <command> ...{RESET}")
        return

    running = {c.server_id for c in (registry.clients() if registry else [])}
    print(f"\n{BOLD}{MAGENTA}MCP-серверы:{RESET}")
    for c in items:
        state = (
            f"{GREEN}● running{RESET}" if c.server_id in running
            else (f"{DIM}○ stopped{RESET}" if c.enabled
                  else f"{DIM}○ disabled{RESET}")
        )
        if c.transport == "http":
            target = f"[http] {c.url}"
        else:
            target = f"[stdio] {c.command} {' '.join(c.args)}".rstrip()
        print(f"  {state}  {BOLD}{c.server_id}{RESET}  {DIM}{target}{RESET}")
    failures = registry.failures() if registry and hasattr(registry, "failures") else []
    if failures:
        print(f"\n{YELLOW}  Ошибки при старте:{RESET}")
        for sid, err in failures:
            print(f"    {YELLOW}•{RESET} {sid}: {err}")
    print()


def _print_tools(registry: Optional[McpRegistry]) -> None:
    if registry is None:
        print(f"{DIM}  Реестр MCP не инициализирован.{RESET}")
        return
    tools = registry.all_tools()
    if not tools:
        print(f"{DIM}  Тулов не обнаружено (ни один MCP-сервер не запущен).{RESET}")
        return
    by_server: dict = {}
    for t in tools:
        by_server.setdefault(t.server_id, []).append(t)
    print(f"\n{BOLD}{MAGENTA}Обнаруженные тулы:{RESET}")
    for sid, ts in by_server.items():
        print(f"  {BOLD}{sid}{RESET}  {DIM}({len(ts)}){RESET}")
        for t in ts:
            desc = (t.description or "").strip().splitlines()[0] if t.description else ""
            short = desc[:80] + ("…" if len(desc) > 80 else "")
            print(f"    {CYAN}{t.name}{RESET}  {DIM}{short}{RESET}")
    print()
