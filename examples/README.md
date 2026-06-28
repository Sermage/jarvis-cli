# MCP-демо для jarvis-cli

Здесь лежат игрушечные MCP-серверы и smoke-скрипт для интеграции
`jarvis-cli` с инструментами по MCP-протоколу.

## Что доступно

- `mcp_servers/calc_server.py` — арифметика: `add`, `multiply`, `sqrt`
- `mcp_servers/notes_server.py` — заметки в памяти: `save_note`, `list_notes`, `read_note`, `delete_note`
- `mcp_smoke.py` — поднимает оба сервера, проверяет реестр, прямые вызовы и ToolRouter с подставным LLM (без сети)

## Зарегистрировать оба сервера в jarvis-cli

```bash
python3 chat.py
```

В REPL:

```
/mcp add calc  python3 examples/mcp_servers/calc_server.py
/mcp add notes python3 examples/mcp_servers/notes_server.py
/quit
```

Запустить заново — серверы поднимутся автоматически:

```
$ python3 chat.py
MCP: запущено 2 серверов, обнаружено 7 тулов.
Tool-loop активен (provider=deepseek).
```

Проверить:

```
/mcp list      # статус
/mcp tools     # все тулы
```

## Длинный флоу с DeepSeek

В чате (требуется `DEEPSEEK_API_KEY` в `.env`):

> Посчитай sqrt(2025), умножь результат на 7, сохрани в заметку «pi-ish»
> и потом прочитай её, чтобы убедиться.

DeepSeek сам выберет порядок вызовов: `calc.sqrt → calc.multiply →
notes.save_note → notes.read_note`. `jarvis-cli` распечатает trace
с tool-loop'ом — это и есть «длинный флоу взаимодействия с несколькими
MCP-серверами».

## Smoke без сети

Если DeepSeek-ключа нет под рукой — `examples/mcp_smoke.py` гоняет
тот же tool-loop с заскриптованным LLM:

```bash
python3 examples/mcp_smoke.py
```

## Внешние MCP-серверы (stdio)

Через `/mcp add` можно подключить и стандартные пакеты:

```
/mcp add fs   npx -y @modelcontextprotocol/server-filesystem  ~/tmp
/mcp add mem  npx -y @modelcontextprotocol/server-memory
```

Любой stdio-сервер, говорящий JSON-RPC по MCP-спеке, поднимется без
изменений в коде.

## HTTP MCP-серверы (Streamable HTTP, 2025-03-26)

```
/mcp add tinvest --http http://host:8000/mcp --header "Authorization: Bearer xxx"
```

`mcp_tinvest_demo.py` — живой длинный флоу через ТРИ сервера сразу
(tinvest по HTTP + calc + notes по stdio). Запускается так:

```bash
python3 examples/mcp_tinvest_demo.py
```

Скрипт берёт URL и токен tinvest напрямую из `~/.claude.json`
(секция `mcpServers.tinvest`) — не нужно дублировать секрет.

## Комплексный pipeline (6+ серверов)

`mcp_portfolio_pipeline.py` — полноценный сценарий анализа портфеля
через стандартные MCP-серверы:

| Сервер | Транспорт | Зачем |
|---|---|---|
| `time` | stdio (uvx) | ISO-timestamp в нужной таймзоне |
| `tinvest` | HTTP | счета + сводка портфеля |
| `calc` | stdio | пересчёт RUB↔USD |
| `sqlite` | stdio (uvx) | журнал `portfolio_log` |
| `filesystem` | stdio (npx) | markdown-отчёт в `~/.jarvis/mcp-workspace/` |
| `memory` | stdio (npx) | knowledge graph (`Portfolio_<account>`) |

Берёт конфиг из `~/.jarvis/mcp/servers.json` — тот же, что и REPL.
На контрольном прогоне сделал 13 tool_calls на 6 серверах в одном flow,
включая авто-восстановление после `Access denied` (попробовал, дёрнул
`filesystem.list_allowed_directories`, переписал путь, повторил).

## Установка зависимостей для полного набора

Python-серверы (`sqlite`, `time`, `fetch`) запускаются через `uvx`:

```bash
brew install uv      # macOS
# или: pip install uv
```

Дальше `uvx mcp-server-<...>` сам подтянет пакет в кэш при первом
запуске сервера jarvis-cli.
