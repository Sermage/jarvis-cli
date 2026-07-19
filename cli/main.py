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
from app.tool_router import ToolRouter
from cli.ansi import BOLD, CLEAR_SCREEN, CYAN, DIM, GREEN, MAGENTA, RESET, YELLOW
from cli.config import (
    ACTIVE_TASK_FILE,
    DEEPSEEK,
    DEEPSEEK_CHAT_URL,
    DEFAULT_PARAMS,
    GIGACHAT,
    GIGACHAT_CHAT_URL,
    GIGACHAT_OAUTH_URL,
    GIGACHAT_SCOPE,
    HISTORY_DIR,
    INVARIANTS_DIR,
    KNOWLEDGE_DIR,
    MAX_SESSIONS,
    MCP_CONFIG_FILE,
    OLLAMA,
    OLLAMA_BASE_URL,
    PROFILES_DIR,
    TASKS_DIR,
    WORKING_DIR,
    DEFAULT_EMBED_MODEL,
    DEFAULT_OLLAMA_URL,
    code_index_path,
    default_model_for,
    load_env,
    load_rag_config,
    resolve_provider,
)
from cli.help_commands import handle_help
from cli.review_commands import handle_review
from cli.invariant_commands import handle_inv
from cli.know_commands import handle_know
from cli.mcp_commands import handle_mcp
from cli.profile_commands import (
    choose_profile,
    create_profile,
    delete_profile,
    edit_profile,
)
from cli.rag_commands import handle_rag
from cli.settings_commands import (
    choose_model,
    choose_provider,
    set_context_window,
    set_max_tokens,
    set_temperature,
)
from cli.input_reader import (
    disable_bracketed_paste,
    enable_bracketed_paste,
    read_input,
)
from cli.spinner import Spinner
from cli.task_commands import handle_task
from cli.tool_progress import ToolProgressReporter
from cli.views import (
    announce_guard_result,
    announce_task_transitions,
    print_mem_detail,
    print_memory_status,
    print_settings,
    show_task,
    wm_show,
)
from cli.wm_commands import handle_wm
from app.ports import LLMClient
from app.retrieval_pipeline import RetrievalPipeline
from domain.profile import Profile
from infra.deepseek_client import DeepSeekClient
from infra.gigachat_client import RequestsGigaChatClient
from infra.ollama_client import OllamaClient
from infra.invariant_repository import FileInvariantRepository
from infra.knowledge_repository import FileKnowledgeRepository
from infra.mcp_config_repository import FileMcpConfigRepository
from infra.mcp_git import McpGitContextProvider
from infra.mcp_registry import StdioMcpRegistry
from infra.local_fs_client import LocalFilesystemClient
from cli.fs_confirm import make_interactive_confirm
from infra.pr_diff import GhDiffProvider
from infra.profile_repository import FileProfileRepository
from infra.query_rewriter import LLMQueryRewriter
from infra.rag_retrieval import CompositeRetrievalEngine, FaissOllamaRetrievalEngine
from infra.rerankers import HeuristicReranker, LLMReranker
from infra.session_repository import FileSessionRepository
from infra.task_repository import FileTaskRepository
from infra.working_memory_repository import FileWorkingMemoryRepository


_YES = {"y", "yes", "да", "д"}


def _build_client(provider: str) -> LLMClient:
    """Собрать LLM-клиент для выбранного провайдера.

    Ключи читаются из os.environ; вызывающий код отвечает за то, чтобы
    .env был уже подгружен.
    """
    if provider == DEEPSEEK:
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            print(f"{YELLOW}Ошибка: DEEPSEEK_API_KEY не задан.{RESET}")
            print(f"{DIM}Получить ключ: https://platform.deepseek.com/api_keys{RESET}")
            print(f"{DIM}И прописать в .env: DEEPSEEK_API_KEY=...{RESET}\n")
            sys.exit(1)
        return DeepSeekClient(api_key=api_key, chat_url=DEEPSEEK_CHAT_URL)

    if provider == GIGACHAT:
        auth_key = os.environ.get("GIGACHAT_AUTH_KEY", "")
        if not auth_key:
            print(f"{YELLOW}Ошибка: GIGACHAT_AUTH_KEY не задан.{RESET}")
            print(f"{DIM}Получить ключ: https://developers.sber.ru/studio{RESET}")
            print(f"{DIM}И прописать в .env: GIGACHAT_AUTH_KEY=...{RESET}\n")
            sys.exit(1)
        return RequestsGigaChatClient(
            auth_key  = auth_key,
            oauth_url = GIGACHAT_OAUTH_URL,
            chat_url  = GIGACHAT_CHAT_URL,
            scope     = GIGACHAT_SCOPE,
        )

    if provider == OLLAMA:
        base_url = os.environ.get("OLLAMA_BASE_URL", "").strip() or OLLAMA_BASE_URL
        return OllamaClient(base_url=base_url)

    raise RuntimeError(f"Неизвестный провайдер: {provider}")


