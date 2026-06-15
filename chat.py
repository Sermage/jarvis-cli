#!/usr/bin/env python3
import sys
import os
import uuid
import time
import json
import subprocess
import threading
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── Config ────────────────────────────────────────────────────────────────────
def _load_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
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
CHAT_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
SCOPE = "GIGACHAT_API_PERS"

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
    "temperature": None,   # None = не передавать (модель решает)
    "max_tokens":  None,
}

HISTORY_DIR      = os.path.expanduser("~/.jarvis/sessions")
PROFILES_DIR     = os.path.expanduser("~/.jarvis/profiles")
MAX_SESSIONS     = 20

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
        sys.stdout.write("\r\033[K")  # очистить строку со спиннером
        sys.stdout.flush()

# ── Profiles ─────────────────────────────────────────────────────────────────
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
    """Создаёт новый профиль и открывает его в редакторе. Возвращает (path, text)."""
    try:
        name = input("Название профиля (например, android-dev): ").strip()
    except (EOFError, KeyboardInterrupt):
        return _current_profile_path, _current_profile_text

    if not name:
        print(f"{YELLOW}Название не может быть пустым.{RESET}")
        return _current_profile_path, _current_profile_text

    # Убираем пробелы и спецсимволы из имени файла
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
    """Возвращает (path, text) выбранного профиля."""
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

# ── Token cache ───────────────────────────────────────────────────────────────
_token = None
_token_expires_at: float = 0.0
TOKEN_EXPIRY_BUFFER = 60  # seconds

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
    _token_expires_at = data["expires_at"] / 1000  # ms → s
    return _token

# ── Chat ──────────────────────────────────────────────────────────────────────
def chat(messages: list, params: dict, system_prompt: str = None) -> str:
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

# ── History ───────────────────────────────────────────────────────────────────
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

# ── UI helpers ────────────────────────────────────────────────────────────────
def print_settings(params: dict):
    temp = params["temperature"] if params["temperature"] is not None else "auto"
    maxt = params["max_tokens"]  if params["max_tokens"]  is not None else "auto"
    pname = profile_name(_current_profile_path) if _current_profile_path else "нет"
    print(f"{DIM}  модель: {params['model']}  temperature: {temp}  max_tokens: {maxt}  профиль: {pname}{RESET}")

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
{BOLD}Команды:{RESET}
  {CYAN}/model{RESET}       — выбрать модель
  {CYAN}/temp{RESET}        — задать temperature
  {CYAN}/tokens{RESET}      — задать max_tokens
  {CYAN}/profile{RESET}     — сменить профиль агента
  {CYAN}/profile new{RESET} — создать новый профиль
  {CYAN}/settings{RESET}    — показать текущие настройки
  {CYAN}/clear{RESET}       — очистить историю чата и файл сессии
  {CYAN}/help{RESET}        — эта справка
  {CYAN}/quit{RESET} или {CYAN}Ctrl+D{RESET}  — выход (история сохраняется)

{DIM}Профили хранятся в: {PROFILES_DIR}{RESET}
""")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    params = dict(DEFAULT_PARAMS)
    messages: list = []

    global _current_session_file, _current_profile_path, _current_profile_text

    print(f"\n{BOLD}{GREEN}Jarvis CLI{RESET}  {DIM}(введите /help для справки){RESET}\n")

    if not AUTH_KEY:
        print(f"{YELLOW}Ошибка: GIGACHAT_AUTH_KEY не задан.{RESET}")
        print(f"{DIM}Создайте файл .env рядом с chat.py:{RESET}")
        print(f"{DIM}  GIGACHAT_AUTH_KEY=ваш_ключ{RESET}\n")
        sys.exit(1)

    # Загрузка профиля
    default_profile = ensure_default_profile()
    _current_profile_path = default_profile
    _current_profile_text = load_profile(default_profile)

    sessions = list_sessions()
    if sessions:
        print(f"{BOLD}Выберите сессию:{RESET}")
        for i, s in enumerate(sessions[:9], 1):
            title = s["title"][:50] + ("…" if len(s["title"]) > 50 else "")
            print(f"  {CYAN}{i}{RESET}. {s['updated_at']}  {DIM}{s['model']} · {s['count']} сообщ.{RESET}  {title}")
        print(f"  {CYAN}n{RESET}. Новый чат")
        try:
            choice = input(f"\nВыбор [1/{len(sessions[:9])} или n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            choice = "n"

        if choice.isdigit() and 1 <= int(choice) <= len(sessions[:9]):
            s = sessions[int(choice) - 1]
            messages = s["messages"]
            params.update(s["params"])
            _current_session_file = s["path"]
            print(f"{DIM}Загружено {len(messages)} сообщений.{RESET}\n")

    print_settings(params)
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
            elif cmd == "/profile new":
                _current_profile_path, _current_profile_text = create_profile()
            elif cmd == "/profile":
                _current_profile_path, _current_profile_text = choose_profile()
            elif cmd == "/clear":
                clear_session(messages)
                print(f"{DIM}Сессия очищена.{RESET}")
            elif cmd == "/help":
                print_help()
            else:
                print(f"{YELLOW}Неизвестная команда. Введите /help.{RESET}")
            continue

        messages.append({"role": "user", "content": user_input})

        try:
            with Spinner("Думаю..."):
                reply = chat(messages, params, _current_profile_text)
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
        messages.append({"role": "assistant", "content": reply})
        save_session(messages, params)
        print()

if __name__ == "__main__":
    main()
