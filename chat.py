#!/usr/bin/env python3
import sys
import os
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

{BOLD}Профиль:{RESET}
  {CYAN}/profile{RESET}         — сменить профиль агента
  {CYAN}/profile new{RESET}     — создать новый профиль

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
            elif cmd == "/profile new":
                _current_profile_path, _current_profile_text = create_profile()
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
