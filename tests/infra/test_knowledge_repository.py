from domain.knowledge import KnowledgeEntry
from infra.knowledge_repository import FileKnowledgeRepository


def _repo(tmp_path, now="2026-06-19 10:00"):
    return FileKnowledgeRepository(
        dir_path=str(tmp_path / "knowledge"),
        now=lambda: now,
    )


def test_list_names_empty_when_dir_missing(tmp_path):
    assert _repo(tmp_path).list_names() == []


def test_save_stamps_with_now_and_round_trips(tmp_path):
    repo = _repo(tmp_path)
    entry = KnowledgeEntry(name="api", content="key=value")
    repo.save(entry)
    # save должен проставить saved_at часами репозитория.
    assert entry.saved_at == "2026-06-19 10:00"

    loaded = repo.load("api")
    assert loaded is not None
    assert loaded.content == "key=value"
    assert loaded.saved_at == "2026-06-19 10:00"


def test_save_preserves_explicit_saved_at(tmp_path):
    repo = _repo(tmp_path)
    repo.save(KnowledgeEntry(name="x", content="c", saved_at="2025-01-01 00:00"))
    assert repo.load("x").saved_at == "2025-01-01 00:00"


def test_list_names_returns_sanitized_basenames_sorted(tmp_path):
    repo = _repo(tmp_path)
    for n in ["beta", "alpha", "gamma"]:
        repo.save(KnowledgeEntry(name=n, content="x"))
    assert repo.list_names() == ["alpha", "beta", "gamma"]


def test_load_returns_none_for_missing(tmp_path):
    assert _repo(tmp_path).load("nope") is None


def test_save_sanitizes_name(tmp_path):
    repo = _repo(tmp_path)
    repo.save(KnowledgeEntry(name="my notes/v2", content="x"))
    assert repo.load("my notes/v2") is not None


def test_all_as_prompt_concatenates_entries(tmp_path):
    repo = _repo(tmp_path)
    repo.save(KnowledgeEntry(name="b", content="второе"))
    repo.save(KnowledgeEntry(name="a", content="первое"))

    text = repo.all_as_prompt()
    # Сортировка по имени — a раньше b.
    assert "### a\nпервое" in text
    assert "### b\nвторое" in text
    assert text.index("### a") < text.index("### b")


def test_all_as_prompt_empty_when_no_entries(tmp_path):
    assert _repo(tmp_path).all_as_prompt() == ""


def test_all_as_prompt_omits_saved_at_marker(tmp_path):
    repo = _repo(tmp_path)
    repo.save(KnowledgeEntry(name="x", content="данные"))
    text = repo.all_as_prompt()
    assert "сохранено" not in text  # маркер не должен попасть в prompt
    assert "данные" in text
