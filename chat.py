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

from domain.task import (
    TaskState,
    TaskTransitionError,
    StageStatus,
    StageResult,
    Task as _DomainTask,
)
from domain.working_memory import WorkingMemory as _DomainWorkingMemory
from domain.profile import (
    DEFAULT_PROFILE_CONTENT,
    PROFILE_TEMPLATE,
    sanitize_profile_name,
)
from domain.knowledge import sanitize_knowledge_name
from infra.working_memory_repository import FileWorkingMemoryRepository
from infra.session_repository import FileSessionRepository
from infra.gigachat_client import RequestsGigaChatClient
from infra.task_repository import FileTaskRepository
from infra.profile_repository import FileProfileRepository
from infra.knowledge_repository import FileKnowledgeRepository
from app.ports import (
    GigaChatClient,
    KnowledgeRepository,
    ProfileRepository,
    TaskRepository,
)
from domain.profile import Profile
from domain.knowledge import KnowledgeEntry
from app.parsers import parse_questions, parse_validation_verdict
from app.stage_prompts import (
    STAGE_PROMPTS,
    STAGE_ORDER,
    build_task_block,
    next_forward_state,
)
from app.system_prompt import build_system_prompt
from app.task_driver import (
    PLAN_APPROVAL_APPROVED,
    PLAN_APPROVAL_REJECTED,
    PLAN_APPROVAL_RETRY,
    advance_task,
    handle_plan_approval,
    handle_plan_revision,
)

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
# Хранилища — infra.profile_repository.FileProfileRepository и
# infra.knowledge_repository.FileKnowledgeRepository.
# Активный профиль живёт как локальная переменная current_profile в main().

def open_in_editor(path: str):
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "nano"
    subprocess.call([editor, path])


def create_profile(repo: ProfileRepository,
                   current: Optional[Profile]) -> Optional[Profile]:
    try:
        raw = input("Название профиля (например, android-dev): ").strip()
    except (EOFError, KeyboardInterrupt):
        return current
    if not raw:
        print(f"{YELLOW}Название не может быть пустым.{RESET}")
        return current

    name = sanitize_profile_name(raw)
    if repo.exists(name):
        print(f"{YELLOW}Профиль «{name}» уже существует.{RESET}")
    else:
        repo.save(Profile.from_template(raw))

    print(f"{DIM}Открываю редактор…{RESET}")
    open_in_editor(repo.path_for(name))

    loaded = repo.load(name)
    print(f"{GREEN}Профиль «{name}» загружен.{RESET}")
    return loaded


def edit_profile(repo: ProfileRepository,
                 current: Optional[Profile]) -> Optional[Profile]:
    names = repo.list_names()
    if not names:
        print(f"{YELLOW}Нет профилей для редактирования.{RESET}")
        return current

    print(f"\n{BOLD}Выберите профиль для редактирования:{RESET}")
    for i, n in enumerate(names, 1):
        marker = " ◀" if current and n == current.name else ""
        print(f"  {CYAN}{i}{RESET}. {n}{marker}")
    try:
        choice = input("Номер (Enter — отмена): ").strip()
    except (EOFError, KeyboardInterrupt):
        return current
    if not (choice.isdigit() and 1 <= int(choice) <= len(names)):
        return current

    target = names[int(choice) - 1]
    print(f"{DIM}Открываю редактор…{RESET}")
    open_in_editor(repo.path_for(target))

    if current and target == current.name:
        loaded = repo.load(target)
        print(f"{GREEN}Профиль «{target}» обновлён и перезагружен.{RESET}")
        return loaded
    print(f"{GREEN}Профиль «{target}» сохранён.{RESET}")
    return current


