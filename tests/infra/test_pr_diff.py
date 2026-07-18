"""Тесты GhDiffProvider: diff и файлы PR через фейковый `gh`-раннер."""
from __future__ import annotations

from infra.pr_diff import GhDiffProvider


class _FakeRun:
    """Фейк infra.gh.run_gh: возвращает заранее заданный вывод по под-команде."""

    def __init__(self, diff="", files_json="{}"):
        self._diff = diff
        self._files_json = files_json
        self.calls: list[list[str]] = []

    def __call__(self, args, stdin=None):
        self.calls.append(args)
        if args[:2] == ["pr", "diff"]:
            return self._diff
        if args[:2] == ["pr", "view"]:
            return self._files_json
        raise AssertionError(f"неожиданный вызов gh: {args}")


def test_fetch_returns_diff_and_files():
    run = _FakeRun(
        diff="diff --git a/x b/x\n+hello",
        files_json='{"files":[{"path":"a.py"},{"path":"b.py"}]}',
    )
    pr = GhDiffProvider(run=run).fetch("12")

    assert pr.number == "12"
    assert pr.diff == "diff --git a/x b/x\n+hello"
    assert pr.files == ["a.py", "b.py"]
    assert run.calls == [["pr", "diff", "12"], ["pr", "view", "12", "--json", "files"]]


def test_fetch_tolerates_empty_files_json():
    run = _FakeRun(diff="", files_json="")
    pr = GhDiffProvider(run=run).fetch("7")
    assert pr.files == [] and pr.is_empty()


def test_fetch_coerces_int_pr_to_str():
    run = _FakeRun(diff="d", files_json='{"files":[]}')
    pr = GhDiffProvider(run=run).fetch(5)
    assert pr.number == "5"
    assert run.calls[0] == ["pr", "diff", "5"]
