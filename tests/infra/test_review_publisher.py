"""Тест GhReviewPublisher: комментарий к PR через фейковый `gh`-раннер."""
from __future__ import annotations

from infra.review_publisher import GhReviewPublisher


class _FakeRun:
    def __init__(self):
        self.calls: list[tuple] = []

    def __call__(self, args, stdin=None):
        self.calls.append((args, stdin))
        return ""


def test_publish_posts_body_via_stdin():
    run = _FakeRun()
    GhReviewPublisher(run=run).publish("12", "## Ревью\nтело")

    args, stdin = run.calls[0]
    assert args == ["pr", "comment", "12", "--body-file", "-"]
    assert stdin == "## Ревью\nтело"


def test_publish_coerces_pr_to_str():
    run = _FakeRun()
    GhReviewPublisher(run=run).publish(9, "x")
    assert run.calls[0][0][2] == "9"
