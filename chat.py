#!/usr/bin/env python3
import sys
import os
import re
import uuid
import time
import json
import subprocess
import threading
from typing import Optional
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── Config ────────────────────────────────────────────────────────────────────
def _load_env():
    env_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())

_load_env()

AUTH_KEY = os.environ.get("GIGACHAT_AUTH_KEY", "")
OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
CHAT_URL  = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
SCOPE     = "GIGACHAT_API_PERS"

MODELS = {
    "1": ("GigaChat",       "GigaChat (слабая)"),
    "2": ("GigaChat-Pro",   "GigaChat-Pro (средняя)"),
    "3": ("GigaChat-Max",   "GigaChat-Max (сильная)"),
    "4": ("GigaChat-2",     "GigaChat-2 (слабая, v2)"),
    "5": ("GigaChat-2-Pro", "GigaChat-2-Pro (средняя, v2)"),
    "6": ("GigaChat-2-Max", "GigaChat-2-Max (сильная, v2)"),
}

DEFAULT_PARAMS = {
    "model":       "GigaChat",
    "temperature": None,
    "max_tokens":  None,
}

HISTORY_DIR  = os.path.expanduser("~/.jarvis/sessions")
PROFILES_DIR = os.path.expanduser("~/.jarvis/profiles")
WORKING_DIR  = os.path.expanduser("~/.jarvis/working")
KNOWLEDGE_DIR = os.path.expanduser("~/.jarvis/knowledge")
TASKS_DIR    = os.path.expanduser("~/.jarvis/tasks")
ACTIVE_TASK_FILE = os.path.join(TASKS_DIR, "active")
MAX_SESSIONS = 20

DEFAULT_PROFILE_CONTENT = """\
# Профиль агента

Ты — Jarvis, интеллектуальный ассистент-разработчик.

## Роль
Помогаешь с разработкой программного обеспечения: пишешь и объясняешь код,
находишь баги, предлагаешь архитектурные решения.

## Правила
- Отвечай на русском языке, если пользователь пишет по-русски
- Давай краткие и точные ответы
- Предпочитай конкретные примеры кода абстрактным объяснениям
- Если вопрос неоднозначен — уточни, прежде чем отвечать

## Ограничения
- Не придумывай факты — лучше скажи, что не знаешь
- Не генерируй вредоносный или небезопасный код
"""

# ── ANSI colors ───────────────────────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
CYAN   = "\033[36m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
MAGENTA= "\033[35m"
BLUE   = "\033[34m"
DIM    = "\033[2m"

# ── Spinner ───────────────────────────────────────────────────────────────────
class Spinner:
    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, label=""):
        self.label = label
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)

    def _spin(self):
        i = 0
        while not self._stop.is_set():
            frame = self.FRAMES[i % len(self.FRAMES)]
            sys.stdout.write(f"\r{DIM}{frame} {self.label}{RESET}")
            sys.stdout.flush()
            time.sleep(0.08)
            i += 1

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._thread.join()
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

# ══════════════════════════════════════════════════════════════════════════════
# СЛОЙ 3 — ДОЛГОВРЕМЕННАЯ ПАМЯТЬ: профили + база знаний
# ══════════════════════════════════════════════════════════════════════════════
_current_profile_path = None
_current_profile_text = None

def list_profiles() -> list:
    if not os.path.isdir(PROFILES_DIR):
        return []
    return sorted([
        os.path.join(PROFILES_DIR, f)
        for f in os.listdir(PROFILES_DIR)
        if f.endswith(".md")
    ])

