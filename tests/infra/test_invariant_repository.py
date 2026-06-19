import json
import os

from domain.invariant import Invariant, InvariantSeverity
from infra.invariant_repository import FileInvariantRepository


def _repo(tmp_path):
    return FileInvariantRepository(dir_path=str(tmp_path / "invariants"))


def test_list_ids_empty_when_dir_missing(tmp_path):
    assert _repo(tmp_path).list_ids() == []
    assert _repo(tmp_path).load_all().is_empty()


def test_save_and_load_round_trip(tmp_path):
    repo = _repo(tmp_path)
    inv = Invariant(
        id="kotlin-only",
        title="Стек — Kotlin",
        rule="бэк и фронт на Kotlin",
        severity=InvariantSeverity.BLOCK,
        forbidden_patterns=(r"\bJava\b", "AsyncTask"),
        required_patterns=("Kotlin",),
    )
    repo.save(inv)
    loaded = repo.load("kotlin-only")
    assert loaded is not None
    assert loaded.id == "kotlin-only"
    assert loaded.title == "Стек — Kotlin"
    assert loaded.severity is InvariantSeverity.BLOCK
    assert loaded.forbidden_patterns == (r"\bJava\b", "AsyncTask")
    assert loaded.required_patterns == ("Kotlin",)
    assert loaded.enabled is True


def test_list_ids_sorted(tmp_path):
    repo = _repo(tmp_path)
    for i in ["gamma", "alpha", "beta"]:
        repo.save(Invariant(id=i, title=i, rule="r"))
    assert repo.list_ids() == ["alpha", "beta", "gamma"]


def test_delete_removes_file(tmp_path):
    repo = _repo(tmp_path)
    repo.save(Invariant(id="x", title="X", rule="r"))
    assert repo.exists("x")
    repo.delete("x")
    assert not repo.exists("x")
    assert repo.load("x") is None
    # Удаление несуществующего — не падает.
    repo.delete("x")


def test_load_returns_none_for_missing(tmp_path):
    assert _repo(tmp_path).load("nope") is None


def test_load_all_skips_broken_json(tmp_path):
    repo = _repo(tmp_path)
    repo.save(Invariant(id="ok", title="ok", rule="r"))
    # Подложим битый файл.
    os.makedirs(str(tmp_path / "invariants"), exist_ok=True)
    with open(str(tmp_path / "invariants" / "broken.json"), "w") as f:
        f.write("{ not valid json")
    inv_set = repo.load_all()
    ids = {i.id for i in inv_set.items}
    assert ids == {"ok"}


def test_save_id_is_sanitized_on_disk(tmp_path):
    repo = _repo(tmp_path)
    repo.save(Invariant(id="weird_ID", title="t", rule="r"))
    # Файл должен лежать с нормализованным id.
    assert os.path.exists(str(tmp_path / "invariants" / "weird-id.json"))


def test_severity_warn_round_trips(tmp_path):
    repo = _repo(tmp_path)
    repo.save(Invariant(id="x", title="x", rule="r",
                        severity=InvariantSeverity.WARN))
    assert repo.load("x").severity is InvariantSeverity.WARN


def test_manual_json_loads_correctly(tmp_path):
    # Эмулируем ручное редактирование файла пользователем.
    d = tmp_path / "invariants"
    os.makedirs(d, exist_ok=True)
    payload = {
        "id": "no-rxjava",
        "title": "без RxJava",
        "rule": "не использовать RxJava",
        "severity": "block",
        "enabled": True,
        "forbidden_patterns": ["RxJava"],
        "required_patterns": [],
    }
    (d / "no-rxjava.json").write_text(json.dumps(payload, ensure_ascii=False),
                                       encoding="utf-8")
    repo = FileInvariantRepository(dir_path=str(d))
    inv = repo.load("no-rxjava")
    assert inv is not None
    assert inv.title == "без RxJava"
    assert inv.forbidden_patterns == ("RxJava",)


def test_unknown_severity_falls_back_to_block(tmp_path):
    d = tmp_path / "invariants"
    os.makedirs(d, exist_ok=True)
    (d / "x.json").write_text(json.dumps({
        "id": "x", "title": "x", "rule": "r", "severity": "explode",
    }), encoding="utf-8")
    repo = FileInvariantRepository(dir_path=str(d))
    assert repo.load("x").severity is InvariantSeverity.BLOCK