def main():
    # .env лежит рядом с настоящим entrypoint-файлом (chat.py), а не рядом
    # с симлинком jarvis в /usr/local/bin — поэтому realpath.
    entrypoint = os.path.realpath(sys.argv[0]) if sys.argv and sys.argv[0] else __file__
    load_env(os.path.join(os.path.dirname(entrypoint), ".env"))

    provider = resolve_provider(os.environ.get("LLM_PROVIDER", ""))
    params   = dict(DEFAULT_PARAMS)
    params["model"] = default_model_for(provider)
    messages: list = []
    current_session_id: Optional[str] = None

    # Composition root: собираем инфраструктурные зависимости.
    wm_repo        = FileWorkingMemoryRepository(os.path.join(WORKING_DIR, "current.json"))
    session_repo   = FileSessionRepository(HISTORY_DIR, MAX_SESSIONS)
    task_repo      = FileTaskRepository(TASKS_DIR, ACTIVE_TASK_FILE)
    profile_repo   = FileProfileRepository(PROFILES_DIR)
    knowledge_repo = FileKnowledgeRepository(KNOWLEDGE_DIR)
    invariant_repo = FileInvariantRepository(INVARIANTS_DIR)
    mcp_repo       = FileMcpConfigRepository(MCP_CONFIG_FILE)

    print(f"\n{BOLD}{GREEN}Jarvis CLI{RESET}  {DIM}(введите /help для справки){RESET}")
    print(f"{DIM}провайдер: {provider}{RESET}\n")

    client       = _build_client(provider)

    # RAG: многоступенчатый пайплайн поверх FAISS-индекса.
    #   query rewrite → поиск (fetch_k) → порог min_score → реранк → top_k.
    # Базовый движок и пайплайн реализуют один порт RetrievalEngine, поэтому
    # build_system_prompt о ступенях не знает. Реранкеры/rewriter строятся здесь
    # (после client), т.к. LLM-варианты требуют клиент модели. temperature=0 —
    # чтобы вспомогательные вызовы были детерминированными.
    rag_config = load_rag_config()
    rag_base_engine = FaissOllamaRetrievalEngine(
        index_path  = rag_config.index_path,
        strategy    = rag_config.strategy,
        embed_model = DEFAULT_EMBED_MODEL,
        ollama_url  = DEFAULT_OLLAMA_URL,
    )
    aux_params = dict(params)
    aux_params["temperature"] = 0
    rag_engine = RetrievalPipeline(
        rag_base_engine,
        rag_config,
        rewriter=LLMQueryRewriter(client, aux_params),
        rerankers={
            "heuristic": HeuristicReranker(),
            "llm": LLMReranker(client, aux_params),
        },
    )
    if rag_config.enabled and not rag_engine.is_ready():
        rag_config.enabled = False  # индекс/зависимости недоступны — тихо в обычный режим

    # AI-ревью PR (/review): RAG по двум индексам сразу — документация + код.
    # Берём базовые движки (без rewrite/rerank): для разбора diff достаточно
    # прямого косинусного поиска, объединённого композитом.
    code_base_engine = FaissOllamaRetrievalEngine(
        index_path  = code_index_path(),
        strategy    = rag_config.strategy,
        embed_model = DEFAULT_EMBED_MODEL,
        ollama_url  = DEFAULT_OLLAMA_URL,
    )
    review_engine = CompositeRetrievalEngine([rag_base_engine, code_base_engine])
    diff_provider = GhDiffProvider()

    orchestrator = build_default_orchestrator(task_repo)

    # MCP: поднимаем все включённые серверы. Если ни одного — registry просто
    # пуст, и ToolRouter будет проксировать chat() без tool-loop.
    mcp_registry = StdioMcpRegistry(mcp_repo)
    if mcp_repo.list_all():
        with Spinner("Поднимаю MCP-серверы..."):
            mcp_registry.start_all()
        running = mcp_registry.clients()
        if running:
            tools_count = len(mcp_registry.all_tools())
            print(f"{DIM}MCP: запущено {len(running)} серверов, "
                  f"обнаружено {tools_count} тулов.{RESET}")
        for sid, err in mcp_registry.failures():
            print(f"{YELLOW}MCP[{sid}] не стартовал: {err}{RESET}")

    # Git-контекст для /help берётся через MCP-сервер git. Путь репозитория —
    # корень jarvis-cli (переопределяется JARVIS_REPO_PATH).
    repo_path = os.path.expanduser(
        os.environ.get("JARVIS_REPO_PATH", "").strip()
        or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    git_provider = McpGitContextProvider(mcp_registry, repo_path)

    # Встроенный источник файловых тулов: агент читает/ищет/пишет файлы проекта
    # прямо в tool-loop, без внешнего MCP-сервера. Sandbox-root по умолчанию —
    # текущий рабочий каталог (откуда запустили jarvis), поэтому можно открыть
    # терминал в любом проекте и подключить агента к нему. Переопределяется
    # JARVIS_FS_ROOT. Запись проходит через интерактивный confirm с цветным diff.
    fs_root = os.path.expanduser(
        os.environ.get("JARVIS_FS_ROOT", "").strip() or os.getcwd()
    )
    try:
        fs_client = LocalFilesystemClient(
            root    = fs_root,
            confirm = make_interactive_confirm(reader=read_input),
        )
        mcp_registry.register(fs_client)
        print(f"{DIM}Файловые тулы (fs) активны на {fs_root}.{RESET}")
    except Exception as e:
        print(f"{YELLOW}Файловые тулы не поднялись: {e}{RESET}")

    tool_router = ToolRouter(client, mcp_registry) \
        if provider == DEEPSEEK and mcp_registry.all_tools() else None
    if tool_router is not None:
        print(f"{DIM}Tool-loop активен (provider=deepseek).{RESET}\n")
    elif provider != DEEPSEEK and mcp_repo.list_all():
        print(f"{YELLOW}MCP-серверы настроены, но tool calling доступен только "
              f"для DeepSeek. Переключи /provider deepseek.{RESET}\n")

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
    if rag_config.enabled:
        rw = "on" if rag_config.rewrite else "off"
        print(f"{DIM}RAG: включён (индекс {rag_config.index_path}, "
              f"стратегия {rag_config.strategy}, top_k={rag_config.top_k}, "
              f"реранк={rag_config.reranker}, rewrite={rw}). "
              f"Настройки — /rag status, выключить — /rag off.{RESET}")
    elif rag_engine.is_ready():
        print(f"{DIM}RAG: индекс найден, но выключен. Включить — /rag on.{RESET}")
    print()

    # Гарантируем остановку MCP-подпроцессов даже при падении или Ctrl+C
    # во время работы тула.
    import atexit
    atexit.register(mcp_registry.shutdown)

    # Включаем bracketed paste, чтобы многострочный paste не отправлялся
    # на первом же \n — REPL увидит вставку как одно сообщение и подождёт
    # явный Enter после неё.
    enable_bracketed_paste()
    atexit.register(disable_bracketed_paste)

    while True:
        try:
            user_input = read_input(f"{BOLD}{CYAN}You:{RESET} ").strip()
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
                choose_model(params, provider, llm_client=client)
            elif cmd == "/provider":
                new_provider = choose_provider(provider)
                if new_provider != provider:
                    provider = new_provider
                    params["model"] = default_model_for(provider)
                    client = _build_client(provider)
                    tool_router = ToolRouter(client, mcp_registry) \
                        if provider == DEEPSEEK and mcp_registry.all_tools() else None
                    print(f"{GREEN}Провайдер переключён: {provider}{RESET}")
                    print(f"{DIM}модель сброшена на дефолт: {params['model']}{RESET}")
                    if tool_router is not None:
                        print(f"{DIM}Tool-loop активен.{RESET}")
                    elif mcp_registry.all_tools():
                        print(f"{YELLOW}Tool calling доступен только для DeepSeek — "
                              f"MCP-тулы временно отключены.{RESET}")
            elif cmd == "/local":
                if provider == OLLAMA:
                    print(f"{DIM}Уже на локальной модели (ollama).{RESET}")
                    choose_model(params, provider, llm_client=client)
                else:
                    provider = OLLAMA
                    client = _build_client(provider)
                    tool_router = None
                    installed = client.list_models() if hasattr(client, "list_models") else []
                    if installed:
                        params["model"] = installed[0]
                        print(f"{GREEN}Переключено на локальную модель: {params['model']}{RESET}")
                        if len(installed) > 1:
                            print(f"{DIM}Другие модели: {', '.join(installed[1:])}. "
                                  f"Сменить — /model{RESET}")
                    else:
                        params["model"] = default_model_for(OLLAMA)
                        print(f"{YELLOW}Ollama не отвечает или нет установленных моделей.{RESET}")
                        print(f"{DIM}Установить: ollama pull qwen2.5:14b{RESET}")
            elif cmd == "/temp":
                set_temperature(params)
            elif cmd == "/tokens":
                set_max_tokens(params)
            elif cmd == "/ctx":
                set_context_window(params)
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
            elif cmd.startswith("/mcp"):
                handle_mcp(user_input, mcp_repo, mcp_registry)
            elif cmd.startswith("/rag"):
                handle_rag(user_input, rag_config, rag_engine)
            elif cmd == "/profile new":
                current_profile = create_profile(profile_repo, current_profile)
            elif cmd == "/profile edit":
                current_profile = edit_profile(profile_repo, current_profile)
            elif cmd == "/profile delete":
                current_profile = delete_profile(profile_repo, current_profile)
            elif cmd == "/profile":
                current_profile = choose_profile(profile_repo, current_profile)
            elif cmd == "/clear":
                # Не удаляем файл сессии — он остаётся в истории (~/.jarvis/sessions),
                # и его можно выбрать при следующем запуске. Просто сбрасываем
                # in-memory диалог; следующее сообщение породит новый session_id.
                current_session_id = None
                messages.clear()
                print(CLEAR_SCREEN, end="")
                print(f"{BOLD}{GREEN}Jarvis CLI{RESET}  {DIM}(новая сессия, /help — справка){RESET}")
                print(f"{DIM}провайдер: {provider}{RESET}\n")
                print(f"{DIM}Краткосрочная память очищена. Старая сессия сохранена в истории.{RESET}")
            elif cmd == "/help" or cmd.startswith("/help "):
                handle_help(user_input, rag_engine, git_provider, client,
                            params, top_k=rag_config.top_k)
            elif cmd == "/review" or cmd.startswith("/review "):
                handle_review(user_input, review_engine, diff_provider, client,
                              params, top_k=rag_config.top_k)
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

        # Формируем system prompt из долговременной + рабочей памяти + инвариантов.
        # В RAG-режиме сюда же подмешивается найденный по вопросу контекст.
        system_prompt = build_system_prompt(
            current_profile.content if current_profile else None,
            wm,
            knowledge_repo,
            invariant_repo,
            retrieval_engine=rag_engine if rag_config.enabled else None,
            user_query=user_input,
            top_k=rag_config.top_k,
        )

        loop = None
        guarded = None
        reporter = ToolProgressReporter() if tool_router is not None else None
        try:
            if tool_router is not None:
                # ToolProgressReporter сам ведёт спиннер «Думаю...» и печатает
                # каждый tool_call вживую — внешний Spinner здесь не нужен.
                try:
                    loop = tool_router.chat(messages, params, system_prompt,
                                            on_event=reporter)
                finally:
                    reporter.stop()
            else:
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

        if loop is not None:
            # Цепочка tool_call'ов уже распечатана live через reporter,
            # повторно её показывать не нужно.
            reply = loop.reply
            if loop.truncated:
                print(f"{YELLOW}[!] tool-loop обрезан по лимиту итераций{RESET}")
            print(f"{BOLD}{GREEN}Agent:{RESET} {reply}")
        else:
            reply = guarded.reply
            print(f"{BOLD}{GREEN}Agent:{RESET} {reply}")
            announce_guard_result(guarded)

        # Краткосрочная память: сохраняем ответ ассистента
        messages.append({"role": "assistant", "content": reply})
        current_session_id = session_repo.save(current_session_id, messages, params)
        print()
