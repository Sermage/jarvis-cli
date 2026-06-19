from domain.profile import Profile
from infra.profile_repository import FileProfileRepository


def _repo(tmp_path):
    return FileProfileRepository(dir_path=str(tmp_path / "profiles"))


def test_list_names_empty_when_dir_missing(tmp_path):
    assert _repo(tmp_path).list_names() == []


def test_save_and_load_round_trip(tmp_path):
    repo = _repo(tmp_path)
    profile = Profile(name="android-dev", content="# роль\nписать котлин\n")
    repo.save(profile)

    loaded = repo.load("android-dev")
    assert loaded is not None
    assert loaded.name == "android-dev"
    assert loaded.content == "# роль\nписать котлин"  # хвостовой \n обрезается


def test_load_returns_none_for_missing(tmp_path):
    assert _repo(tmp_path).load("nope") is None


def test_save_sanitizes_name_on_path(tmp_path):
    repo = _repo(tmp_path)
    repo.save(Profile(name="my profile/v2", content="x"))
    # Имя файла нормализуется на пути; вызов load с тем же сырым именем находит файл.
    assert repo.exists("my profile/v2")
    assert repo.load("my profile/v2") is not None


def test_list_names_sorted(tmp_path):
    repo = _repo(tmp_path)
    for n in ["beta", "alpha", "gamma"]:
        repo.save(Profile(name=n, content="x"))
    assert repo.list_names() == ["alpha", "beta", "gamma"]


def test_ensure_default_creates_when_missing(tmp_path):
    repo = _repo(tmp_path)
    profile = repo.ensure_default()
    assert profile.name == "default"
    assert "Jarvis" in profile.content
    assert repo.exists("default")


def test_ensure_default_keeps_existing(tmp_path):
    repo = _repo(tmp_path)
    repo.save(Profile(name="default", content="кастомное"))
    profile = repo.ensure_default()
    assert profile.content == "кастомное"


def test_delete_removes_file(tmp_path):
    repo = _repo(tmp_path)
    repo.save(Profile(name="x", content="y"))
    repo.delete("x")
    assert not repo.exists("x")


def test_delete_is_noop_for_missing(tmp_path):
    _repo(tmp_path).delete("nope")


def test_path_for_uses_sanitized_name(tmp_path):
    repo = _repo(tmp_path)
    assert repo.path_for("my profile").endswith("profiles/my-profile.md")
