import pytest

from domain.task import (
    StageResult,
    StageStatus,
    Task,
    TaskState,
    TaskTransitionError,
)


# ── factory ──────────────────────────────────────────────────────────────────


def test_new_assigns_intake_state_and_metadata():
    task = Task.new("Сделать рефакторинг", profile="prof", model="GigaChat", now="2026-06-19 10:00")
    assert task.state == TaskState.INTAKE
    assert task.request == "Сделать рефакторинг"
    assert task.title == "Сделать рефакторинг"
    assert task.profile_snapshot == "prof"
    assert task.model_snapshot == "GigaChat"
    assert task.created_at == "2026-06-19 10:00"
    assert task.updated_at == "2026-06-19 10:00"
    assert len(task.id) == 8


def test_new_title_is_first_line_truncated():
    long_first_line = "x" * 80
    task = Task.new(f"{long_first_line}\nsecond line")
    assert task.title == "x" * 60


def test_new_title_falls_back_for_empty_request():
    task = Task.new("   ")
    assert task.title == "—"


def test_new_ids_are_unique():
    ids = {Task.new(f"req {i}").id for i in range(50)}
    assert len(ids) == 50


# ── state machine ───────────────────────────────────────────────────────────


def test_can_transition_follows_allowed_table():
    task = Task.new("x")
    assert task.can_transition(TaskState.PLANNING)
    assert task.can_transition(TaskState.ABORTED)
    assert not task.can_transition(TaskState.EXECUTION)
    assert not task.can_transition(TaskState.DONE)


def test_transition_records_history_and_changes_state():
    task = Task.new("x")
    task.transition(TaskState.PLANNING, reason="готов планировать", at="2026-06-19 11:00")
    assert task.state == TaskState.PLANNING
    assert task.transitions == [{
        "from":   TaskState.INTAKE,
        "to":     TaskState.PLANNING,
        "at":     "2026-06-19 11:00",
        "reason": "готов планировать",
    }]


def test_transition_rejects_unknown_state():
    task = Task.new("x")
    with pytest.raises(TaskTransitionError, match="Неизвестное состояние"):
        task.transition("limbo")


def test_transition_rejects_forbidden_jump():
    task = Task.new("x")
    with pytest.raises(TaskTransitionError, match="Запрещённый переход"):
        task.transition(TaskState.DONE)


def test_validation_cannot_roll_back_to_planning():
    """Инвариант: после planning всегда был хотя бы один заход в execution."""
    task = Task.new("x")
    task.transition(TaskState.PLANNING)
    task.transition(TaskState.EXECUTION)
    task.transition(TaskState.VALIDATION)
    with pytest.raises(TaskTransitionError):
        task.transition(TaskState.PLANNING)


def test_terminal_states_cannot_transition():
    task = Task.new("x")
    task.transition(TaskState.ABORTED)
    assert task.is_terminal()
    with pytest.raises(TaskTransitionError):
        task.transition(TaskState.INTAKE)


def test_is_terminal_recognises_done_and_aborted():
    task = Task.new("x")
    assert not task.is_terminal()
    task.transition(TaskState.PLANNING)
    task.transition(TaskState.EXECUTION)
    task.transition(TaskState.VALIDATION)
    task.transition(TaskState.DONE)
    assert task.is_terminal()


# ── serialization ───────────────────────────────────────────────────────────


def test_round_trip_serialization_preserves_fields():
    task = Task.new("исследовать API", profile="p", model="m", now="2026-06-19 10:00")
    task.transition(TaskState.PLANNING, reason="ok", at="2026-06-19 10:01")
    task.context["repo"] = "jarvis"
    task.pending_questions.append("какой стек?")
    task.answers.append("python")
    task.stages["intake"] = StageResult(status=StageStatus.DONE, output="hello")

    restored = Task.from_dict(task.to_dict())

    assert restored.id == task.id
    assert restored.state == TaskState.PLANNING
    assert restored.context == {"repo": "jarvis"}
    assert restored.pending_questions == ["какой стек?"]
    assert restored.answers == ["python"]
    assert restored.transitions == task.transitions
    assert isinstance(restored.stages["intake"], StageResult)
    assert restored.stages["intake"].status == StageStatus.DONE
    assert restored.stages["intake"].output == "hello"


def test_from_dict_tolerates_missing_optional_fields():
    minimal = {"id": "abc12345"}
    task = Task.from_dict(minimal)
    assert task.id == "abc12345"
    assert task.state == TaskState.INTAKE
    assert task.context == {}
    assert task.transitions == []
    assert task.stages == {}


def test_stage_result_round_trip():
    sr = StageResult(
        status=StageStatus.AWAITING_USER,
        output="нужны уточнения",
        artifacts={"questions": ["q1"]},
        started_at="2026-06-19 10:00",
        finished_at=None,
    )
    restored = StageResult.from_dict(sr.to_dict())
    assert restored.status == StageStatus.AWAITING_USER
    assert restored.output == "нужны уточнения"
    assert restored.artifacts == {"questions": ["q1"]}
    assert restored.started_at == "2026-06-19 10:00"
    assert restored.finished_at is None


# ── independence guarantee ──────────────────────────────────────────────────


def test_default_collections_are_not_shared_between_instances():
    a = Task.new("a")
    b = Task.new("b")
    a.context["k"] = "v"
    a.transitions.append({"foo": "bar"})
    assert b.context == {}
    assert b.transitions == []