def delete_profile(repo: ProfileRepository,
                   current: Optional[Profile]) -> Optional[Profile]:
    names = repo.list_names()
    if not names:
        print(f"{YELLOW}Нет профилей для удаления.{RESET}")
        return current

    print(f"\n{BOLD}Выберите профиль для удаления:{RESET}")
    for i, n in enumerate(names, 1):
        marker = f" {YELLOW}◀ активный{RESET}" if current and n == current.name else ""
        print(f"  {CYAN}{i}{RESET}. {n}{marker}")
    try:
        choice = input("Номер (Enter — отмена): ").strip()
    except (EOFError, KeyboardInterrupt):
        return current
    if not (choice.isdigit() and 1 <= int(choice) <= len(names)):
        return current

    target = names[int(choice) - 1]
    try:
        confirm = input(f"Удалить «{target}»? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return current
    if confirm != "y":
        print(f"{DIM}Отменено.{RESET}")
        return current

    repo.delete(target)
    print(f"{GREEN}Профиль «{target}» удалён.{RESET}")
    if current and target == current.name:
        print(f"{YELLOW}Активный профиль удалён — профиль сброшен.{RESET}")
        return None
    return current


def choose_profile(repo: ProfileRepository,
                   current: Optional[Profile]) -> Optional[Profile]:
    names = repo.list_names()
    if not names:
        return repo.ensure_default()

    print(f"\n{BOLD}Выберите профиль:{RESET}")
    for i, n in enumerate(names, 1):
        marker = " ◀" if current and n == current.name else ""
        print(f"  {CYAN}{i}{RESET}. {n}{marker}")
    print(f"  {CYAN}+{RESET}. Создать новый")
    print(f"  {CYAN}n{RESET}. Без профиля")
    try:
        choice = input("Номер (Enter — оставить текущий): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return current

    if choice == "+":
        return create_profile(repo, current)
    if choice == "n":
        return None
    if choice.isdigit() and 1 <= int(choice) <= len(names):
        target = names[int(choice) - 1]
        loaded = repo.load(target)
        if loaded:
            print(f"{GREEN}Профиль: {target}{RESET}")
        return loaded
    return current

# ══════════════════════════════════════════════════════════════════════════════
# СЛОЙ 2 — РАБОЧАЯ ПАМЯТЬ: контекст текущей задачи
# ══════════════════════════════════════════════════════════════════════════════
# Модель — domain.working_memory.WorkingMemory.
# Хранилище — infra.working_memory_repository.FileWorkingMemoryRepository.
# UI-функции ниже останутся здесь до выделения слоя cli/.

WorkingMemory = _DomainWorkingMemory  # переходный алиас: убрать при переезде вызовов


def wm_show(wm: WorkingMemory) -> None:
    if wm.is_empty():
        print(f"    {DIM}пусто{RESET}")
        return
    if wm.task:
        print(f"    {BOLD}Задача:{RESET} {wm.task}")
    if wm.context:
        print(f"    {BOLD}Контекст:{RESET}")
        for k, v in wm.context.items():
            print(f"      {CYAN}{k}{RESET}: {v}")
    if wm.notes:
        print(f"    {BOLD}Заметки:{RESET}")
        for note in wm.notes:
            print(f"      • {note}")
    if wm.updated_at:
        print(f"    {DIM}обновлено: {wm.updated_at}{RESET}")


def wm_status_badge(wm: WorkingMemory) -> str:
    """Однострочный индикатор для строки статуса."""
    if wm.is_empty():
        return f"{DIM}рабочая: —{RESET}"
    parts = []
    if wm.task:
        short = wm.task[:30] + ("…" if len(wm.task) > 30 else "")
        parts.append(short)
    if wm.context:
        parts.append(f"{len(wm.context)} ключ.")
    if wm.notes:
        parts.append(f"{len(wm.notes)} заметок")
    return f"{MAGENTA}рабочая: {', '.join(parts)}{RESET}"


def handle_wm(cmd_str: str, wm: WorkingMemory, wm_repo: FileWorkingMemoryRepository):
    """Обрабатывает /wm <sub> <args>."""
    parts = cmd_str.split(None, 2)
    sub   = parts[1].lower() if len(parts) > 1 else "show"

    if sub in ("show", ""):
        print(f"\n{BOLD}{MAGENTA}Рабочая память:{RESET}")
        wm_show(wm)
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
            wm_repo.save(wm)
            print(f"{GREEN}  Задача установлена.{RESET}")

    elif sub == "set":
        rest = parts[2] if len(parts) > 2 else ""
        kv   = rest.split(None, 1)
        if len(kv) < 2:
            print(f"{YELLOW}  Использование: /wm set <ключ> <значение>{RESET}")
        else:
            wm.context[kv[0]] = kv[1]
            wm_repo.save(wm)
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
            wm_repo.save(wm)
            print(f"{GREEN}  Заметка добавлена.{RESET}")

    elif sub == "del":
        key = parts[2].strip() if len(parts) > 2 else ""
        if key in wm.context:
            del wm.context[key]
            wm_repo.save(wm)
            print(f"{GREEN}  Ключ «{key}» удалён.{RESET}")
        else:
            print(f"{YELLOW}  Ключ «{key}» не найден.{RESET}")

    elif sub == "clear":
        wm_repo.clear()
        wm.task = wm.created_at = wm.updated_at = None
        wm.context.clear()
        wm.notes.clear()
        print(f"{DIM}  Рабочая память очищена.{RESET}")

    else:
        print(f"{YELLOW}  Подкоманды /wm: show · task · set · note · del · clear{RESET}")


# ── База знаний: команды ──────────────────────────────────────────────────────

def handle_know(cmd_str: str, repo: KnowledgeRepository):
    """/know save <имя> | /know list | /know show <имя>"""
    parts = cmd_str.split(None, 2)
    sub   = parts[1].lower() if len(parts) > 1 else "list"

    if sub == "list":
        names = repo.list_names()
        if not names:
            print(f"{DIM}  База знаний пуста. Используй /know save <имя>{RESET}")
        else:
            print(f"\n{BOLD}{BLUE}База знаний:{RESET}")
            for n in names:
                print(f"    {BLUE}•{RESET} {n}")
            print()

    elif sub == "save":
        name = sanitize_knowledge_name(parts[2]) if len(parts) > 2 else ""
        if not name:
            try:
                name = sanitize_knowledge_name(input("  Имя записи: "))
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
            repo.save(KnowledgeEntry(name=name, content="\n".join(lines)))
            print(f"{GREEN}  Сохранено в базу знаний: {name}{RESET}")

    elif sub == "show":
        name = parts[2].strip() if len(parts) > 2 else ""
        if not name:
            print(f"{YELLOW}  Использование: /know show <имя>{RESET}")
            return
        entry = repo.load(name)
        if entry is None:
            print(f"{YELLOW}  Запись «{name}» не найдена.{RESET}")
        else:
            print(f"\n{BOLD}{BLUE}{entry.name}:{RESET}\n{entry.to_file_text()}")

    else:
        print(f"{YELLOW}  Подкоманды /know: list · save · show{RESET}")


# ══════════════════════════════════════════════════════════════════════════════
# СЛОЙ 1 — КРАТКОСРОЧНАЯ ПАМЯТЬ: диалог текущей сессии
# ══════════════════════════════════════════════════════════════════════════════
# Хранилище и ротация — в infra.session_repository.FileSessionRepository.
# Идентификатор активной сессии живёт как локальная переменная в main().

# ══════════════════════════════════════════════════════════════════════════════
# СЛОЙ 4 — МАШИНА СОСТОЯНИЙ ЗАДАЧИ
# ══════════════════════════════════════════════════════════════════════════════
# Модель — domain.task.Task.
# Хранилище и указатель активной задачи — infra.task_repository.FileTaskRepository.

Task = _DomainTask  # переходный алиас: убрать при переезде вызовов


# ── Use cases работы с задачей ────────────────────────────────────────────────
# Логика стадий, промпты, парсеры и драйвер вынесены в app/:
#   app.parsers          — parse_questions, parse_validation_verdict
#   app.stage_prompts    — STAGE_PROMPTS, build_task_block, next_forward_state
#   app.task_driver      — advance_task, handle_plan_approval, handle_plan_revision

_YES = {"y", "yes", "да", "д"}
_NO  = {"n", "no", "нет", "н"}


def announce_task_transitions(task: Task, prev_state: str) -> None:
    """Если стадия изменилась во время advance_task — печатаем явное сообщение.
    Помогает не пропустить validation→execution или достижение done.
    """
    if task.state == prev_state:
        return
    if task.state == TaskState.DONE:
        print(f"\n{GREEN}{BOLD}✓ Задача #{task.id} завершена (validation OK).{RESET}\n")
    elif prev_state == TaskState.VALIDATION and task.state == TaskState.EXECUTION:
        print(f"\n{YELLOW}↻ Валидация нашла проблемы — возвращаемся к выполнению.{RESET}\n")
    elif prev_state == TaskState.PLANNING and task.state == TaskState.EXECUTION:
        print(f"\n{GREEN}→ Перешли к выполнению плана.{RESET}\n")
    elif prev_state == TaskState.INTAKE and task.state == TaskState.PLANNING:
        print(f"\n{GREEN}→ Уточнения собраны — перехожу к планированию.{RESET}\n")
    else:
        print(f"\n{DIM}→ {prev_state} → {task.state}{RESET}\n")


def show_task(task: Task):
    print(f"\n{BOLD}{MAGENTA}Задача #{task.id}:{RESET} {task.title}")
    print(f"  {DIM}запрос:{RESET} {task.request}")
    print(f"  {BOLD}стадия:{RESET} {task.state}")
    if task.created_at or task.updated_at:
        print(f"  {DIM}создана: {task.created_at}  обновлена: {task.updated_at}{RESET}")
    if task.profile_snapshot or task.model_snapshot:
        print(f"  {DIM}профиль: {task.profile_snapshot or '—'}  модель: {task.model_snapshot or '—'}{RESET}")
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
            extra = ""
            revs = r.artifacts.get("revisions") if r.artifacts else None
            if revs:
                extra += f"  ({len(revs)} версий до текущей)"
            print(f"    {mark} {s}: {r.status}{extra}")
    counts = []
    if task.answers:
        clar = sum(1 for a in task.answers if a.get("kind") == "clarification")
        rev  = sum(1 for a in task.answers if a.get("kind") == "plan_revision")
        if clar:
            counts.append(f"{clar} уточн.")
        if rev:
            counts.append(f"{rev} правок плана")
    if task.transitions:
        counts.append(f"{len(task.transitions)} переходов")
    if counts:
        print(f"  {DIM}история: {', '.join(counts)}{RESET}")
    if task.transitions:
        last = task.transitions[-1]
        print(f"  {DIM}последний переход: {last['from']} → {last['to']} ({last.get('reason','')}){RESET}")
    current = task.stages.get(task.state)
    if current and current.output:
        print(f"\n  {BOLD}текущий результат:{RESET}\n{current.output}")
    print()


def handle_task(cmd_str: str,
                params: dict,
                current_profile: Optional[Profile],
                wm: "WorkingMemory",
                client: GigaChatClient,
                task_repo: TaskRepository,
                knowledge_repo: KnowledgeRepository) -> None:
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
        active = task_repo.get_active()
        if active and not active.is_terminal():
            print(f"{YELLOW}  Уже есть активная задача #{active.id} ({active.state}). "
                  f"Сначала /task abort или /task done.{RESET}")
            return
        # Если active указывает на терминальную задачу — расчищаем перед стартом
        # новой, чтобы дашборд не висел в неопределённом виде.
        if active and active.is_terminal():
            task_repo.clear_active()
        task = Task.new(
            request,
            profile=current_profile.name if current_profile else None,
            model=params["model"],
        )
        task_repo.save(task)
        task_repo.set_active(task)
        print(f"{GREEN}  Создана задача #{task.id} (стадия: {task.state}).{RESET}")
        profile_text = current_profile.content if current_profile else None
        try:
            with Spinner("Думаю..."):
                reply = advance_task(task, request, params, profile_text, wm, client, task_repo, knowledge_repo)
        except Exception as e:
            print(f"{YELLOW}  Ошибка стадии: {e}{RESET}")
            return
        print(f"\n{BOLD}{GREEN}Agent:{RESET} {reply}\n")
        return

    if sub in ("show", ""):
        task = task_repo.get_active()
        if not task:
            print(f"{DIM}  Активной задачи нет.{RESET}")
            return
        show_task(task)
        return

    if sub == "list":
        tasks = task_repo.list_all()
        if not tasks:
            print(f"{DIM}  Задач нет.{RESET}")
            return
        active_id = task_repo.get_active_id()
        print(f"\n{BOLD}Задачи:{RESET}")
        for t in tasks:
            mark = f" {YELLOW}◀ активная{RESET}" if t.id == active_id else ""
            title = t.title[:50] + ("…" if len(t.title) > 50 else "")
            updated = t.updated_at or "—"
            print(f"  {CYAN}#{t.id}{RESET}  {t.state:10}  {DIM}{updated}{RESET}  {title}{mark}")
        print()
        return

    if sub == "resume":
        tid = parts[2].strip() if len(parts) > 2 else ""
        if not tid:
            print(f"{YELLOW}  Использование: /task resume <id>{RESET}")
            return
        t = task_repo.load(tid)
        if not t:
            print(f"{YELLOW}  Задача #{tid} не найдена.{RESET}")
            return
        task_repo.set_active(t)
        print(f"{GREEN}  Активной выбрана #{t.id} (стадия: {t.state}).{RESET}")
        show_task(t)
        return

    if sub == "advance":
        task = task_repo.get_active()
        if not task:
            print(f"{YELLOW}  Активной задачи нет.{RESET}")
            return
        nxt = next_forward_state(task.state)
        if not nxt:
            print(f"{YELLOW}  Из {task.state} вперёд идти некуда.{RESET}")
            return
        reason = parts[2].strip() if len(parts) > 2 else "ручной переход вперёд"
        try:
            task_repo.transition(task, nxt, reason=reason)
        except TaskTransitionError as e:
            print(f"{YELLOW}  {e}{RESET}")
            return
        print(f"{GREEN}  Стадия: {task.state}.{RESET}")
        return

    if sub == "back":
        task = task_repo.get_active()
        if not task:
            print(f"{YELLOW}  Активной задачи нет.{RESET}")
            return
        target = parts[2].strip() if len(parts) > 2 else ""
        if not target:
            print(f"{YELLOW}  Использование: /task back <стадия>{RESET}")
            return
        try:
            task_repo.transition(task, target, reason="ручной откат")
        except TaskTransitionError as e:
            print(f"{YELLOW}  {e}{RESET}")
            return
        print(f"{GREEN}  Стадия: {task.state}.{RESET}")
        return

    if sub == "abort":
        task = task_repo.get_active()
        if not task:
            print(f"{YELLOW}  Активной задачи нет.{RESET}")
            return
        reason = parts[2].strip() if len(parts) > 2 else "пользователь отменил"
        try:
            task_repo.transition(task, TaskState.ABORTED, reason=reason)
        except TaskTransitionError as e:
            print(f"{YELLOW}  {e}{RESET}")
            return
        task_repo.clear_active()
        print(f"{DIM}  Задача #{task.id} отменена.{RESET}")
        return

    if sub == "done":
        task = task_repo.get_active()
        if not task:
            print(f"{YELLOW}  Активной задачи нет.{RESET}")
            return
        try:
            task_repo.transition(task, TaskState.DONE, reason="вручную завершено")
        except TaskTransitionError as e:
            print(f"{YELLOW}  {e}{RESET}")
            return
        task_repo.clear_active()
        print(f"{GREEN}  Задача #{task.id} завершена.{RESET}")
        return

    if sub == "delete":
        tid = parts[2].strip() if len(parts) > 2 else ""
        if not tid:
            print(f"{YELLOW}  Использование: /task delete <id>{RESET}")
            return
        t = task_repo.load(tid)
        if not t:
            # Файл задачи мог отсутствовать (никогда не сохранялась), но active
            # pointer всё ещё может на неё указывать — почистим, чтобы дашборд
            # не показывал «призрак».
            if task_repo.get_active_id() == tid:
                task_repo.clear_active()
                print(f"{YELLOW}  Задача #{tid} не найдена на диске; active-указатель очищен.{RESET}")
            else:
                print(f"{YELLOW}  Задача #{tid} не найдена.{RESET}")
            return
        try:
            confirm = input(f"  Удалить задачу #{t.id} «{t.title}»? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return
        if confirm not in _YES:
            print(f"{DIM}  Отменено.{RESET}")
            return
        task_repo.delete(t)  # сам подчистит active pointer если нужно
        print(f"{GREEN}  Задача #{tid} удалена.{RESET}")
        return

    if sub == "log":
        tid = parts[2].strip() if len(parts) > 2 else ""
        task = task_repo.load(tid) if tid else task_repo.get_active()
        if not task:
            print(f"{YELLOW}  {'Задача не найдена.' if tid else 'Активной задачи нет.'}{RESET}")
            return
        print(f"\n{BOLD}История задачи #{task.id}:{RESET}")
        if not task.transitions:
            print(f"  {DIM}переходов ещё не было{RESET}")
        for tr in task.transitions:
            reason = tr.get("reason", "") or ""
            print(f"  {DIM}{tr.get('at','')}{RESET}  {tr['from']:10} → {tr['to']:10}  {DIM}{reason}{RESET}")
        print()
        return

    print(f"{YELLOW}  Подкоманды /task: new · show · list · resume · advance · back · "
          f"abort · done · delete · log{RESET}")


# ── HTTP / GigaChat ───────────────────────────────────────────────────────────
# OAuth, токен-кэш и вызов модели — в infra.gigachat_client.RequestsGigaChatClient.
# Экземпляр собирается в main() и прокидывается явным параметром.

# ── UI helpers ────────────────────────────────────────────────────────────────
def print_settings(params: dict, current_profile: Optional[Profile]):
    temp  = params["temperature"] if params["temperature"] is not None else "auto"
    maxt  = params["max_tokens"]  if params["max_tokens"]  is not None else "auto"
    pname = current_profile.name if current_profile else "нет"
    print(f"{DIM}  модель: {params['model']}  temperature: {temp}  max_tokens: {maxt}  профиль: {pname}{RESET}")

def _task_status_badge(task_repo: TaskRepository) -> str:
    """Однострочный индикатор активной задачи для общего дашборда."""
    task = task_repo.get_active()
    if not task or task.is_terminal():
        return f"{DIM}задача: —{RESET}"
    short = task.title[:30] + ("…" if len(task.title) > 30 else "")
    extras = []
    if task.awaiting == "plan_approval":
        extras.append("ждёт y/n плана")
    elif task.awaiting == "plan_revision_input":
        extras.append("ждёт правок плана")
    elif task.pending_questions:
        extras.append(f"{len(task.pending_questions)} вопр.")
    suffix = f" · {', '.join(extras)}" if extras else ""
    return f"{YELLOW}задача: #{task.id} {task.state} · {short}{suffix}{RESET}"


def print_memory_status(messages: list,
                        wm: WorkingMemory,
                        task_repo: TaskRepository,
                        current_profile: Optional[Profile],
                        knowledge_repo: KnowledgeRepository):
    """Однострочный дашборд всех слоёв памяти + активная задача."""
    # краткосрочная
    st_label = f"{GREEN}краткосрочная: {len(messages)} сообщ.{RESET}" if messages \
               else f"{DIM}краткосрочная: —{RESET}"
    # рабочая
    wm_label = wm_status_badge(wm)
    # долговременная
    pname    = current_profile.name if current_profile else "нет"
    k_count  = len(knowledge_repo.list_names())
    lt_label = f"{BLUE}долговременная: {pname}"
    if k_count:
        lt_label += f", {k_count} знаний"
    lt_label += RESET
    # задача
    task_label = _task_status_badge(task_repo)

    print(f"  {st_label}  │  {wm_label}  │  {lt_label}  │  {task_label}")

def print_mem_detail(messages: list,
                     wm: WorkingMemory,
                     session_id: Optional[str],
                     session_repo: FileSessionRepository,
                     task_repo: TaskRepository,
                     current_profile: Optional[Profile],
                     knowledge_repo: KnowledgeRepository):
    """Подробный вывод всех трёх слоёв."""
    print(f"\n{BOLD}═══ Модель памяти ═══{RESET}\n")

    # Слой 1
    print(f"{BOLD}{GREEN}[1] Краткосрочная память{RESET}  {DIM}(текущий диалог){RESET}")
    if messages:
        print(f"    {len(messages)} сообщений в текущей сессии")
        if session_id:
            print(f"    {DIM}файл: {session_repo.path_for(session_id)}{RESET}")
    else:
        print(f"    {DIM}пусто (новая сессия){RESET}")
    total = len(session_repo.list_all())
    if total:
        print(f"    {DIM}всего сохранённых сессий: {total}{RESET}")

    # Слой 2
    print(f"\n{BOLD}{MAGENTA}[2] Рабочая память{RESET}  {DIM}(задача и контекст){RESET}")
    wm_show(wm)

    # Слой 3
    print(f"\n{BOLD}{BLUE}[3] Долговременная память{RESET}  {DIM}(профиль + знания){RESET}")
    pname = current_profile.name if current_profile else "нет"
    print(f"    Профиль: {pname}")
    knames = knowledge_repo.list_names()
    if knames:
        print(f"    База знаний ({len(knames)} записей):")
        for n in knames:
            print(f"      {BLUE}•{RESET} {n}")
    else:
        print(f"    {DIM}База знаний пуста. Используй /know save{RESET}")

    # Слой 4
    print(f"\n{BOLD}{YELLOW}[4] Задача{RESET}  {DIM}(машина состояний){RESET}")
    active = task_repo.get_active()
    if active and not active.is_terminal():
        print(f"    Активная: #{active.id} «{active.title}» (стадия: {active.state})")
        if active.awaiting:
            print(f"    {YELLOW}Ожидание ввода:{RESET} {active.awaiting}")
    else:
        print(f"    {DIM}Активной задачи нет.{RESET}")
    all_tasks = task_repo.list_all()
    if all_tasks:
        nonterm = sum(1 for t in all_tasks if not t.is_terminal())
        done    = sum(1 for t in all_tasks if t.state == TaskState.DONE)
        abort   = sum(1 for t in all_tasks if t.state == TaskState.ABORTED)
        print(f"    {DIM}всего задач: {len(all_tasks)} (активные: {nonterm}, done: {done}, aborted: {abort}){RESET}")
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
  {CYAN}/task log [id]{RESET}       — история переходов задачи
  {CYAN}/task abort{RESET}          — отменить задачу
  {CYAN}/task done{RESET}           — пометить задачу завершённой
  {CYAN}/task delete <id>{RESET}    — удалить задачу с диска
  {DIM}Шлюзы и автопереходы:
    planning → execution     по «y» (или «n» → правки плана)
    validation → done        по метке [VALIDATION OK] от модели
    validation → execution   по метке [VALIDATION ISSUES] от модели
    [QUESTION] в ответе      → задача переходит в режим ожидания ответа{RESET}

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
    current_session_id: Optional[str] = None

    # Composition root: собираем инфраструктурные зависимости.
    wm_repo        = FileWorkingMemoryRepository(os.path.join(WORKING_DIR, "current.json"))
    session_repo   = FileSessionRepository(HISTORY_DIR, MAX_SESSIONS)
    task_repo      = FileTaskRepository(TASKS_DIR, ACTIVE_TASK_FILE)
    profile_repo   = FileProfileRepository(PROFILES_DIR)
    knowledge_repo = FileKnowledgeRepository(KNOWLEDGE_DIR)
    client         = RequestsGigaChatClient(
        auth_key  = AUTH_KEY,
        oauth_url = OAUTH_URL,
        chat_url  = CHAT_URL,
        scope     = SCOPE,
    )

    print(f"\n{BOLD}{GREEN}Jarvis CLI{RESET}  {DIM}(введите /help для справки){RESET}\n")

    if not AUTH_KEY:
        print(f"{YELLOW}Ошибка: GIGACHAT_AUTH_KEY не задан.{RESET}")
        print(f"{DIM}Создайте файл .env рядом с chat.py:{RESET}")
        print(f"{DIM}  GIGACHAT_AUTH_KEY=ваш_ключ{RESET}\n")
        sys.exit(1)

    # Инициализация долговременной памяти
    current_profile: Optional[Profile] = profile_repo.ensure_default()

    # Инициализация рабочей памяти
    wm = wm_repo.load()
    if not wm.is_empty():
        print(f"{MAGENTA}Рабочая память загружена:{RESET}")
        wm_show(wm)
        print()

    # Выбор краткосрочной памяти (сессии)
    sessions = session_repo.list_all()
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
            current_session_id = s["id"]
            print(f"{DIM}Загружено {len(messages)} сообщений.{RESET}\n")

    # Восстановление активной задачи (Слой 4): спрашиваем пользователя, продолжать ли.
    pending_restoration_hint = False
    saved_active = task_repo.get_active()
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
            task_repo.clear_active()
            print(f"{DIM}  Задача #{saved_active.id} оставлена в /task list (но не активна).{RESET}\n")

    print_settings(params, current_profile)
    print_memory_status(messages, wm, task_repo, current_profile, knowledge_repo)
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
                print_settings(params, current_profile)
                print_memory_status(messages, wm, task_repo, current_profile, knowledge_repo)
            elif cmd == "/mem":
                print_mem_detail(messages, wm, current_session_id, session_repo,
                                 task_repo, current_profile, knowledge_repo)
            elif cmd.startswith("/wm"):
                handle_wm(user_input, wm, wm_repo)
            elif cmd.startswith("/know"):
                handle_know(user_input, knowledge_repo)
            elif cmd.startswith("/task"):
                handle_task(user_input, params, current_profile, wm,
                            client, task_repo, knowledge_repo)
            elif cmd == "/profile new":
                current_profile = create_profile(profile_repo, current_profile)
            elif cmd == "/profile edit":
                current_profile = edit_profile(profile_repo, current_profile)
            elif cmd == "/profile delete":
                current_profile = delete_profile(profile_repo, current_profile)
            elif cmd == "/profile":
                current_profile = choose_profile(profile_repo, current_profile)
            elif cmd == "/clear":
                if current_session_id:
                    session_repo.delete(current_session_id)
                    current_session_id = None
                messages.clear()
                print(f"{DIM}Краткосрочная память очищена (диалог).{RESET}")
            elif cmd == "/help":
                print_help()
            else:
                print(f"{YELLOW}Неизвестная команда. Введите /help.{RESET}")
            continue

        # Если есть активная нетерминальная задача — ввод идёт в её драйвер,
        # а не в обычный чат. Сначала проверяем спец-режимы (plan_approval,
        # plan_revision_input), потом обычный clarification/stage цикл.
        active_task = task_repo.get_active()
        if active_task and not active_task.is_terminal():

            # === шлюз утверждения плана ===
            if active_task.awaiting == "plan_approval":
                result = handle_plan_approval(active_task, user_input, task_repo)
                if result == PLAN_APPROVAL_RETRY:
                    print(f"{YELLOW}  Ответь «y» (одобрить) или «n» (нужны правки).{RESET}")
                    continue
                if result == PLAN_APPROVAL_REJECTED:
                    print(f"{DIM}  План отклонён.{RESET}")
                    print(f"{BOLD}Что нужно поправить в плане?{RESET}")
                    continue
                # APPROVED → planning закрыт, мы уже в execution, сразу запускаем стадию.
                print(f"{GREEN}  План утверждён. Перехожу к выполнению.{RESET}\n")
                prev_state = active_task.state
                try:
                    with Spinner("Думаю..."):
                        reply = advance_task(active_task, "", params,
                                             current_profile.content if current_profile else None,
                                             wm, client, task_repo, knowledge_repo,
                                             restoration_hint=pending_restoration_hint)
                except Exception as e:
                    print(f"{YELLOW}Ошибка: {e}{RESET}")
                    continue
                pending_restoration_hint = False
                print(f"{BOLD}{GREEN}Agent:{RESET} {reply}\n")
                announce_task_transitions(active_task, prev_state)
                continue

            # === пользователь ответил на «что поправить?» ===
            if active_task.awaiting == "plan_revision_input":
                try:
                    handle_plan_revision(active_task, user_input, task_repo)
                except RuntimeError as e:
                    print(f"{YELLOW}  {e}{RESET}")
                    continue
                # Сразу перегенерируем план.
                prev_state = active_task.state
                try:
                    with Spinner("Перепланирую..."):
                        reply = advance_task(active_task, "", params,
                                             current_profile.content if current_profile else None,
                                             wm, client, task_repo, knowledge_repo,
                                             restoration_hint=pending_restoration_hint)
                except Exception as e:
                    print(f"{YELLOW}Ошибка: {e}{RESET}")
                    continue
                pending_restoration_hint = False
                print(f"{BOLD}{GREEN}Agent:{RESET} {reply}\n")
                announce_task_transitions(active_task, prev_state)
                continue

            # === обычный режим: stage prompt + (опционально) clarification ===
            prev_state = active_task.state
            try:
                with Spinner("Думаю..."):
                    reply = advance_task(active_task, user_input, params,
                                         current_profile.content if current_profile else None,
                                         wm, client, task_repo, knowledge_repo,
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
            announce_task_transitions(active_task, prev_state)
            continue

        # Краткосрочная память: добавляем сообщение пользователя
        messages.append({"role": "user", "content": user_input})

        # Формируем system prompt из долговременной + рабочей памяти
        system_prompt = build_system_prompt(
            current_profile.content if current_profile else None,
            wm,
            knowledge_repo,
        )

        try:
            with Spinner("Думаю..."):
                reply = client.chat(messages, params, system_prompt)
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
        current_session_id = session_repo.save(current_session_id, messages, params)
        print()

if __name__ == "__main__":
    main()
