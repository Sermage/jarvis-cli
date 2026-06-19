import json

import pytest

from infra.session_repository import FileSessionRepository


def _repo(tmp_path, max_sessions=20, now_id_seq=None, now_label="2026-06-19 10:00"):
    seq = iter(now_id_seq) if now_id_seq else None
    return FileSessionRepository(
        dir_path=str(tmp_path / "sessions"),
        max_sessions=max_sessions,
        now_id=(lambda: next(seq)) if seq else (lambda: "2026-06-19T10-00-00"),
        now_label=lambda: now_label,
    )


def test_save_creates_dir_and_returns_new_id(tmp_path):
    repo = _repo(tmp_path)
    session_id = repo.save(None, [{"role": "user", "content": "привет"}], {"model": "GigaChat"})
    assert session_id == "2026-06-19T10-00-00"

    path = repo.path_for(session_id)
    data = json.loads(open(path, encoding="utf-8").read())
    assert data["title"] == "привет"
    assert data["model"] == "GigaChat"
    assert data["messages"][0]["content"] == "привет"
    assert data["updated_at"] == "2026-06-19 10:00"


def test_save_reuses_id_when_provided(tmp_path):
    repo = _repo(tmp_path)
    sid = repo.save(None, [{"role": "user", "content": "x"}], {"model": "m"})
    same_sid = repo.save(sid, [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}], {"model": "m"})
    assert sid == same_sid

    data = json.loads(open(repo.path_for(sid), encoding="utf-8").read())
    assert len(data["messages"]) == 2


def test_save_handles_empty_messages_for_title(tmp_path):
    repo = _repo(tmp_path)
    sid = repo.save(None, [], {"model": "m"})
    data = json.loads(open(repo.path_for(sid), encoding="utf-8").read())
    assert data["title"] == ""


def test_title_truncates_to_60_chars_and_strips_newlines(tmp_path):
    repo = _repo(tmp_path)
    content = ("a" * 30) + "\n" + ("b" * 50)
    sid = repo.save(None, [{"role": "user", "content": content}], {"model": "m"})
    data = json.loads(open(repo.path_for(sid), encoding="utf-8").read())
    assert len(data["title"]) == 60
    assert "\n" not in data["title"]


def test_list_all_returns_newest_first(tmp_path):
    repo = _repo(tmp_path, now_id_seq=[
        "2026-06-19T09-00-00",
        "2026-06-19T10-00-00",
        "2026-06-19T11-00-00",
    ])
    repo.save(None, [{"role": "user", "content": "first"}], {"model": "m"})
    repo.save(None, [{"role": "user", "content": "second"}], {"model": "m"})
    repo.save(None, [{"role": "user", "content": "third"}], {"model": "m"})

    listing = repo.list_all()
    assert [s["title"] for s in listing] == ["third", "second", "first"]
    assert listing[0]["id"] == "2026-06-19T11-00-00"
    assert listing[0]["count"] == 1


def test_list_all_skips_corrupt_files(tmp_path):
    repo = _repo(tmp_path)
    repo.save(None, [{"role": "user", "content": "ok"}], {"model": "m"})
    (tmp_path / "sessions" / "broken.json").write_text("not json", encoding="utf-8")
    listing = repo.list_all()
    assert len(listing) == 1
    assert listing[0]["title"] == "ok"


def test_list_all_returns_empty_when_dir_missing(tmp_path):
    repo = _repo(tmp_path)
    assert repo.list_all() == []


def test_delete_removes_file(tmp_path):
    repo = _repo(tmp_path)
    sid = repo.save(None, [{"role": "user", "content": "x"}], {"model": "m"})
    repo.delete(sid)
    assert repo.list_all() == []


def test_delete_is_noop_for_missing(tmp_path):
    repo = _repo(tmp_path)
    repo.delete("nonexistent")  # должно молча проигнорировать


def test_prune_keeps_only_max_sessions(tmp_path):
    repo = _repo(tmp_path, max_sessions=2, now_id_seq=[
        "2026-06-19T09-00-00",
        "2026-06-19T10-00-00",
        "2026-06-19T11-00-00",
        "2026-06-19T12-00-00",
    ])
    for i in range(4):
        repo.save(None, [{"role": "user", "content": f"m{i}"}], {"model": "m"})

    listing = repo.list_all()
    ids = [s["id"] for s in listing]
    assert ids == ["2026-06-19T12-00-00", "2026-06-19T11-00-00"]


def test_path_for_uses_dir(tmp_path):
    repo = _repo(tmp_path)
    assert repo.path_for("abc").endswith("sessions/abc.json")
