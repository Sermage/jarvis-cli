"""Роевой исполнитель стадии EXECUTION.

Три фазы:
  1) **Декомпозиция** — один LLM-вызов разбивает утверждённый план на
     независимые подзадачи с указанием роли воркера.
  2) **Параллельное выполнение** — воркеры по ролям обрабатывают подзадачи
     одновременно через `ThreadPoolExecutor`.
  3) **Слияние** — детерминированная сборка отчёта из результатов
     подзадач, без дополнительного LLM-вызова.

Если декомпозитор задаёт `[QUESTION]` — стадия зависает в `awaiting_user`
как обычно. Если декомпозитор не выделил ни одной подзадачи —
SwarmExecutorAgent падает обратно в одиночный прогон (как простой
`ExecutorAgent`), чтобы не залипнуть на стадии при кривом плане.

Подзадачи сохраняются в `stage.artifacts["subtasks"]` через
`AgentResult.extra_artifacts` — оркестратор сам сольёт их в стадию.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional, Protocol

from app.agents._prompting import build_full_prompt, call_llm
from app.agents.base import AgentContext, AgentResult
from app.invariant_guard import GuardedResult
from app.parsers import parse_questions
from domain.subtask import Subtask, SubtaskStatus, WorkerRole
from domain.task import Task, TaskState


# ── Декомпозитор ────────────────────────────────────────────────────────────


_DECOMPOSE_INSTRUCTION = (
    "СЕЙЧАС РЕЖИМ ДЕКОМПОЗИЦИИ.\n"
    "Разбей утверждённый план на 2–6 НЕЗАВИСИМЫХ подзадач, которые можно "
    "выполнять параллельно разными агентами. Каждой назначь роль:\n"
    "  coder      — написать/изменить код;\n"
    "  researcher — изучить материалы, собрать факты;\n"
    "  writer     — написать текст/документацию/описание;\n"
    "  tester     — спроектировать или написать проверки/тесты;\n"
    "  generic    — что-то другое (если ни одна роль не подходит).\n"
    "\n"
    "Формат вывода — по одной подзадаче на строку, СТРОГО так:\n"
    "  [SUBTASK role=<роль>] <краткое описание, одна строка>\n"
    "\n"
    "Не пиши ничего, кроме этих строк. Никаких заголовков, нумерации, "
    "комментариев между ними.\n"
    "\n"
    "Если для декомпозиции не хватает данных — задай уточняющий вопрос "
    "по протоколу уточнений (см. выше) и НЕ выводи [SUBTASK]."
)


# Якорим описание горизонтальными пробелами (не `\s`), чтобы `]` без описания
# на этой строке не «съедал» следующую строку как продолжение.
_SUBTASK_RE = re.compile(
    r"^[ \t]*\[SUBTASK[ \t]+role=([a-zA-Z_]+)\][ \t]+(.+?)[ \t]*$",
    re.MULTILINE,
)


def parse_subtasks(text: str) -> list[Subtask]:
    """Извлечь подзадачи из ответа декомпозитора."""
    out: list[Subtask] = []
    for role, desc in _SUBTASK_RE.findall(text):
        role_norm = role.strip().lower()
        if role_norm not in WorkerRole.ALL:
            role_norm = WorkerRole.GENERIC
        if desc.strip():
            out.append(Subtask.new(role_norm, desc))
    return out


@dataclass
class DecomposeResult:
    guarded:   GuardedResult
    reply:     str
    subtasks:  list[Subtask]
    questions: list[str]


class Decomposer:
    """Один LLM-вызов: план → список Subtask."""

    def decompose(self, task: Task, followup: str, ctx: AgentContext) -> DecomposeResult:
        system_prompt = build_full_prompt(task, ctx, extra_instruction=_DECOMPOSE_INSTRUCTION)
        guarded       = call_llm(ctx, system_prompt, user_message=followup)
        reply         = guarded.reply
        questions     = parse_questions(reply)
        subtasks      = [] if questions else parse_subtasks(reply)
        return DecomposeResult(guarded=guarded, reply=reply,
                               subtasks=subtasks, questions=questions)


# ── Воркеры ─────────────────────────────────────────────────────────────────


_ROLE_INSTRUCTIONS: dict[str, str] = {
    WorkerRole.CODER: (
        "Ты coder-воркер. Фокусируйся на коде: сигнатуры, файлы, конкретные "
        "блоки. Если показываешь код — оформляй блоками с указанием языка."
    ),
    WorkerRole.RESEARCHER: (
        "Ты researcher-воркер. Собери факты, ссылки, варианты. Делай "
        "лаконичные тезисы; помечай неуверенность словом «предположительно»."
    ),
    WorkerRole.WRITER: (
        "Ты writer-воркер. Пиши связный текст для людей: README, описание "
        "решения, комментарии. Никаких списков ради списков, нормальные абзацы."
    ),
    WorkerRole.TESTER: (
        "Ты tester-воркер. Спроектируй или напиши проверки: кейсы, граничные "
        "значения, ассерты. Если уместен код — оформляй блоками."
    ),
    WorkerRole.GENERIC: (
        "Ты generic-воркер. Выполни подзадачу прагматично, в свободной форме."
    ),
}


_WORKER_TAIL = (
    "\n\nОграничения для воркера:\n"
    " - НЕ задавай уточняющих вопросов; работай с тем, что есть, либо явно "
    "укажи в результате «не хватает данных: …».\n"
    " - НЕ повторяй описание подзадачи в начале ответа — сразу к результату.\n"
    " - Ответ должен помещаться в один-два экрана; будь конкретен."
)


class Worker(Protocol):
    role: str
    def execute(self, subtask: Subtask, task: Task, ctx: AgentContext) -> Subtask: ...


class _BaseWorker:
    """Воркер по умолчанию: системный промпт = базовый + роль + описание подзадачи."""
    role: str = WorkerRole.GENERIC

    def execute(self, subtask: Subtask, task: Task, ctx: AgentContext) -> Subtask:
        role_intro = _ROLE_INSTRUCTIONS.get(self.role, _ROLE_INSTRUCTIONS[WorkerRole.GENERIC])
        extra = (
            f"СЕЙЧАС РЕЖИМ ВОРКЕРА ({self.role}).\n"
            f"{role_intro}\n"
            f"{_WORKER_TAIL}\n"
            f"\nТВОЯ ПОДЗАДАЧА (id={subtask.id}):\n{subtask.description}"
        )
        system_prompt = build_full_prompt(task, ctx, extra_instruction=extra)
        try:
            guarded = call_llm(ctx, system_prompt, user_message="")
        except Exception as e:
            subtask.status = SubtaskStatus.FAILED
            subtask.error  = f"{type(e).__name__}: {e}"
            return subtask
        subtask.result = guarded.reply.strip()
        subtask.status = SubtaskStatus.DONE
        return subtask


class CoderWorker(_BaseWorker):      role = WorkerRole.CODER
class ResearcherWorker(_BaseWorker): role = WorkerRole.RESEARCHER
class WriterWorker(_BaseWorker):     role = WorkerRole.WRITER
class TesterWorker(_BaseWorker):     role = WorkerRole.TESTER
class GenericWorker(_BaseWorker):    role = WorkerRole.GENERIC


def build_default_workers() -> dict[str, Worker]:
    return {
        WorkerRole.CODER:      CoderWorker(),
        WorkerRole.RESEARCHER: ResearcherWorker(),
        WorkerRole.WRITER:     WriterWorker(),
        WorkerRole.TESTER:     TesterWorker(),
        WorkerRole.GENERIC:    GenericWorker(),
    }


# ── Слияние ─────────────────────────────────────────────────────────────────


def merge_subtask_results(subtasks: list[Subtask]) -> str:
    """Детерминированный отчёт: заголовок + секции по подзадачам в исходном порядке."""
    lines: list[str] = [
        f"Выполнение разбито на {len(subtasks)} подзадач (параллельно):",
        "",
    ]
    for i, st in enumerate(subtasks, 1):
        head = f"### {i}. [{st.role}] {st.description}"
        if st.status == SubtaskStatus.DONE:
            body = st.result or "(пустой результат)"
            lines.extend([head, "", body, ""])
        else:
            body = st.error or "(нет результата)"
            lines.extend([head + f"  — ⚠ {st.status}", "", body, ""])
    return "\n".join(lines).rstrip()


# ── SwarmExecutorAgent ──────────────────────────────────────────────────────


class SwarmExecutorAgent:
    """EXECUTION через рой: декомпозиция → параллельные воркеры → слияние."""
    stage = TaskState.EXECUTION

    def __init__(self,
                 decomposer: Optional[Decomposer] = None,
                 workers: Optional[dict[str, Worker]] = None,
                 max_parallel: int = 4):
        self._decomposer   = decomposer or Decomposer()
        self._workers      = dict(workers) if workers is not None else build_default_workers()
        self._max_parallel = max(1, max_parallel)
        if WorkerRole.GENERIC not in self._workers:
            self._workers[WorkerRole.GENERIC] = GenericWorker()

    def run(self, task: Task, followup_message: str, ctx: AgentContext) -> AgentResult:
        decomp = self._decomposer.decompose(task, followup_message, ctx)

        # 1) Декомпозитор просит уточнений — стадия зависает.
        if decomp.questions:
            return AgentResult(
                reply=decomp.reply, guarded=decomp.guarded,
                questions=decomp.questions,
            )

        # 2) План не разложился — fallback в одиночный прогон (как обычный ExecutorAgent).
        if not decomp.subtasks:
            return AgentResult(reply=decomp.reply, guarded=decomp.guarded)

        # 3) Параллельное выполнение воркерами.
        subtasks = decomp.subtasks
        for st in subtasks:
            st.status = SubtaskStatus.IN_PROGRESS

        # Шедулим в стабильном порядке, но await через as_completed для скорости;
        # порядок в финальном отчёте — исходный, не порядок завершения.
        with ThreadPoolExecutor(max_workers=self._max_parallel) as ex:
            futures = {
                ex.submit(self._dispatch, st, task, ctx): st
                for st in subtasks
            }
            for fut in as_completed(futures):
                # _dispatch уже мутирует Subtask в месте; исключения мы не
                # ожидаем (worker ловит свои), но на всякий случай страхуем.
                try:
                    fut.result()
                except Exception as e:
                    st = futures[fut]
                    st.status = SubtaskStatus.FAILED
                    st.error  = f"{type(e).__name__}: {e}"

        merged = merge_subtask_results(subtasks)
        artifacts = {"subtasks": [st.to_dict() for st in subtasks]}
        return AgentResult(
            reply=merged,
            guarded=decomp.guarded,  # нарушения декомпозитора уйдут в artifacts стадии
            extra_artifacts=artifacts,
        )

    def _dispatch(self, subtask: Subtask, task: Task, ctx: AgentContext) -> Subtask:
        worker = self._workers.get(subtask.role) or self._workers[WorkerRole.GENERIC]
        return worker.execute(subtask, task, ctx)
