"""Печатающие UI-функции CLI: статусы, дашборды, развёрнутый просмотр объектов.

Здесь только вывод. Никакой бизнес-логики, чтения с диска или сети —
все нужные данные принимаются через параметры (или через инжектированные
репозитории, которые сами знают свой путь).
"""
from __future__ import annotations

from typing import Optional

from app.invariant_guard import GuardedResult
from app.ports import KnowledgeRepository, SessionRepository, TaskRepository
from app.stage_prompts import STAGE_ORDER
from cli.ansi import BLUE, BOLD, CYAN, DIM, GREEN, MAGENTA, RESET, YELLOW
from cli.config import MODELS
from domain.invariant import InvariantSeverity
from domain.profile import Profile
from domain.task import Task, TaskState
from domain.working_memory import WorkingMemory


# ── invariant violations ────────────────────────────────────────────────────


def announce_guard_result(result: GuardedResult) -> None:
    """Печать предупреждений/блокировок от InvariantGuard, если есть."""
    if not result.violations and not result.blocked:
        return
    if result.blocked:
        print(f"{YELLOW}⚠ Ответ нарушает инварианты даже после "
              f"{result.retries_used} попытки/попыток переделать:{RESET}")
    elif result.retries_used:
        print(f"{DIM}  (модель переделала ответ {result.retries_used} раз — "
              f"первоначальный ответ нарушал инварианты){RESET}")
    elif any(v.severity is InvariantSeverity.WARN for v in result.violations):
        print(f"{YELLOW}⚠ В ответе есть нарушения warn-инвариантов:{RESET}")
    for v in result.violations:
        sev = "block" if v.severity is InvariantSeverity.BLOCK else "warn"
        print(f"    {DIM}-{RESET} [{sev}] {v.invariant_id}: {v.reason}")


# ── working memory ──────────────────────────────────────────────────────────


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


# ── tasks ───────────────────────────────────────────────────────────────────


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


def show_task(task: Task) -> None:
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
        for s in STAGE_ORDER:
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


def task_status_badge(task_repo: TaskRepository) -> str:
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


# ── settings + memory dashboards ────────────────────────────────────────────


def print_settings(params: dict, current_profile: Optional[Profile]) -> None:
    temp  = params["temperature"] if params["temperature"] is not None else "auto"
    maxt  = params["max_tokens"]  if params["max_tokens"]  is not None else "auto"
    pname = current_profile.name if current_profile else "нет"
    print(f"{DIM}  модель: {params['model']}  temperature: {temp}  max_tokens: {maxt}  профиль: {pname}{RESET}")


def print_memory_status(messages: list,
                        wm: WorkingMemory,
                        task_repo: TaskRepository,
                        current_profile: Optional[Profile],
                        knowledge_repo: KnowledgeRepository) -> None:
    """Однострочный дашборд всех слоёв памяти + активная задача."""
    st_label = f"{GREEN}краткосрочная: {len(messages)} сообщ.{RESET}" if messages \
               else f"{DIM}краткосрочная: —{RESET}"
    wm_label = wm_status_badge(wm)
    pname    = current_profile.name if current_profile else "нет"
    k_count  = len(knowledge_repo.list_names())
    lt_label = f"{BLUE}долговременная: {pname}"
    if k_count:
        lt_label += f", {k_count} знаний"
    lt_label += RESET
    task_label = task_status_badge(task_repo)
    print(f"  {st_label}  │  {wm_label}  │  {lt_label}  │  {task_label}")


def print_mem_detail(messages: list,
                     wm: WorkingMemory,
                     session_id: Optional[str],
                     session_repo: SessionRepository,
                     task_repo: TaskRepository,
                     current_profile: Optional[Profile],
                     knowledge_repo: KnowledgeRepository) -> None:
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


# ── help ────────────────────────────────────────────────────────────────────


def print_help() -> None:
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

{BOLD}{MAGENTA}Инварианты (/inv):{RESET}
  {CYAN}/inv list{RESET}        — список нерушимых ограничений проекта
  {CYAN}/inv show <id>{RESET}   — показать инвариант полностью
  {CYAN}/inv add  <id>{RESET}   — создать новый (с шагом редактирования в editor)
  {CYAN}/inv edit <id>{RESET}   — открыть JSON-файл в $EDITOR
  {CYAN}/inv rm   <id>{RESET}   — удалить (block — с подтверждением)

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
