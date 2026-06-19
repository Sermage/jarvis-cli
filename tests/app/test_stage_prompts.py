from domain.task import StageResult, StageStatus, Task, TaskState
from app.stage_prompts import (
    STAGE_ORDER,
    STAGE_PROMPTS,
    build_task_block,
    next_forward_state,
)


def test_stage_order_covers_all_non_aborted_states():
    assert STAGE_ORDER == [
        TaskState.INTAKE,
        TaskState.PLANNING,
        TaskState.EXECUTION,
        TaskState.VALIDATION,
        TaskState.DONE,
    ]


def test_stage_prompts_has_entry_for_each_active_stage():
    for state in (TaskState.INTAKE, TaskState.PLANNING, TaskState.EXECUTION, TaskState.VALIDATION):
        assert state in STAGE_PROMPTS


# ── next_forward_state ──────────────────────────────────────────────────────


def test_next_forward_state_walks_linearly():
    assert next_forward_state(TaskState.INTAKE)     == TaskState.PLANNING
    assert next_forward_state(TaskState.PLANNING)   == TaskState.EXECUTION
    assert next_forward_state(TaskState.EXECUTION)  == TaskState.VALIDATION
    assert next_forward_state(TaskState.VALIDATION) == TaskState.DONE


def test_next_forward_state_terminal_returns_none():
    assert next_forward_state(TaskState.DONE) is None


def test_next_forward_state_unknown_returns_none():
    assert next_forward_state("limbo") is None
    assert next_forward_state(TaskState.ABORTED) is None


# ── build_task_block ────────────────────────────────────────────────────────


def test_block_contains_header_request_and_stage():
    task = Task.new("Сделать рефакторинг", now="2026-06-19 10:00")
    block = build_task_block(task)
    assert f"[ЗАДАЧА #{task.id}: Сделать рефакторинг]" in block
    assert "Исходный запрос пользователя: Сделать рефакторинг" in block
    assert f"Текущая стадия: {TaskState.INTAKE}" in block


def test_block_includes_context_entries():
    task = Task.new("x")
    task.context["repo"]  = "jarvis"
    task.context["scope"] = "MVP"
    block = build_task_block(task)
    assert "Контекст задачи:" in block
    assert "  repo: jarvis" in block
    assert "  scope: MVP" in block


def test_block_includes_prior_stage_outputs_only_for_completed():
    task = Task.new("x")
    task.transition(TaskState.PLANNING)
    task.transition(TaskState.EXECUTION)
    task.stages[TaskState.INTAKE]   = StageResult(status=StageStatus.DONE, output="intake-итог")
    task.stages[TaskState.PLANNING] = StageResult(status=StageStatus.DONE, output="план")
    # текущая EXECUTION не должна попадать в "результат стадии"
    task.stages[TaskState.EXECUTION] = StageResult(status=StageStatus.IN_PROGRESS, output="не показывать")

    block = build_task_block(task)
    assert "Результат стадии intake" in block
    assert "intake-итог" in block
    assert "Результат стадии planning" in block
    assert "план" in block
    assert "не показывать" not in block


def test_block_includes_user_clarifications():
    task = Task.new("x")
    task.answers.append({"q": "стек?", "a": "python"})
    block = build_task_block(task)
    assert "Уточнения от пользователя" in block
    assert "Q: стек?" in block
    assert "A: python" in block


def test_block_includes_current_stage_instruction():
    task = Task.new("x")
    block = build_task_block(task)
    assert f"Инструкция для текущей стадии ({TaskState.INTAKE})" in block
    assert STAGE_PROMPTS[TaskState.INTAKE].split("\n", 1)[0] in block


def test_block_always_includes_question_protocol():
    task = Task.new("x")
    block = build_task_block(task)
    assert "Протокол уточнений" in block
    assert "[QUESTION]" in block


def test_block_restoration_hint_optional():
    task = Task.new("x")
    assert "Возобновление после перерыва" not in build_task_block(task)
    assert "Возобновление после перерыва" in build_task_block(task, restoration_hint=True)
