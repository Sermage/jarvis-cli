"""CLI-обработчик /task — создание, просмотр, переходы стадий."""
from __future__ import annotations

from typing import Optional

from app.ports import (
    GigaChatClient,
    InvariantRepository,
    KnowledgeRepository,
    TaskRepository,
)
from app.stage_prompts import next_forward_state
from app.task_driver import advance_task
from cli.ansi import BOLD, CYAN, DIM, GREEN, RESET, YELLOW
from cli.spinner import Spinner
from cli.views import show_task
from domain.profile import Profile
from domain.task import Task, TaskState, TaskTransitionError
from domain.working_memory import WorkingMemory


_YES = {"y", "yes", "да", "д"}


def handle_task(cmd_str: str,
                params: dict,
                current_profile: Optional[Profile],
                wm: WorkingMemory,
                client: GigaChatClient,
                task_repo: TaskRepository,
                knowledge_repo: KnowledgeRepository,
                invariant_repo: Optional[InvariantRepository] = None) -> None:
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
                reply = advance_task(task, request, params, profile_text, wm,
                                     client, task_repo, knowledge_repo,
                                     invariant_repo=invariant_repo)
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
