"""In-process источник файловых инструментов для агента.

Реализует тот же протокол `McpClient` (`app.ports.McpClient`), что и
stdio/http-клиенты, но вместо JSON-RPC к подпроцессу выполняет операции с
файловой системой напрямую. За счёт этого он бесшовно встаёт в
`McpRegistry`, а `ToolRouter` отдаёт его тулы модели и маршрутизирует
вызовы `fs__<tool>` сюда — как к обычному MCP-серверу. Никаких правок в
tool-loop не требуется: «агент сам выбирает инструмент» уже работает.

Все пути — внутри sandbox-root (`root`). Любой выход за него (через `..`
или симлинк) отклоняется. Запись (`write_file`) проходит через
инъектируемый `confirm(rel_path, unified_diff) -> bool`: CLI показывает
diff и спрашивает подтверждение, тесты подставляют фейк. Так закрывается
требование «изменения сохраняются или выводятся как diff».

Тулы:
  • list_dir  — перечислить каталог
  • read_file — прочитать файл
  • search    — искать по дереву (подстрока или regex), с glob-фильтром
  • write_file— создать/перезаписать файл (diff + подтверждение)
"""
from __future__ import annotations

import difflib
import fnmatch
import os
import re
from pathlib import Path
from typing import Callable, Optional

from domain.mcp import McpTool, ToolResult


class PathEscapeError(Exception):
    """Попытка обратиться к пути за пределами sandbox-root."""


# Подтверждение записи: (относительный путь, unified diff) -> писать ли.
Confirmer = Callable[[str, str], bool]

# Каталоги, которые не имеет смысла обходить при поиске/листинге.
_SKIP_DIRS = {
    ".git", ".hg", ".svn",
    ".venv", "venv", "env",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", ".idea", ".vscode", "dist", "build",
}

_DEFAULT_MAX_BYTES = 200_000       # потолок на чтение одного файла
_DEFAULT_MAX_MATCHES = 200         # потолок на число совпадений поиска
_SEARCH_MAX_FILE_BYTES = 2_000_000  # файлы крупнее этого при поиске пропускаем


def _always_yes(_rel: str, _diff: str) -> bool:
    return True


