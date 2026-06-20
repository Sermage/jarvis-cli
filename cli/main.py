"""REPL и composition root приложения.

Здесь собираются конкретные реализации портов (infra/), прокидываются
в use cases (app/) и UI-обработчики (cli/*_commands.py). Никакой
бизнес-логики и I/O напрямую — только сборка графа зависимостей и
маршрутизация команд.
"""
from __future__ import annotations

import os
import sys
from typing import Optional

import requests

from app.invariant_guard import guarded_chat
from app.orchestrator import build_default_orchestrator
from app.system_prompt import build_system_prompt
from app.task_driver import (
    PLAN_APPROVAL_REJECTED,
    PLAN_APPROVAL_RETRY,
    advance_task,
    handle_plan_approval,
    handle_plan_revision,
)
from cli.ansi import BOLD, CYAN, DIM, GREEN, MAGENTA, RESET, YELLOW
from cli.config import (
    ACTIVE_TASK_FILE,
    CHAT_URL,
    DEFAULT_PARAMS,
    HISTORY_DIR,
    INVARIANTS_DIR,
    KNOWLEDGE_DIR,
    MAX_SESSIONS,
    OAUTH_URL,
    PROFILES_DIR,
    SCOPE,
    TASKS_DIR,
    WORKING_DIR,
    load_env,
)
from cli.invariant_commands import handle_inv
from cli.know_commands import handle_know
from cli.profile_commands import (
    choose_profile,
    create_profile,
    delete_profile,
    edit_profile,
)
from cli.settings_commands import choose_model, set_max_tokens, set_temperature
from cli.spinner import Spinner
from cli.task_commands import handle_task
from cli.views import (
    announce_guard_result,
    announce_task_transitions,
    print_help,
    print_mem_detail,
    print_memory_status,
    print_settings,
    show_task,
    wm_show,
)
from cli.wm_commands import handle_wm
from domain.profile import Profile
from infra.gigachat_client import RequestsGigaChatClient
from infra.invariant_repository import FileInvariantRepository
from infra.knowledge_repository import FileKnowledgeRepository
from infra.profile_repository import FileProfileRepository
from infra.session_repository import FileSessionRepository
from infra.task_repository import FileTaskRepository
from infra.working_memory_repository import FileWorkingMemoryRepository


_YES = {"y", "yes", "да", "д"}


def main():
    # .env лежит рядом с настоящим entrypoint-файлом (chat.py), а не рядом
    # с симлинком jarvis в /usr/local/bin — поэтому realpath.
    entrypoint = os.path.realpath(sys.argv[0]) if sys.argv and sys.argv[0] else __file__
    load_env(os.path.join(os.path.dirname(entrypoint), ".env"))
    auth_key = os.environ.get("GIGACHAT_AUTH_KEY", "")

    params   = dict(DEFAULT_PARAMS)
    messages: list = []
    current_session_id: Optional[str] = None

    # Composition root: собираем инфраструктурные зависимости.
    wm_repo        = FileWorkingMemoryRepository(os.path.join(WORKING_DIR, "current.json"))
    session_repo   = FileSessionRepository(HISTORY_DIR, MAX_SESSIONS)
    task_repo      = FileTaskRepository(TASKS_DIR, ACTIVE_TASK_FILE)
    profile_repo   = FileProfileRepository(PROFILES_DIR)
    knowledge_repo = FileKnowledgeRepository(KNOWLEDGE_DIR)
    invariant_repo = FileInvariantRepository(INVARIANTS_DIR)
    client         = RequestsGigaChatClient(
        auth_key  = auth_key,
        oauth_url = OAUTH_URL,
        chat_url  = CHAT_URL,
        scope     = SCOPE,
    )
    orchestrator   = build_default_orchestrator(task_repo)

    print(f"\n{BOLD}{GREEN}Jarvis CLI{RESET}  {DIM}(введите /help для справки){RESET}\n")

    if not auth_key:
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
                            client, task_repo, knowledge_repo, invariant_repo,
                            orchestrator=orchestrator)
            elif cmd.startswith("/inv"):
                handle_inv(user_input, invariant_repo)
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
                                             invariant_repo=invariant_repo,
                                             restoration_hint=pending_restoration_hint,
                                             orchestrator=orchestrator)
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
                                             invariant_repo=invariant_repo,
                                             restoration_hint=pending_restoration_hint,
                                             orchestrator=orchestrator)
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
                                         invariant_repo=invariant_repo,
                                         restoration_hint=pending_restoration_hint,
                                         orchestrator=orchestrator)
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

        # Формируем system prompt из долговременной + рабочей памяти + инвариантов
        system_prompt = build_system_prompt(
            current_profile.content if current_profile else None,
            wm,
            knowledge_repo,
            invariant_repo,
        )

        try:
            with Spinner("Думаю..."):
                guarded = guarded_chat(client, messages, params, system_prompt,
                                       invariant_repo.load_all(), max_retries=1)
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

        reply = guarded.reply
        print(f"{BOLD}{GREEN}Agent:{RESET} {reply}")
        announce_guard_result(guarded)

        # Краткосрочная память: сохраняем ответ ассистента
        messages.append({"role": "assistant", "content": reply})
        current_session_id = session_repo.save(current_session_id, messages, params)
        print()