def load_profile(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read().strip()

def ensure_default_profile() -> str:
    os.makedirs(PROFILES_DIR, exist_ok=True)
    default = os.path.join(PROFILES_DIR, "default.md")
    if not os.path.exists(default):
        with open(default, "w", encoding="utf-8") as f:
            f.write(DEFAULT_PROFILE_CONTENT)
    return default

def profile_name(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]

PROFILE_TEMPLATE = """\
# {name}

## Роль
Опиши роль и личность агента.

## Правила
- Правило 1
- Правило 2

## Ограничения
- Ограничение 1
"""

def open_in_editor(path: str):
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "nano"
    subprocess.call([editor, path])

def create_profile() -> tuple:
    try:
        name = input("Название профиля (например, android-dev): ").strip()
    except (EOFError, KeyboardInterrupt):
        return _current_profile_path, _current_profile_text

    if not name:
        print(f"{YELLOW}Название не может быть пустым.{RESET}")
        return _current_profile_path, _current_profile_text

    safe_name = name.replace(" ", "-").replace("/", "-")
    path = os.path.join(PROFILES_DIR, f"{safe_name}.md")

    if os.path.exists(path):
        print(f"{YELLOW}Профиль «{safe_name}» уже существует.{RESET}")
    else:
        os.makedirs(PROFILES_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(PROFILE_TEMPLATE.format(name=name))

    print(f"{DIM}Открываю редактор…{RESET}")
    open_in_editor(path)

    text = load_profile(path)
    print(f"{GREEN}Профиль «{safe_name}» загружен.{RESET}")
    return path, text

def edit_profile() -> tuple:
    profiles = list_profiles()
    if not profiles:
        print(f"{YELLOW}Нет профилей для редактирования.{RESET}")
        return _current_profile_path, _current_profile_text

    print(f"\n{BOLD}Выберите профиль для редактирования:{RESET}")
    for i, p in enumerate(profiles, 1):
        marker = " ◀" if p == _current_profile_path else ""
        print(f"  {CYAN}{i}{RESET}. {profile_name(p)}{marker}")
    try:
        choice = input("Номер (Enter — отмена): ").strip()
    except (EOFError, KeyboardInterrupt):
        return _current_profile_path, _current_profile_text

    if not (choice.isdigit() and 1 <= int(choice) <= len(profiles)):
        return _current_profile_path, _current_profile_text

    path = profiles[int(choice) - 1]
    print(f"{DIM}Открываю редактор…{RESET}")
    open_in_editor(path)
    text = load_profile(path)
    if path == _current_profile_path:
        print(f"{GREEN}Профиль «{profile_name(path)}» обновлён и перезагружен.{RESET}")
        return path, text
    print(f"{GREEN}Профиль «{profile_name(path)}» сохранён.{RESET}")
    return _current_profile_path, _current_profile_text


def delete_profile() -> tuple:
    profiles = list_profiles()
    if not profiles:
        print(f"{YELLOW}Нет профилей для удаления.{RESET}")
        return _current_profile_path, _current_profile_text

    print(f"\n{BOLD}Выберите профиль для удаления:{RESET}")
    for i, p in enumerate(profiles, 1):
        marker = f" {YELLOW}◀ активный{RESET}" if p == _current_profile_path else ""
        print(f"  {CYAN}{i}{RESET}. {profile_name(p)}{marker}")
    try:
        choice = input("Номер (Enter — отмена): ").strip()
    except (EOFError, KeyboardInterrupt):
        return _current_profile_path, _current_profile_text

    if not (choice.isdigit() and 1 <= int(choice) <= len(profiles)):
        return _current_profile_path, _current_profile_text

    path = profiles[int(choice) - 1]
    name = profile_name(path)
    try:
        confirm = input(f"Удалить «{name}»? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return _current_profile_path, _current_profile_text

    if confirm != "y":
        print(f"{DIM}Отменено.{RESET}")
        return _current_profile_path, _current_profile_text

    os.remove(path)
    print(f"{GREEN}Профиль «{name}» удалён.{RESET}")
    if path == _current_profile_path:
        print(f"{YELLOW}Активный профиль удалён — профиль сброшен.{RESET}")
        return None, None
    return _current_profile_path, _current_profile_text


def choose_profile() -> tuple:
    profiles = list_profiles()
    if not profiles:
        path = ensure_default_profile()
        return path, load_profile(path)

    print(f"\n{BOLD}Выберите профиль:{RESET}")
    for i, p in enumerate(profiles, 1):
        marker = " ◀" if p == _current_profile_path else ""
        print(f"  {CYAN}{i}{RESET}. {profile_name(p)}{marker}")
    print(f"  {CYAN}+{RESET}. Создать новый")
    print(f"  {CYAN}n{RESET}. Без профиля")
    try:
        choice = input("Номер (Enter — оставить текущий): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return _current_profile_path, _current_profile_text

    if choice == "+":
        return create_profile()
    if choice == "n":
        return None, None
    if choice.isdigit() and 1 <= int(choice) <= len(profiles):
        path = profiles[int(choice) - 1]
        text = load_profile(path)
        print(f"{GREEN}Профиль: {profile_name(path)}{RESET}")
        return path, text
    return _current_profile_path, _current_profile_text

# ── База знаний (долговременная) ──────────────────────────────────────────────
def _knowledge_file(name: str) -> str:
    return os.path.join(KNOWLEDGE_DIR, f"{name}.md")

def save_knowledge(name: str, content: str):
    os.makedirs(KNOWLEDGE_DIR, exist_ok=True)
    path = _knowledge_file(name)
    with open(path, "w", encoding="utf-8") as f:
        ts = time.strftime("%Y-%m-%d %H:%M")
        f.write(f"<!-- сохранено: {ts} -->\n{content}")

def load_all_knowledge() -> str:
    if not os.path.isdir(KNOWLEDGE_DIR):
        return ""
    parts = []
    for fname in sorted(os.listdir(KNOWLEDGE_DIR)):
        if fname.endswith(".md"):
            path = os.path.join(KNOWLEDGE_DIR, fname)
            try:
                with open(path, encoding="utf-8") as f:
                    text = f.read().strip()
                name = os.path.splitext(fname)[0]
                parts.append(f"### {name}\n{text}")
            except Exception:
                pass
    return "\n\n".join(parts)

def list_knowledge() -> list:
    if not os.path.isdir(KNOWLEDGE_DIR):
        return []
    return [f for f in sorted(os.listdir(KNOWLEDGE_DIR)) if f.endswith(".md")]

# ══════════════════════════════════════════════════════════════════════════════
# СЛОЙ 2 — РАБОЧАЯ ПАМЯТЬ: контекст текущей задачи
# ══════════════════════════════════════════════════════════════════════════════
class WorkingMemory:
    """Рабочая память: задача, контекст, заметки для текущего сеанса работы."""

    _FILE = os.path.join(WORKING_DIR, "current.json")

    def __init__(self):
        self.task: Optional[str] = None
        self.context: dict       = {}
        self.notes: list         = []
        self.created_at: Optional[str] = None
        self.updated_at: Optional[str] = None

    # ── persistence ──────────────────────────────────────────────────────────

    def load(self) -> "WorkingMemory":
        if os.path.exists(self._FILE):
            try:
                with open(self._FILE, encoding="utf-8") as f:
                    d = json.load(f)
                self.task       = d.get("task")
                self.context    = d.get("context", {})
                self.notes      = d.get("notes", [])
                self.created_at = d.get("created_at")
                self.updated_at = d.get("updated_at")
            except Exception:
                pass
        return self

    def save(self):
        os.makedirs(WORKING_DIR, exist_ok=True)
        now = time.strftime("%Y-%m-%d %H:%M")
        if not self.created_at:
            self.created_at = now
        self.updated_at = now
        with open(self._FILE, "w", encoding="utf-8") as f:
            json.dump({
                "task":       self.task,
                "context":    self.context,
                "notes":      self.notes,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
            }, f, ensure_ascii=False, indent=2)

    def clear(self):
        self.task = self.created_at = self.updated_at = None
        self.context = {}
        self.notes   = []
        if os.path.exists(self._FILE):
            os.remove(self._FILE)

    # ── helpers ───────────────────────────────────────────────────────────────

    def is_empty(self) -> bool:
        return not self.task and not self.context and not self.notes

    def to_prompt(self) -> str:
        """Формирует блок для system prompt."""
        if self.is_empty():
            return ""
        lines = ["[РАБОЧАЯ ПАМЯТЬ]"]
        if self.task:
            lines.append(f"Текущая задача: {self.task}")
        if self.context:
            lines.append("Контекст:")
            for k, v in self.context.items():
                lines.append(f"  {k}: {v}")
        if self.notes:
            lines.append("Заметки:")
            for note in self.notes:
                lines.append(f"  • {note}")
        return "\n".join(lines)

    def show(self):
        if self.is_empty():
            print(f"    {DIM}пусто{RESET}")
            return
        if self.task:
            print(f"    {BOLD}Задача:{RESET} {self.task}")
        if self.context:
            print(f"    {BOLD}Контекст:{RESET}")
            for k, v in self.context.items():
                print(f"      {CYAN}{k}{RESET}: {v}")
        if self.notes:
            print(f"    {BOLD}Заметки:{RESET}")
            for note in self.notes:
                print(f"      • {note}")
        if self.updated_at:
            print(f"    {DIM}обновлено: {self.updated_at}{RESET}")

    def status_badge(self) -> str:
        """Однострочный индикатор для строки статуса."""
        if self.is_empty():
            return f"{DIM}рабочая: —{RESET}"
        parts = []
        if self.task:
            short = self.task[:30] + ("…" if len(self.task) > 30 else "")
            parts.append(short)
        if self.context:
            parts.append(f"{len(self.context)} ключ.")
        if self.notes:
            parts.append(f"{len(self.notes)} заметок")
        return f"{MAGENTA}рабочая: {', '.join(parts)}{RESET}"


def handle_wm(cmd_str: str, wm: WorkingMemory):
    """Обрабатывает /wm <sub> <args>."""
    parts = cmd_str.split(None, 2)
    sub   = parts[1].lower() if len(parts) > 1 else "show"

    if sub in ("show", ""):
        print(f"\n{BOLD}{MAGENTA}Рабочая память:{RESET}")
        wm.show()
        print()

    elif sub == "task":
        desc = parts[2].strip() if len(parts) > 2 else ""
        if not desc:
            try:
                desc = input("  Описание задачи: ").strip()
            except (EOFError, KeyboardInterrupt):
                return
        if desc:
            wm.task = desc
            wm.save()
            print(f"{GREEN}  Задача установлена.{RESET}")

    elif sub == "set":
        rest = parts[2] if len(parts) > 2 else ""
        kv   = rest.split(None, 1)
        if len(kv) < 2:
            print(f"{YELLOW}  Использование: /wm set <ключ> <значение>{RESET}")
        else:
            wm.context[kv[0]] = kv[1]
            wm.save()
            print(f"{GREEN}  Сохранено: {kv[0]} = {kv[1]}{RESET}")

    elif sub == "note":
        text = parts[2].strip() if len(parts) > 2 else ""
        if not text:
            try:
                text = input("  Заметка: ").strip()
            except (EOFError, KeyboardInterrupt):
                return
        if text:
            wm.notes.append(text)
            wm.save()
            print(f"{GREEN}  Заметка добавлена.{RESET}")

    elif sub == "del":
        key = parts[2].strip() if len(parts) > 2 else ""
        if key in wm.context:
            del wm.context[key]
            wm.save()
            print(f"{GREEN}  Ключ «{key}» удалён.{RESET}")
        else:
            print(f"{YELLOW}  Ключ «{key}» не найден.{RESET}")

    elif sub == "clear":
        wm.clear()
        print(f"{DIM}  Рабочая память очищена.{RESET}")

    else:
        print(f"{YELLOW}  Подкоманды /wm: show · task · set · note · del · clear{RESET}")


# ── База знаний: команды ──────────────────────────────────────────────────────

def handle_know(cmd_str: str):
    """/know save <имя> | /know list | /know show <имя>"""
    parts = cmd_str.split(None, 2)
    sub   = parts[1].lower() if len(parts) > 1 else "list"

    if sub == "list":
        files = list_knowledge()
        if not files:
            print(f"{DIM}  База знаний пуста. Используй /know save <имя>{RESET}")
        else:
            print(f"\n{BOLD}{BLUE}База знаний:{RESET}")
            for f in files:
                print(f"    {BLUE}•{RESET} {os.path.splitext(f)[0]}")
            print()

    elif sub == "save":
        name = parts[2].strip().replace(" ", "-") if len(parts) > 2 else ""
        if not name:
            try:
                name = input("  Имя записи: ").strip().replace(" ", "-")
            except (EOFError, KeyboardInterrupt):
                return
        if not name:
            print(f"{YELLOW}  Имя не может быть пустым.{RESET}")
            return
        print(f"  Введите содержимое (пустая строка — конец):")
        lines = []
        try:
            while True:
                line = input("  > ")
                if line == "":
                    break
                lines.append(line)
        except (EOFError, KeyboardInterrupt):
            pass
        if lines:
            save_knowledge(name, "\n".join(lines))
            print(f"{GREEN}  Сохранено в базу знаний: {name}{RESET}")

    elif sub == "show":
        name = parts[2].strip() if len(parts) > 2 else ""
        if not name:
            print(f"{YELLOW}  Использование: /know show <имя>{RESET}")
            return
        path = _knowledge_file(name)
        if not os.path.exists(path):
            print(f"{YELLOW}  Запись «{name}» не найдена.{RESET}")
        else:
            with open(path, encoding="utf-8") as f:
                print(f"\n{BOLD}{BLUE}{name}:{RESET}\n{f.read()}")

    else:
        print(f"{YELLOW}  Подкоманды /know: list · save · show{RESET}")


# ══════════════════════════════════════════════════════════════════════════════
# СЛОЙ 1 — КРАТКОСРОЧНАЯ ПАМЯТЬ: диалог текущей сессии
# ══════════════════════════════════════════════════════════════════════════════
_current_session_file = None

def _session_path() -> str:
    ts = time.strftime("%Y-%m-%dT%H-%M-%S")
    return os.path.join(HISTORY_DIR, f"{ts}.json")

def save_session(messages: list, params: dict):
    global _current_session_file
    os.makedirs(HISTORY_DIR, exist_ok=True)
    if _current_session_file is None:
        _current_session_file = _session_path()
    title = messages[0]["content"][:60].replace("\n", " ") if messages else ""
    data = {
        "title":      title,
        "model":      params["model"],
        "updated_at": time.strftime("%Y-%m-%d %H:%M"),
        "params":     params,
        "messages":   messages,
    }
    with open(_current_session_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    _prune_sessions()

def _prune_sessions():
    files = sorted(_list_session_files())
    for f in files[:-MAX_SESSIONS]:
        try:
            os.remove(f)
        except OSError:
            pass

def _list_session_files() -> list:
    if not os.path.isdir(HISTORY_DIR):
        return []
    return [
        os.path.join(HISTORY_DIR, f)
        for f in os.listdir(HISTORY_DIR)
        if f.endswith(".json")
    ]

def list_sessions() -> list:
    sessions = []
    for path in sorted(_list_session_files(), reverse=True):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            sessions.append({
                "path":       path,
                "title":      data.get("title", "—"),
                "model":      data.get("model", "?"),
                "updated_at": data.get("updated_at", ""),
                "count":      len(data.get("messages", [])),
                "params":     data.get("params", {}),
                "messages":   data.get("messages", []),
            })
        except Exception:
            pass
    return sessions

def clear_session(messages: list):
    global _current_session_file
    messages.clear()
    if _current_session_file and os.path.exists(_current_session_file):
        os.remove(_current_session_file)
    _current_session_file = None

# ══════════════════════════════════════════════════════════════════════════════
# СЛОЙ 4 — МАШИНА СОСТОЯНИЙ ЗАДАЧИ: структура данных и персистентность
# ══════════════════════════════════════════════════════════════════════════════
class TaskState:
    INTAKE     = "intake"
    PLANNING   = "planning"
    EXECUTION  = "execution"
    VALIDATION = "validation"
    DONE       = "done"
    ABORTED    = "aborted"

    ALL = [INTAKE, PLANNING, EXECUTION, VALIDATION, DONE, ABORTED]
    TERMINAL = {DONE, ABORTED}


# Разрешённые переходы. Откат validation → planning умышленно запрещён:
# если на этапе валидации выяснилось, что план плох, сначала идём в execution,
# а оттуда уже в planning. Это сохраняет инвариант «после planning всегда
# был хотя бы один заход в execution».
_ALLOWED_TRANSITIONS = {
    TaskState.INTAKE     : {TaskState.PLANNING,  TaskState.ABORTED},
    TaskState.PLANNING   : {TaskState.EXECUTION, TaskState.INTAKE,    TaskState.ABORTED},
    TaskState.EXECUTION  : {TaskState.VALIDATION, TaskState.PLANNING, TaskState.ABORTED},
    TaskState.VALIDATION : {TaskState.EXECUTION, TaskState.DONE,      TaskState.ABORTED},
    TaskState.DONE       : set(),
    TaskState.ABORTED    : set(),
}


class TaskTransitionError(Exception):
    pass


class StageStatus:
    PENDING       = "pending"
    IN_PROGRESS   = "in_progress"
    AWAITING_USER = "awaiting_user"
    DONE          = "done"
    FAILED        = "failed"


class StageResult:
    """Результат одной стадии задачи."""

    def __init__(self,
                 status: str = StageStatus.PENDING,
                 output: str = "",
                 artifacts: Optional[dict] = None,
                 started_at: Optional[str] = None,
                 finished_at: Optional[str] = None):
        self.status      = status
        self.output      = output
        self.artifacts   = artifacts if artifacts is not None else {}
        self.started_at  = started_at
        self.finished_at = finished_at

    def to_dict(self) -> dict:
        return {
            "status":      self.status,
            "output":      self.output,
            "artifacts":   self.artifacts,
            "started_at":  self.started_at,
            "finished_at": self.finished_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StageResult":
        return cls(
            status      = d.get("status", StageStatus.PENDING),
            output      = d.get("output", ""),
            artifacts   = d.get("artifacts") or {},
            started_at  = d.get("started_at"),
            finished_at = d.get("finished_at"),
        )


class Task:
    """Задача с явной машиной состояний и персистентностью.

    Шаг 1: только структура данных + сериализация. Логика переходов,
    драйвер стадий, парсинг вопросов и UI добавляются на следующих шагах.
    """

    def __init__(self,
                 id: str,
                 title: str,
                 request: str,
                 state: str = TaskState.INTAKE,
                 stages: Optional[dict] = None,
                 context: Optional[dict] = None,
                 pending_questions: Optional[list] = None,
                 answers: Optional[list] = None,
                 awaiting: Optional[str] = None,
                 profile_snapshot: Optional[str] = None,
                 model_snapshot: Optional[str] = None,
                 created_at: Optional[str] = None,
                 updated_at: Optional[str] = None,
                 transitions: Optional[list] = None):
        self.id                = id
        self.title             = title
        self.request           = request
        self.state             = state
        self.stages            = stages if stages is not None else {}
        self.context           = context if context is not None else {}
        self.pending_questions = pending_questions if pending_questions is not None else []
        self.answers           = answers if answers is not None else []
        self.awaiting          = awaiting
        self.profile_snapshot  = profile_snapshot
        self.model_snapshot    = model_snapshot
        self.created_at        = created_at
        self.updated_at        = updated_at
        self.transitions       = transitions if transitions is not None else []

    # ── factory ──────────────────────────────────────────────────────────────

    @classmethod
    def new(cls,
            request: str,
            profile: Optional[str] = None,
            model: Optional[str] = None) -> "Task":
        now   = time.strftime("%Y-%m-%d %H:%M")
        title = request.strip().split("\n", 1)[0][:60] or "—"
        return cls(
            id               = uuid.uuid4().hex[:8],
            title            = title,
            request          = request,
            state            = TaskState.INTAKE,
            profile_snapshot = profile,
            model_snapshot   = model,
            created_at       = now,
            updated_at       = now,
        )

    # ── state machine ────────────────────────────────────────────────────────

    def can_transition(self, new_state: str) -> bool:
        return new_state in _ALLOWED_TRANSITIONS.get(self.state, set())

    def transition(self, new_state: str, reason: str = "", save: bool = True):
        """Перевести задачу в новое состояние.

        Запись в `transitions` идёт всегда, save() вызывается по умолчанию,
        чтобы переживать падения между шагами. Передай save=False, если
        хочешь сгруппировать несколько изменений и сохранить один раз.
        """
        if new_state not in TaskState.ALL:
            raise TaskTransitionError(f"Неизвестное состояние: {new_state!r}")
        if not self.can_transition(new_state):
            allowed = sorted(_ALLOWED_TRANSITIONS.get(self.state, set()))
            raise TaskTransitionError(
                f"Запрещённый переход: {self.state} → {new_state}. "
                f"Разрешено из {self.state}: {allowed or 'ничего (терминальное состояние)'}"
            )
        self.transitions.append({
            "from":   self.state,
            "to":     new_state,
            "at":     time.strftime("%Y-%m-%d %H:%M:%S"),
            "reason": reason,
        })
        self.state = new_state
        if save:
            self.save()

    def is_terminal(self) -> bool:
        return self.state in TaskState.TERMINAL

    # ── serialization ────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "id":                self.id,
            "title":             self.title,
            "request":           self.request,
            "state":             self.state,
            "stages":            {k: v.to_dict() for k, v in self.stages.items()},
            "context":           self.context,
            "pending_questions": self.pending_questions,
            "answers":           self.answers,
            "awaiting":          self.awaiting,
            "profile_snapshot":  self.profile_snapshot,
            "model_snapshot":    self.model_snapshot,
            "created_at":        self.created_at,
            "updated_at":        self.updated_at,
            "transitions":       self.transitions,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        raw_stages = d.get("stages") or {}
        stages = {k: StageResult.from_dict(v) for k, v in raw_stages.items()}
        return cls(
            id                = d["id"],
            title             = d.get("title", "—"),
            request           = d.get("request", ""),
            state             = d.get("state", TaskState.INTAKE),
            stages            = stages,
            context           = d.get("context") or {},
            pending_questions = d.get("pending_questions") or [],
            answers           = d.get("answers") or [],
            awaiting          = d.get("awaiting"),
            profile_snapshot  = d.get("profile_snapshot"),
            model_snapshot    = d.get("model_snapshot"),
            created_at        = d.get("created_at"),
            updated_at        = d.get("updated_at"),
            transitions       = d.get("transitions") or [],
        )

    # ── persistence ──────────────────────────────────────────────────────────

    @staticmethod
    def _path(task_id: str) -> str:
        return os.path.join(TASKS_DIR, f"{task_id}.json")

    def save(self):
        os.makedirs(TASKS_DIR, exist_ok=True)
        self.updated_at = time.strftime("%Y-%m-%d %H:%M")
        with open(self._path(self.id), "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, task_id: str) -> Optional["Task"]:
        path = cls._path(task_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                return cls.from_dict(json.load(f))
        except Exception:
            return None

    @classmethod
    def list_all(cls) -> list:
        if not os.path.isdir(TASKS_DIR):
            return []
        tasks = []
        for fname in os.listdir(TASKS_DIR):
            if not fname.endswith(".json"):
                continue
            t = cls.load(os.path.splitext(fname)[0])
            if t is not None:
                tasks.append(t)
        tasks.sort(key=lambda t: t.updated_at or "", reverse=True)
        return tasks

    def delete(self):
        path = self._path(self.id)
        if os.path.exists(path):
            os.remove(path)
        if Task.get_active_id() == self.id:
            Task.clear_active()

    # ── active task pointer ──────────────────────────────────────────────────

    def set_active(self):
        os.makedirs(TASKS_DIR, exist_ok=True)
        with open(ACTIVE_TASK_FILE, "w", encoding="utf-8") as f:
            f.write(self.id)

    @staticmethod
    def get_active_id() -> Optional[str]:
        if not os.path.exists(ACTIVE_TASK_FILE):
            return None
        try:
            with open(ACTIVE_TASK_FILE, encoding="utf-8") as f:
                tid = f.read().strip()
            return tid or None
        except Exception:
            return None

    @classmethod
    def get_active(cls) -> Optional["Task"]:
        tid = cls.get_active_id()
        if not tid:
            return None
        return cls.load(tid)

    @staticmethod
    def clear_active():
        if os.path.exists(ACTIVE_TASK_FILE):
            os.remove(ACTIVE_TASK_FILE)


# ── Stage prompts + driver ────────────────────────────────────────────────────
QUESTION_PROTOCOL = (
    "ПРАВИЛО УТОЧНЕНИЙ: если на любом этапе тебе не хватает данных, чтобы продолжать "
    "уверенно — задай уточняющий вопрос пользователю. Каждый такой вопрос ОБЯЗАТЕЛЬНО "
    "оформи отдельной строкой ровно в формате:\n"
    "[QUESTION] <текст вопроса>\n"
    "Можно несколько подряд. Если вопросов нет — НЕ используй этот тег вовсе."
)

STAGE_PROMPTS = {
    TaskState.INTAKE: (
        "Сейчас стадия INTAKE — сбор и уточнение задачи.\n"
        "Твоя цель: убедиться, что задача понятна полностью.\n"
        "Если есть неясности — задай уточняющие вопросы (см. ПРАВИЛО УТОЧНЕНИЙ).\n"
        "Если всё ясно — сформулируй задачу одним абзацем и скажи, что готов перейти к планированию. "
        "В этом случае НЕ задавай вопросов."
    ),
    TaskState.PLANNING: (
        "Сейчас стадия PLANNING — составление плана.\n"
        "На основе уточнённой задачи составь подробный пошаговый план: пронумерованные пункты, "
        "у каждого — что именно будет сделано и какой ожидаемый результат.\n"
        "Если для составления плана не хватает данных — сначала задай уточняющие вопросы "
        "(см. ПРАВИЛО УТОЧНЕНИЙ) и НЕ выводи план в этом ответе.\n"
        "Когда план готов — в самом конце ответа ОБЯЗАТЕЛЬНО спроси дословно: «Утвердить план? [y/n]»"
    ),
    TaskState.EXECUTION: (
        "Сейчас стадия EXECUTION — выполнение утверждённого плана.\n"
        "Выполняй пункты плана по порядку. По каждому пункту опиши, что сделано и какой получился результат.\n"
        "Если по ходу выяснилось, что план неверен — явно скажи об этом, не продолжай вслепую.\n"
        "Если нужны данные от пользователя — задай уточняющий вопрос (см. ПРАВИЛО УТОЧНЕНИЙ)."
    ),
    TaskState.VALIDATION: (
        "Сейчас стадия VALIDATION — проверка результата.\n"
        "Сверь сделанное с утверждённым планом и исходным запросом.\n"
        "Перечисли найденные проблемы конкретными пунктами; если проблем нет — скажи прямо, что всё в порядке.\n"
        "Если для проверки не хватает данных — задай уточняющий вопрос (см. ПРАВИЛО УТОЧНЕНИЙ)."
    ),
}

# Маркер вопроса: строка, начинающаяся с [QUESTION]. Захватываем всё до следующего
# такого тега или до конца текста, поддерживая многострочные формулировки.
_QUESTION_RE = re.compile(r"^\s*\[QUESTION\]\s*(.+?)(?=^\s*\[QUESTION\]|\Z)",
                          re.MULTILINE | re.DOTALL)


def parse_questions(text: str) -> list:
    """Извлекает вопросы агента из ответа модели."""
    return [m.strip() for m in _QUESTION_RE.findall(text) if m.strip()]

# Порядок стадий «вперёд» — нужен для построения контекста и команды /task advance.
_STAGE_ORDER = [
    TaskState.INTAKE,
    TaskState.PLANNING,
    TaskState.EXECUTION,
    TaskState.VALIDATION,
    TaskState.DONE,
]


def build_task_block(task: Task, restoration_hint: bool = False) -> str:
    """Блок [ЗАДАЧА] для system prompt: контекст, прошлые стадии, инструкция для текущей.

    Если restoration_hint=True — добавляется блок «возобновление после перерыва»,
    просящий модель кратко напомнить пользователю, на чём остановились.
    """
    lines = [f"[ЗАДАЧА #{task.id}: {task.title}]"]
    lines.append(f"Исходный запрос пользователя: {task.request}")
    lines.append(f"Текущая стадия: {task.state}")

    if task.context:
        lines.append("Контекст задачи:")
        for k, v in task.context.items():
            lines.append(f"  {k}: {v}")

    # результаты прошлых стадий (всё, что строго до текущей)
    for s in _STAGE_ORDER:
        if s == task.state:
            break
        result = task.stages.get(s)
        if result and result.output:
            lines.append(f"\n--- Результат стадии {s} ---\n{result.output}")

    if task.answers:
        lines.append("\n--- Уточнения от пользователя ---")
        for a in task.answers:
            lines.append(f"Q: {a.get('q','')}\nA: {a.get('a','')}")

    instr = STAGE_PROMPTS.get(task.state)
    if instr:
        lines.append(f"\n--- Инструкция для текущей стадии ({task.state}) ---\n{instr}")

    lines.append(f"\n--- Протокол уточнений ---\n{QUESTION_PROTOCOL}")

    if restoration_hint:
        lines.append(
            "\n--- Возобновление после перерыва ---\n"
            "Работа над этой задачей была прервана (закрыт чат / истекло время / "
            "пропала связь) и сейчас возобновлена. Прежде чем продолжать стадию, "
            "начни ответ с краткого (1–2 предложения) напоминания, на чём именно "
            "остановились — чтобы пользователь быстро восстановил контекст. "
            "Потом продолжай как обычно."
        )

    return "\n".join(lines)


def advance_task(task: Task,
                 user_input: str,
                 params: dict,
                 profile_text: Optional[str],
                 wm: "WorkingMemory",
                 restoration_hint: bool = False) -> str:
    """Один прогон текущей стадии через модель.

    Если у задачи есть незакрытые вопросы (pending_questions), user_input
    трактуется как ответ на них: пишется в task.answers, очищается список
    вопросов, и только после этого зовётся модель. Если модель в новом ответе
    задаёт новые [QUESTION] — стадия остаётся awaiting_user; иначе — done.
    """
    if task.is_terminal():
        raise RuntimeError(f"Задача в терминальном состоянии: {task.state}")
    if task.state not in STAGE_PROMPTS:
        raise RuntimeError(f"Для стадии {task.state} нет промпта")

    # Если уже ждём чего-то отличного от уточнения (например, plan_approval) —
    # на это есть отдельный обработчик; этот код такой ввод не трогает.
    if task.awaiting and task.awaiting != "clarification":
        raise RuntimeError(f"Задача ожидает {task.awaiting}, а не свободного ответа")

    # 1) Если ждали ответа на уточнения — фиксируем его в answers.
    if task.pending_questions and user_input:
        task.answers.append({
            "kind":  "clarification",
            "stage": task.state,
            "q":     "\n".join(task.pending_questions),
            "a":     user_input,
            "at":    time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        task.pending_questions = []
        task.awaiting = None
        task.save()
        # user_input уже впитан в answers и попадёт в task_block — не дублируем
        # его как отдельное user-message, чтобы модель не отвечала на ответ как
        # на новый вопрос.
        followup_message = ""
    else:
        followup_message = user_input

    stage_obj = task.stages.get(task.state) or StageResult()
    if stage_obj.started_at is None:
        stage_obj.started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    stage_obj.status = StageStatus.IN_PROGRESS
    task.stages[task.state] = stage_obj
    task.save()

    base = build_system_prompt(profile_text, wm) or ""
    task_block = build_task_block(task, restoration_hint=restoration_hint)
    system_prompt = (base + "\n\n" + task_block) if base else task_block

    stage_messages = []
    if followup_message:
        stage_messages.append({"role": "user", "content": followup_message})

    try:
        reply = chat(stage_messages, params, system_prompt)
    except Exception:
        stage_obj.status = StageStatus.FAILED
        task.save()
        raise

    # Накапливаем вывод стадии (важно при многошаговых итерациях).
    stage_obj.output = (stage_obj.output + "\n\n" + reply).strip() if stage_obj.output else reply

    # Разбираем reply: если в нём есть [QUESTION] — ставим awaiting_user.
    questions = parse_questions(reply)
    if questions:
        task.pending_questions = questions
        task.awaiting = "clarification"
        stage_obj.status = StageStatus.AWAITING_USER
    elif task.state == TaskState.PLANNING:
        # План готов — стадия не закрывается, ждём явного y/n от пользователя.
        # finished_at выставится позже, в handle_plan_approval после "y".
        task.pending_questions = []
        task.awaiting = "plan_approval"
        stage_obj.status = StageStatus.AWAITING_USER
    else:
        task.pending_questions = []
        task.awaiting = None
        stage_obj.status = StageStatus.DONE
        stage_obj.finished_at = time.strftime("%Y-%m-%d %H:%M:%S")

    task.stages[task.state] = stage_obj
    task.save()
    return reply


def _next_forward_state(state: str) -> Optional[str]:
    """Следующая стадия по линейному порядку (для /task advance)."""
    try:
        i = _STAGE_ORDER.index(state)
    except ValueError:
        return None
    return _STAGE_ORDER[i + 1] if i + 1 < len(_STAGE_ORDER) else None


# Результаты handle_plan_approval — простые строковые константы.
PLAN_APPROVAL_APPROVED = "approved"
PLAN_APPROVAL_REJECTED = "rejected"
PLAN_APPROVAL_RETRY    = "retry"

_YES = {"y", "yes", "да", "д"}
_NO  = {"n", "no", "нет", "н"}


def handle_plan_approval(task: Task, user_input: str) -> str:
    """Обработать y/n на запрос утверждения плана.

    Только мутирует task. UI и запуск execution делает main(), чтобы здесь
    не было зависимости от chat()/Spinner.
    """
    if task.awaiting != "plan_approval":
        raise RuntimeError(f"Задача не ждёт plan_approval (awaiting={task.awaiting!r})")
    ans = user_input.strip().lower()
    if ans in _YES:
        st = task.stages.get(TaskState.PLANNING)
        if st is not None:
            st.status      = StageStatus.DONE
            st.finished_at = time.strftime("%Y-%m-%d %H:%M:%S")
            task.stages[TaskState.PLANNING] = st
        task.awaiting = None
        task.transition(TaskState.EXECUTION, reason="план утверждён пользователем")
        return PLAN_APPROVAL_APPROVED
    if ans in _NO:
        task.awaiting = "plan_revision_input"
        task.save()
        return PLAN_APPROVAL_REJECTED
    return PLAN_APPROVAL_RETRY


def handle_plan_revision(task: Task, user_input: str) -> None:
    """Зафиксировать правки от пользователя и подготовить planning к перегенерации.

    Старый план уходит в stages[planning].artifacts["revisions"], output чистится,
    статус сбрасывается в pending — следующий advance_task стартует «с нуля» и
    напишет новый план целиком. Пожелания пользователя сохраняются в answers
    с kind="plan_revision" и попадают в task_block следующего вызова.
    """
    if task.awaiting != "plan_revision_input":
        raise RuntimeError(f"Задача не ждёт plan_revision_input (awaiting={task.awaiting!r})")
    if not user_input.strip():
        raise RuntimeError("Пустой ответ — нечего править")

    now = time.strftime("%Y-%m-%d %H:%M:%S")
    st  = task.stages.get(TaskState.PLANNING)
    if st is not None and st.output:
        revisions = st.artifacts.setdefault("revisions", [])
        revisions.append({"output": st.output, "at": now})
        st.output       = ""
        st.status       = StageStatus.PENDING
        st.started_at   = None
        st.finished_at  = None
        task.stages[TaskState.PLANNING] = st

    task.answers.append({
        "kind":  "plan_revision",
        "stage": TaskState.PLANNING,
        "q":     "Что нужно поправить в плане?",
        "a":     user_input,
        "at":    now,
    })
    task.awaiting = None
    task.save()


def show_task(task: Task):
    print(f"\n{BOLD}{MAGENTA}Задача #{task.id}:{RESET} {task.title}")
    print(f"  {DIM}запрос:{RESET} {task.request}")
    print(f"  {BOLD}стадия:{RESET} {task.state}")
    if task.awaiting:
        print(f"  {YELLOW}ожидание ввода:{RESET} {task.awaiting}")
    if task.pending_questions:
        print(f"  {YELLOW}незакрытые вопросы:{RESET}")
        for q in task.pending_questions:
            print(f"    • {q}")
    if task.stages:
        print(f"  {BOLD}стадии:{RESET}")
        for s in _STAGE_ORDER:
            r = task.stages.get(s)
            if r is None:
                continue
            mark = "◀" if s == task.state else " "
            print(f"    {mark} {s}: {r.status}")
    if task.transitions:
        last = task.transitions[-1]
        print(f"  {DIM}последний переход: {last['from']} → {last['to']} ({last.get('reason','')}){RESET}")
    current = task.stages.get(task.state)
    if current and current.output:
        print(f"\n  {BOLD}текущий результат:{RESET}\n{current.output}")
    print()


def handle_task(cmd_str: str,
                params: dict,
                profile_text: Optional[str],
                wm: "WorkingMemory") -> None:
    """Обработка /task <sub> ..."""
    parts = cmd_str.split(None, 2)
    sub = parts[1].lower() if len(parts) > 1 else "show"

    if sub == "new":
        request = parts[2].strip() if len(parts) > 2 else ""
        if not request:
            try:
                request = input("  Опиши задачу: ").strip()
            except (EOFError, KeyboardInterrupt):
                return
        if not request:
            print(f"{YELLOW}  Пустой запрос — задача не создана.{RESET}")
            return
        active = Task.get_active()
        if active and not active.is_terminal():
            print(f"{YELLOW}  Уже есть активная задача #{active.id} ({active.state}). "
                  f"Сначала /task abort или /task done.{RESET}")
            return
        task = Task.new(
            request,
            profile=profile_name(_current_profile_path) if _current_profile_path else None,
            model=params["model"],
        )
        task.save()
        task.set_active()
        print(f"{GREEN}  Создана задача #{task.id} (стадия: {task.state}).{RESET}")
        try:
            with Spinner("Думаю..."):
                reply = advance_task(task, request, params, profile_text, wm)
        except Exception as e:
            print(f"{YELLOW}  Ошибка стадии: {e}{RESET}")
            return
        print(f"\n{BOLD}{GREEN}Agent:{RESET} {reply}\n")
        return

    if sub in ("show", ""):
        task = Task.get_active()
        if not task:
            print(f"{DIM}  Активной задачи нет.{RESET}")
            return
        show_task(task)
        return

    if sub == "list":
        tasks = Task.list_all()
        if not tasks:
            print(f"{DIM}  Задач нет.{RESET}")
            return
        active_id = Task.get_active_id()
        print(f"\n{BOLD}Задачи:{RESET}")
        for t in tasks:
            mark = f" {YELLOW}◀ активная{RESET}" if t.id == active_id else ""
            print(f"  {CYAN}#{t.id}{RESET}  {t.state:10}  {t.title}{mark}")
        print()
        return

    if sub == "resume":
        tid = parts[2].strip() if len(parts) > 2 else ""
        if not tid:
            print(f"{YELLOW}  Использование: /task resume <id>{RESET}")
            return
        t = Task.load(tid)
        if not t:
            print(f"{YELLOW}  Задача #{tid} не найдена.{RESET}")
            return
        t.set_active()
        print(f"{GREEN}  Активной выбрана #{t.id} (стадия: {t.state}).{RESET}")
        show_task(t)
        return

    if sub == "advance":
        task = Task.get_active()
        if not task:
            print(f"{YELLOW}  Активной задачи нет.{RESET}")
            return
        nxt = _next_forward_state(task.state)
        if not nxt:
            print(f"{YELLOW}  Из {task.state} вперёд идти некуда.{RESET}")
            return
        reason = parts[2].strip() if len(parts) > 2 else "ручной переход вперёд"
        try:
            task.transition(nxt, reason=reason)
        except TaskTransitionError as e:
            print(f"{YELLOW}  {e}{RESET}")
            return
        print(f"{GREEN}  Стадия: {task.state}.{RESET}")
        return

    if sub == "back":
        task = Task.get_active()
        if not task:
            print(f"{YELLOW}  Активной задачи нет.{RESET}")
            return
        target = parts[2].strip() if len(parts) > 2 else ""
        if not target:
            print(f"{YELLOW}  Использование: /task back <стадия>{RESET}")
            return
        try:
            task.transition(target, reason="ручной откат")
        except TaskTransitionError as e:
            print(f"{YELLOW}  {e}{RESET}")
            return
        print(f"{GREEN}  Стадия: {task.state}.{RESET}")
        return

    if sub == "abort":
        task = Task.get_active()
        if not task:
            print(f"{YELLOW}  Активной задачи нет.{RESET}")
            return
        reason = parts[2].strip() if len(parts) > 2 else "пользователь отменил"
        try:
            task.transition(TaskState.ABORTED, reason=reason)
        except TaskTransitionError as e:
            print(f"{YELLOW}  {e}{RESET}")
            return
        Task.clear_active()
        print(f"{DIM}  Задача #{task.id} отменена.{RESET}")
        return

    if sub == "done":
        task = Task.get_active()
        if not task:
            print(f"{YELLOW}  Активной задачи нет.{RESET}")
            return
        try:
            task.transition(TaskState.DONE, reason="вручную завершено")
        except TaskTransitionError as e:
            print(f"{YELLOW}  {e}{RESET}")
            return
        Task.clear_active()
        print(f"{GREEN}  Задача #{task.id} завершена.{RESET}")
        return

    print(f"{YELLOW}  Подкоманды /task: new · show · list · resume · advance · back · abort · done{RESET}")


# ══════════════════════════════════════════════════════════════════════════════
# Построение system prompt из всех слоёв памяти
# ══════════════════════════════════════════════════════════════════════════════
def build_system_prompt(profile_text: Optional[str], wm: WorkingMemory) -> Optional[str]:
    """
    Долговременная память (профиль + знания) + рабочая память → system prompt.
    Краткосрочная (messages) передаётся отдельно как история диалога.
    """
    parts = []

    if profile_text:
        parts.append(f"[ДОЛГОВРЕМЕННАЯ ПАМЯТЬ — Профиль]\n{profile_text}")

    knowledge = load_all_knowledge()
    if knowledge:
        parts.append(f"[ДОЛГОВРЕМЕННАЯ ПАМЯТЬ — База знаний]\n{knowledge}")

    wm_text = wm.to_prompt()
    if wm_text:
        parts.append(wm_text)

    return "\n\n".join(parts) if parts else None

# ── Token cache ───────────────────────────────────────────────────────────────
_token = None
_token_expires_at: float = 0.0
TOKEN_EXPIRY_BUFFER = 60

def get_token() -> str:
    global _token, _token_expires_at
    if _token and time.time() < _token_expires_at - TOKEN_EXPIRY_BUFFER:
        return _token
    resp = requests.post(
        OAUTH_URL,
        headers={
            "Authorization": f"Basic {AUTH_KEY}",
            "RqUID": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"scope": SCOPE},
        verify=False,
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    _token = data["access_token"]
    _token_expires_at = data["expires_at"] / 1000
    return _token

# ── Chat ──────────────────────────────────────────────────────────────────────
def chat(messages: list, params: dict, system_prompt: Optional[str] = None) -> str:
    token = get_token()
    api_messages = messages
    if system_prompt:
        api_messages = [{"role": "system", "content": system_prompt}] + messages
    body = {"model": params["model"], "messages": api_messages}
    if params["temperature"] is not None:
        body["temperature"] = params["temperature"]
    if params["max_tokens"] is not None:
        body["max_tokens"] = params["max_tokens"]

    resp = requests.post(
        CHAT_URL,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        verify=False,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]

# ── UI helpers ────────────────────────────────────────────────────────────────
def print_settings(params: dict):
    temp  = params["temperature"] if params["temperature"] is not None else "auto"
    maxt  = params["max_tokens"]  if params["max_tokens"]  is not None else "auto"
    pname = profile_name(_current_profile_path) if _current_profile_path else "нет"
    print(f"{DIM}  модель: {params['model']}  temperature: {temp}  max_tokens: {maxt}  профиль: {pname}{RESET}")

def print_memory_status(messages: list, wm: WorkingMemory):
    """Однострочный дашборд трёх слоёв памяти."""
    # краткосрочная
    st_label = f"{GREEN}краткосрочная: {len(messages)} сообщ.{RESET}" if messages \
               else f"{DIM}краткосрочная: —{RESET}"
    # рабочая
    wm_label = wm.status_badge()
    # долговременная
    pname  = profile_name(_current_profile_path) if _current_profile_path else "нет"
    kfiles = list_knowledge()
    lt_label = f"{BLUE}долговременная: {pname}"
    if kfiles:
        lt_label += f", {len(kfiles)} знаний"
    lt_label += RESET

    print(f"  {st_label}  │  {wm_label}  │  {lt_label}")

def print_mem_detail(messages: list, wm: WorkingMemory):
    """Подробный вывод всех трёх слоёв."""
    print(f"\n{BOLD}═══ Модель памяти ═══{RESET}\n")

    # Слой 1
    print(f"{BOLD}{GREEN}[1] Краткосрочная память{RESET}  {DIM}(текущий диалог){RESET}")
    if messages:
        print(f"    {len(messages)} сообщений в текущей сессии")
        if _current_session_file:
            print(f"    {DIM}файл: {_current_session_file}{RESET}")
    else:
        print(f"    {DIM}пусто (новая сессия){RESET}")
    total = len(_list_session_files())
    if total:
        print(f"    {DIM}всего сохранённых сессий: {total}{RESET}")

    # Слой 2
    print(f"\n{BOLD}{MAGENTA}[2] Рабочая память{RESET}  {DIM}(задача и контекст){RESET}")
    wm.show()

    # Слой 3
    print(f"\n{BOLD}{BLUE}[3] Долговременная память{RESET}  {DIM}(профиль + знания){RESET}")
    pname = profile_name(_current_profile_path) if _current_profile_path else "нет"
    print(f"    Профиль: {pname}")
    kfiles = list_knowledge()
    if kfiles:
        print(f"    База знаний ({len(kfiles)} записей):")
        for fname in kfiles:
            print(f"      {BLUE}•{RESET} {os.path.splitext(fname)[0]}")
    else:
        print(f"    {DIM}База знаний пуста. Используй /know save{RESET}")
    print()

def choose_model(params: dict):
    print(f"\n{BOLD}Выберите модель:{RESET}")
    for k, (mid, label) in MODELS.items():
        marker = " ◀" if mid == params["model"] else ""
        print(f"  {k}. {label}{marker}")
    choice = input("Номер (Enter — оставить текущую): ").strip()
    if choice in MODELS:
        params["model"] = MODELS[choice][0]
        print(f"{GREEN}Модель: {params['model']}{RESET}")

def set_temperature(params: dict):
    val = input("temperature (0.0–2.0, Enter — auto): ").strip()
    if val == "":
        params["temperature"] = None
    else:
        params["temperature"] = float(val)

def set_max_tokens(params: dict):
    val = input("max_tokens (целое число, Enter — auto): ").strip()
    if val == "":
        params["max_tokens"] = None
    else:
        params["max_tokens"] = int(val)

def print_help():
    print(f"""
{BOLD}Чат:{RESET}
  {CYAN}/model{RESET}          — выбрать модель
  {CYAN}/temp{RESET}           — задать temperature
  {CYAN}/tokens{RESET}         — задать max_tokens
  {CYAN}/settings{RESET}       — текущие настройки
  {CYAN}/clear{RESET}          — очистить краткосрочную память (диалог)
  {CYAN}/quit{RESET} / Ctrl+D  — выход

{BOLD}{MAGENTA}Рабочая память (/wm):{RESET}
  {CYAN}/wm{RESET}                      — показать рабочую память
  {CYAN}/wm task <описание>{RESET}      — установить текущую задачу
  {CYAN}/wm set <ключ> <значение>{RESET} — сохранить факт в контекст
  {CYAN}/wm note <текст>{RESET}         — добавить заметку
  {CYAN}/wm del <ключ>{RESET}           — удалить ключ из контекста
  {CYAN}/wm clear{RESET}                — очистить рабочую память

{BOLD}{BLUE}Долговременная память (/know):{RESET}
  {CYAN}/know list{RESET}       — список записей
  {CYAN}/know save <имя>{RESET} — сохранить знание
  {CYAN}/know show <имя>{RESET} — показать запись

{BOLD}Задачи (/task):{RESET}
  {CYAN}/task new <описание>{RESET} — создать задачу и начать стадию intake
  {CYAN}/task{RESET}                — показать активную задачу
  {CYAN}/task list{RESET}           — список всех задач
  {CYAN}/task resume <id>{RESET}    — сделать другую задачу активной
  {CYAN}/task advance{RESET}        — перейти в следующую стадию вперёд
  {CYAN}/task back <стадия>{RESET}  — откатить на указанную стадию
  {CYAN}/task abort{RESET}          — отменить задачу
  {CYAN}/task done{RESET}           — пометить задачу завершённой

{BOLD}Профиль:{RESET}
  {CYAN}/profile{RESET}         — сменить профиль агента
  {CYAN}/profile new{RESET}     — создать новый профиль
  {CYAN}/profile edit{RESET}    — редактировать профиль
  {CYAN}/profile delete{RESET}  — удалить профиль

{BOLD}Обзор:{RESET}
  {CYAN}/mem{RESET}             — показать все слои памяти
  {CYAN}/help{RESET}            — эта справка

{DIM}Что куда сохраняется:
  краткосрочная → текущий диалог (messages), авто
  рабочая       → задача/контекст/заметки, вручную через /wm
  долговременная → профиль + /know save{RESET}
""")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    params   = dict(DEFAULT_PARAMS)
    messages: list = []

    global _current_session_file, _current_profile_path, _current_profile_text

    print(f"\n{BOLD}{GREEN}Jarvis CLI{RESET}  {DIM}(введите /help для справки){RESET}\n")

    if not AUTH_KEY:
        print(f"{YELLOW}Ошибка: GIGACHAT_AUTH_KEY не задан.{RESET}")
        print(f"{DIM}Создайте файл .env рядом с chat.py:{RESET}")
        print(f"{DIM}  GIGACHAT_AUTH_KEY=ваш_ключ{RESET}\n")
        sys.exit(1)

    # Инициализация долговременной памяти
    default_profile = ensure_default_profile()
    _current_profile_path = default_profile
    _current_profile_text = load_profile(default_profile)

    # Инициализация рабочей памяти
    wm = WorkingMemory().load()
    if not wm.is_empty():
        print(f"{MAGENTA}Рабочая память загружена:{RESET}")
        wm.show()
        print()

    # Выбор краткосрочной памяти (сессии)
    sessions = list_sessions()
    if sessions:
        print(f"{BOLD}Выберите сессию:{RESET}")
        for i, s in enumerate(sessions[:9], 1):
            title = s["title"][:50] + ("…" if len(s["title"]) > 50 else "")
            print(f"  {CYAN}{i}{RESET}. {s['updated_at']}  {DIM}{s['model']} · {s['count']} сообщ.{RESET}  {title}")
        print(f"  {CYAN}n{RESET}. Новый чат")
        try:
            choice = input(f"\nВыбор [1–{len(sessions[:9])} или n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            choice = "n"

        if choice.isdigit() and 1 <= int(choice) <= len(sessions[:9]):
            s = sessions[int(choice) - 1]
            messages = s["messages"]
            params.update(s["params"])
            _current_session_file = s["path"]
            print(f"{DIM}Загружено {len(messages)} сообщений.{RESET}\n")

    # Восстановление активной задачи (Слой 4): спрашиваем пользователя, продолжать ли.
    pending_restoration_hint = False
    saved_active = Task.get_active()
    if saved_active and not saved_active.is_terminal():
        print(f"{BOLD}{MAGENTA}Найдена активная задача:{RESET}")
        show_task(saved_active)
        try:
            choice = input(f"Продолжить задачу #{saved_active.id}? [y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            choice = "n"
        if choice in _YES:
            pending_restoration_hint = True
            if saved_active.awaiting == "plan_approval":
                print(f"{DIM}  Задача ждёт утверждения плана — ответь y или n.{RESET}")
            elif saved_active.awaiting == "plan_revision_input":
                print(f"{DIM}  Задача ждёт правок к плану — опиши, что поправить.{RESET}")
            elif saved_active.pending_questions:
                print(f"{DIM}  Задача ждёт ответа на уточняющие вопросы (см. выше).{RESET}")
            print(f"{GREEN}  Возобновляем.{RESET}\n")
        else:
            Task.clear_active()
            print(f"{DIM}  Задача #{saved_active.id} оставлена в /task list (но не активна).{RESET}\n")

    print_settings(params)
    print_memory_status(messages, wm)
    print()

    while True:
        try:
            user_input = input(f"{BOLD}{CYAN}You:{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{DIM}Выход.{RESET}")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            cmd = user_input.lower()

            if cmd in ("/quit", "/exit", "/q"):
                print(f"{DIM}Выход.{RESET}")
                break
            elif cmd == "/model":
                choose_model(params)
            elif cmd == "/temp":
                set_temperature(params)
            elif cmd == "/tokens":
                set_max_tokens(params)
            elif cmd == "/settings":
                print_settings(params)
                print_memory_status(messages, wm)
            elif cmd == "/mem":
                print_mem_detail(messages, wm)
            elif cmd.startswith("/wm"):
                handle_wm(user_input, wm)
            elif cmd.startswith("/know"):
                handle_know(user_input)
            elif cmd.startswith("/task"):
                handle_task(user_input, params, _current_profile_text, wm)
            elif cmd == "/profile new":
                _current_profile_path, _current_profile_text = create_profile()
            elif cmd == "/profile edit":
                _current_profile_path, _current_profile_text = edit_profile()
            elif cmd == "/profile delete":
                _current_profile_path, _current_profile_text = delete_profile()
            elif cmd == "/profile":
                _current_profile_path, _current_profile_text = choose_profile()
            elif cmd == "/clear":
                clear_session(messages)
                print(f"{DIM}Краткосрочная память очищена (диалог).{RESET}")
            elif cmd == "/help":
                print_help()
            else:
                print(f"{YELLOW}Неизвестная команда. Введите /help.{RESET}")
            continue

        # Если есть активная нетерминальная задача — ввод идёт в её драйвер,
        # а не в обычный чат. Сначала проверяем спец-режимы (plan_approval,
        # plan_revision_input), потом обычный clarification/stage цикл.
        active_task = Task.get_active()
        if active_task and not active_task.is_terminal():

            # === шлюз утверждения плана ===
            if active_task.awaiting == "plan_approval":
                result = handle_plan_approval(active_task, user_input)
                if result == PLAN_APPROVAL_RETRY:
                    print(f"{YELLOW}  Ответь «y» (одобрить) или «n» (нужны правки).{RESET}")
                    continue
                if result == PLAN_APPROVAL_REJECTED:
                    print(f"{DIM}  План отклонён.{RESET}")
                    print(f"{BOLD}Что нужно поправить в плане?{RESET}")
                    continue
                # APPROVED → planning закрыт, мы уже в execution, сразу запускаем стадию.
                print(f"{GREEN}  План утверждён. Перехожу к выполнению.{RESET}\n")
                try:
                    with Spinner("Думаю..."):
                        reply = advance_task(active_task, "", params,
                                             _current_profile_text, wm,
                                             restoration_hint=pending_restoration_hint)
                except Exception as e:
                    print(f"{YELLOW}Ошибка: {e}{RESET}")
                    continue
                pending_restoration_hint = False
                print(f"{BOLD}{GREEN}Agent:{RESET} {reply}\n")
                continue

            # === пользователь ответил на «что поправить?» ===
            if active_task.awaiting == "plan_revision_input":
                try:
                    handle_plan_revision(active_task, user_input)
                except RuntimeError as e:
                    print(f"{YELLOW}  {e}{RESET}")
                    continue
                # Сразу перегенерируем план.
                try:
                    with Spinner("Перепланирую..."):
                        reply = advance_task(active_task, "", params,
                                             _current_profile_text, wm,
                                             restoration_hint=pending_restoration_hint)
                except Exception as e:
                    print(f"{YELLOW}Ошибка: {e}{RESET}")
                    continue
                pending_restoration_hint = False
                print(f"{BOLD}{GREEN}Agent:{RESET} {reply}\n")
                continue

            # === обычный режим: stage prompt + (опционально) clarification ===
            try:
                with Spinner("Думаю..."):
                    reply = advance_task(active_task, user_input, params,
                                         _current_profile_text, wm,
                                         restoration_hint=pending_restoration_hint)
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else "?"
                try:
                    detail = e.response.json()
                except Exception:
                    detail = e.response.text if e.response is not None else ""
                print(f"{YELLOW}Ошибка {status}: {detail}{RESET}")
                continue
            except requests.ConnectionError as e:
                print(f"{YELLOW}Нет соединения: {e}{RESET}")
                continue
            except requests.Timeout:
                print(f"{YELLOW}Таймаут — сервер не ответил вовремя{RESET}")
                continue
            except Exception as e:
                print(f"{YELLOW}Ошибка: {e}{RESET}")
                continue
            pending_restoration_hint = False
            print(f"{BOLD}{GREEN}Agent:{RESET} {reply}")
            print()
            continue

        # Краткосрочная память: добавляем сообщение пользователя
        messages.append({"role": "user", "content": user_input})

        # Формируем system prompt из долговременной + рабочей памяти
        system_prompt = build_system_prompt(_current_profile_text, wm)

        try:
            with Spinner("Думаю..."):
                reply = chat(messages, params, system_prompt)
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else "?"
            try:
                detail = e.response.json()
            except Exception:
                detail = e.response.text if e.response is not None else ""
            print(f"{YELLOW}Ошибка {status}: {detail}{RESET}")
            messages.pop()
            continue
        except requests.ConnectionError as e:
            print(f"{YELLOW}Нет соединения: {e}{RESET}")
            messages.pop()
            continue
        except requests.Timeout:
            print(f"{YELLOW}Таймаут — сервер не ответил вовремя{RESET}")
            messages.pop()
            continue
        except Exception as e:
            print(f"{YELLOW}Ошибка: {e}{RESET}")
            messages.pop()
            continue

        print(f"{BOLD}{GREEN}Agent:{RESET} {reply}")

        # Краткосрочная память: сохраняем ответ ассистента
        messages.append({"role": "assistant", "content": reply})
        save_session(messages, params)
        print()

if __name__ == "__main__":
    main()