class LocalFilesystemClient:
    """Файловые тулы над sandbox-каталогом `root`.

    `confirm` вызывается перед каждой записью; по умолчанию — авто-«да»
    (удобно для демо/скриптов). CLI прокидывает интерактивный промпт,
    тесты — фейк с нужным решением.
    """

    def __init__(self,
                 root: str,
                 server_id: str = "fs",
                 confirm: Optional[Confirmer] = None,
                 max_read_bytes: int = _DEFAULT_MAX_BYTES,
                 max_matches: int = _DEFAULT_MAX_MATCHES):
        self.server_id      = server_id
        self._root          = Path(root).expanduser().resolve()
        self._confirm       = confirm or _always_yes
        self._max_read      = max_read_bytes
        self._max_matches   = max_matches

    # ── жизненный цикл (McpClient) ────────────────────────────────────────────

    def start(self) -> None:
        if not self._root.is_dir():
            raise RuntimeError(f"fs root не каталог: {self._root}")

    def close(self) -> None:
        pass

    # ── описание тулов ────────────────────────────────────────────────────────

    def list_tools(self) -> list[McpTool]:
        return [
            self._tool("list_dir",
                       "Перечислить содержимое каталога проекта. Пути — "
                       "относительно корня проекта. Начни с '.' если не знаешь структуру.",
                       {"path": {"type": "string",
                                 "description": "Относительный путь к каталогу (по умолчанию корень).",
                                 "default": "."}}),
            self._tool("read_file",
                       "Прочитать текстовый файл проекта целиком. Используй для "
                       "анализа содержимого перед изменением.",
                       {"path": {"type": "string",
                                 "description": "Относительный путь к файлу."}},
                       required=["path"]),
            self._tool("search",
                       "Искать текст по нескольким файлам проекта сразу (grep по "
                       "дереву). Возвращает совпадения в формате 'путь:строка: текст'. "
                       "Используй, чтобы найти все места использования компонента/API.",
                       {"query": {"type": "string",
                                  "description": "Что искать: подстрока или regex."},
                        "glob": {"type": "string",
                                 "description": "Фильтр по имени/пути файла, напр. '*.py' или '*.md'. По умолчанию все файлы.",
                                 "default": "*"},
                        "regex": {"type": "boolean",
                                  "description": "Трактовать query как регулярное выражение.",
                                  "default": False},
                        "ignore_case": {"type": "boolean",
                                        "description": "Игнорировать регистр.",
                                        "default": False}},
                       required=["query"]),
            self._tool("write_file",
                       "Создать новый или перезаписать существующий файл. Сначала "
                       "показывается unified diff и запрашивается подтверждение "
                       "пользователя; без подтверждения запись не происходит.",
                       {"path": {"type": "string",
                                 "description": "Относительный путь к файлу."},
                        "content": {"type": "string",
                                    "description": "Полное новое содержимое файла."}},
                       required=["path", "content"]),
        ]

    def _tool(self, name: str, description: str, properties: dict,
              required: Optional[list] = None) -> McpTool:
        return McpTool(
            server_id    = self.server_id,
            name         = name,
            description  = description,
            input_schema = {
                "type": "object",
                "properties": properties,
                "required": required or [],
            },
        )

    # ── вызов тула (McpClient) ────────────────────────────────────────────────

    def call_tool(self, name: str, arguments: dict) -> ToolResult:
        args = arguments or {}
        try:
            if name == "list_dir":
                return self._list_dir(args.get("path", "."))
            if name == "read_file":
                return self._read_file(args["path"])
            if name == "search":
                return self._search(
                    query       = args["query"],
                    glob        = args.get("glob", "*") or "*",
                    regex       = bool(args.get("regex", False)),
                    ignore_case = bool(args.get("ignore_case", False)),
                )
            if name == "write_file":
                return self._write_file(args["path"], args["content"])
        except KeyError as e:
            return ToolResult(text=f"Отсутствует обязательный аргумент: {e}", is_error=True)
        except (PathEscapeError, FileNotFoundError, IsADirectoryError,
                NotADirectoryError, UnicodeDecodeError, OSError) as e:
            return ToolResult(text=f"Ошибка: {e}", is_error=True)
        return ToolResult(text=f"Неизвестный тул: {name}", is_error=True)

    # ── реализация тулов ──────────────────────────────────────────────────────

    def _list_dir(self, rel: str) -> ToolResult:
        target = self._resolve(rel)
        if not target.is_dir():
            return ToolResult(text=f"Не каталог: {rel}", is_error=True)
        lines: list[str] = []
        for entry in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name)):
            if entry.is_dir():
                if entry.name in _SKIP_DIRS:
                    continue
                lines.append(f"{self._rel(entry)}/")
            else:
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                lines.append(f"{self._rel(entry)}  ({size} B)")
        body = "\n".join(lines) if lines else "(пусто)"
        return ToolResult(text=body)

    def _read_file(self, rel: str) -> ToolResult:
        target = self._resolve(rel)
        if target.is_dir():
            return ToolResult(text=f"Это каталог, не файл: {rel}", is_error=True)
        data = target.read_bytes()
        truncated = len(data) > self._max_read
        text = data[: self._max_read].decode("utf-8", errors="replace")
        if truncated:
            text += f"\n… (обрезано, показано {self._max_read} из {len(data)} байт)"
        return ToolResult(text=text)

    def _search(self, query: str, glob: str, regex: bool, ignore_case: bool) -> ToolResult:
        flags = re.IGNORECASE if ignore_case else 0
        if regex:
            matcher = re.compile(query, flags)
            def hit(line: str) -> bool:
                return matcher.search(line) is not None
        else:
            needle = query.lower() if ignore_case else query
            def hit(line: str) -> bool:
                hay = line.lower() if ignore_case else line
                return needle in hay

        results: list[str] = []
        truncated = False
        for path in self._walk_files():
            rel = self._rel(path)
            if not (fnmatch.fnmatch(rel, glob) or fnmatch.fnmatch(path.name, glob)):
                continue
            try:
                if path.stat().st_size > _SEARCH_MAX_FILE_BYTES:
                    continue
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue  # бинарь/недоступно — молча пропускаем
            for lineno, line in enumerate(content.splitlines(), 1):
                if hit(line):
                    results.append(f"{rel}:{lineno}: {line.strip()[:200]}")
                    if len(results) >= self._max_matches:
                        truncated = True
                        break
            if truncated:
                break

        if not results:
            return ToolResult(text=f"Совпадений не найдено: {query!r} (glob={glob})")
        header = f"Найдено совпадений: {len(results)}" + (" (обрезано)" if truncated else "")
        return ToolResult(text=header + "\n" + "\n".join(results))

    def _write_file(self, rel: str, content: str) -> ToolResult:
        target = self._resolve(rel, must_exist=False)
        if target.is_dir():
            return ToolResult(text=f"Это каталог, не файл: {rel}", is_error=True)

        old = ""
        if target.exists():
            try:
                old = target.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                return ToolResult(text=f"Не могу прочитать текущий файл для diff: {rel}",
                                  is_error=True)

        if old == content:
            return ToolResult(text=f"Изменений нет: {rel} уже содержит этот текст.")

        diff = self._unified_diff(rel, old, content)

        if not self._confirm(rel, diff):
            return ToolResult(text=f"Запись в {rel} отклонена пользователем. Файл не изменён.")

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        verb = "создан" if not old else "обновлён"
        return ToolResult(text=f"Файл {verb}: {rel}\n\n{diff}")

    # ── вспомогательное (чистое) ──────────────────────────────────────────────

    @staticmethod
    def _unified_diff(rel: str, old: str, new: str) -> str:
        diff = difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        )
        text = "".join(diff)
        return text or f"(новый файл {rel})"

    def _walk_files(self):
        for dirpath, dirnames, filenames in os.walk(self._root):
            dirnames[:] = [d for d in dirnames
                           if d not in _SKIP_DIRS and not d.endswith(".egg-info")]
            for name in filenames:
                yield Path(dirpath) / name

    def _resolve(self, rel: str, must_exist: bool = True) -> Path:
        raw = (self._root / (rel or ".")).resolve()
        if raw != self._root and self._root not in raw.parents:
            raise PathEscapeError(
                f"путь вне корня проекта запрещён: {rel}"
            )
        if must_exist and not raw.exists():
            raise FileNotFoundError(f"нет такого пути: {rel}")
        return raw

    def _rel(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self._root).as_posix()
        except ValueError:
            return path.name
