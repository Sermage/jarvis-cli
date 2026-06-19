import json

import pytest

from domain.task import Task, TaskState, TaskTransitionError
from infra.task_repository import FileTaskRepository


def _repo(tmp_path, now="2026-06-19 10:00"):
    return FileTaskRepository(
        dir_path=str(tmp_path / "tasks"),
        now=lambda: now,
    )


# ── persistence ──────────────────────────────────────────────────────────────


def test_save_creates_dir_and_writes_json(tmp_path):
    repo = _repo(tmp_path)
    task = Task.new("исследовать API", now="2026-06-19 09:00")
    repo.save(task)

    on_disk = json.loads(open(tmp_path / "tasks" / f"{task.id}.json").read())
    assert on_disk["id"] == task.id
    assert on_disk["state"] == TaskState.INTAKE
    # save должен обновить updated_at часами репозитория.
    assert on_disk["updated_at"] == "2026-06-19 10:00"
    assert task.updated_at == "2026-06-19 10:00"


def test_load_returns_none_for_missing(tmp_path):
    assert _repo(tmp_path).load("nope") is None


def test_load_returns_none_for_corrupt(tmp_path):
    repo = _repo(tmp_path)
    (tmp_path / "tasks").mkdir()
    (tmp_path / "tasks" / "abc.json").write_text("not json", encoding="utf-8")
    assert repo.load("abc") is None


def test_round_trip_through_disk(tmp_path):
    repo = _repo(tmp_path)
    task = Task.new("рефакторинг")
    task.context["repo"] = "jarvis-cli"
    task.pending_questions.append("какой стек?")
    repo.save(task)

    loaded = repo.load(task.id)
    assert loaded is not None
    assert loaded.id == task.id
    assert loaded.context == {"repo": "jarvis-cli"}
    assert loaded.pending_questions == ["какой стек?"]


def test_list_all_sorted_by_updated_at_desc(tmp_path):
    seq = iter(["2026-06-19 09:00", "2026-06-19 10:00", "2026-06-19 11:00"])
    repo = FileTaskRepository(
        dir_path=str(tmp_path / "tasks"),
        now=lambda: next(seq),
    )
    t1 = Task.new("первая")
    t2 = Task.new("вторая")
    t3 = Task.new("третья")
    repo.save(t1)
    repo.save(t2)
    repo.save(t3)

    listing = repo.list_all()
    assert [t.title for t in listing] == ["третья", "вторая", "первая"]


def test_list_all_empty_when_dir_missing(tmp_path):
    assert _repo(tmp_path).list_all() == []


def test_list_all_skips_corrupt_files(tmp_path):
    repo = _repo(tmp_path)
    repo.save(Task.new("живая"))
    (tmp_path / "tasks" / "bad.json").write_text("oops", encoding="utf-8")
    assert len(repo.list_all()) == 1


# ── delete + active pointer ──────────────────────────────────────────────────


def test_delete_removes_file(tmp_path):
    repo = _repo(tmp_path)
    task = Task.new("x")
    repo.save(task)
    repo.delete(task)
    assert repo.load(task.id) is None


def test_delete_is_noop_for_unsaved(tmp_path):
    repo = _repo(tmp_path)
    repo.delete(Task.new("x"))  # должно молча отработать


def test_delete_clears_active_when_it_was_the_active(tmp_path):
    repo = _repo(tmp_path)
    task = Task.new("x")
    repo.save(task)
    repo.set_active(task)
    repo.delete(task)
    assert repo.get_active_id() is None


def test_delete_preserves_other_active(tmp_path):
    repo = _repo(tmp_path)
    a = Task.new("a")
    b = Task.new("b")
    repo.save(a); repo.save(b)
    repo.set_active(a)
    repo.delete(b)
    assert repo.get_active_id() == a.id


def test_active_pointer_round_trip(tmp_path):
    repo = _repo(tmp_path)
    task = Task.new("x")
    repo.save(task)
    repo.set_active(task)
    assert repo.get_active_id() == task.id
    loaded = repo.get_active()
    assert loaded is not None and loaded.id == task.id


def test_get_active_returns_none_when_no_pointer(tmp_path):
    repo = _repo(tmp_path)
    assert repo.get_active_id() is None
    assert repo.get_active() is None


def test_clear_active_removes_pointer(tmp_path):
    repo = _repo(tmp_path)
    task = Task.new("x")
    repo.save(task)
    repo.set_active(task)
    repo.clear_active()
    assert repo.get_active_id() is None


def test_clear_active_is_noop_when_missing(tmp_path):
    _repo(tmp_path).clear_active()  # не должно падать


def test_get_active_returns_none_when_target_deleted(tmp_path):
    """Активный указатель есть, но файл задачи удалён руками."""
    repo = _repo(tmp_path)
    task = Task.new("x")
    repo.save(task)
    repo.set_active(task)
    import os
    os.remove(tmp_path / "tasks" / f"{task.id}.json")
    assert repo.get_active_id() == task.id
    assert repo.get_active() is None


# ── transition (mutate + save) ───────────────────────────────────────────────


def test_transition_persists_new_state(tmp_path):
    repo = _repo(tmp_path)
    task = Task.new("x")
    repo.save(task)
    repo.transition(task, TaskState.PLANNING, reason="готов планировать")

    loaded = repo.load(task.id)
    assert loaded.state == TaskState.PLANNING
    assert loaded.transitions[-1]["from"] == TaskState.INTAKE
    assert loaded.transitions[-1]["to"]   == TaskState.PLANNING
    assert loaded.transitions[-1]["reason"] == "готов планировать"


def test_transition_rejects_forbidden_jump_and_does_not_save(tmp_path):
    repo = _repo(tmp_path)
    task = Task.new("x")
    repo.save(task)
    with pytest.raises(TaskTransitionError):
        repo.transition(task, TaskState.DONE)
    # На диске состояние не изменилось.
    assert repo.load(task.id).state == TaskState.INTAKE
