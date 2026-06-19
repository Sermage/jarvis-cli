import json

from domain.working_memory import WorkingMemory
from infra.working_memory_repository import FileWorkingMemoryRepository


def _repo(tmp_path, now="2026-06-19 10:00"):
    return FileWorkingMemoryRepository(
        file_path=str(tmp_path / "wm" / "current.json"),
        now=lambda: now,
    )


def test_load_returns_empty_when_file_missing(tmp_path):
    wm = _repo(tmp_path).load()
    assert wm.is_empty()
    assert wm.task is None


def test_save_creates_parent_dir_and_writes_json(tmp_path):
    repo = _repo(tmp_path)
    wm = WorkingMemory(task="t", context={"a": "1"}, notes=["n"])
    repo.save(wm)

    on_disk = json.loads((tmp_path / "wm" / "current.json").read_text(encoding="utf-8"))
    assert on_disk["task"] == "t"
    assert on_disk["context"] == {"a": "1"}
    assert on_disk["notes"] == ["n"]
    assert on_disk["created_at"] == "2026-06-19 10:00"
    assert on_disk["updated_at"] == "2026-06-19 10:00"


def test_save_preserves_created_at_and_updates_updated_at(tmp_path):
    times = iter(["2026-06-19 10:00", "2026-06-19 10:05"])
    repo = FileWorkingMemoryRepository(
        file_path=str(tmp_path / "current.json"),
        now=lambda: next(times),
    )
    wm = WorkingMemory(task="t")
    repo.save(wm)
    repo.save(wm)
    assert wm.created_at == "2026-06-19 10:00"
    assert wm.updated_at == "2026-06-19 10:05"


def test_round_trip_through_disk(tmp_path):
    repo = _repo(tmp_path)
    original = WorkingMemory(task="рефакторинг", context={"layer": "infra"}, notes=["wip"])
    repo.save(original)

    loaded = repo.load()
    assert loaded.task == "рефакторинг"
    assert loaded.context == {"layer": "infra"}
    assert loaded.notes == ["wip"]
    assert loaded.created_at == "2026-06-19 10:00"


def test_clear_removes_file(tmp_path):
    repo = _repo(tmp_path)
    repo.save(WorkingMemory(task="t"))
    repo.clear()
    assert repo.load().is_empty()


def test_clear_is_noop_when_file_missing(tmp_path):
    # Should not raise.
    _repo(tmp_path).clear()


def test_load_recovers_from_corrupt_json(tmp_path):
    path = tmp_path / "current.json"
    path.write_text("not json", encoding="utf-8")
    repo = FileWorkingMemoryRepository(file_path=str(path))
    assert repo.load().is_empty()
