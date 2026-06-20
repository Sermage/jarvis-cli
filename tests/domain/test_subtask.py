"""Чистые юнит-тесты доменной модели Subtask."""
from __future__ import annotations

from domain.subtask import Subtask, SubtaskStatus, WorkerRole


def test_new_assigns_id_and_trims_description():
    st = Subtask.new(WorkerRole.CODER, "  написать foo  ")
    assert st.role == WorkerRole.CODER
    assert st.description == "написать foo"
    assert st.status == SubtaskStatus.PENDING
    assert st.id and len(st.id) == 6


def test_roundtrip_to_from_dict():
    st = Subtask.new(WorkerRole.TESTER, "проверить bar")
    st.status = SubtaskStatus.DONE
    st.result = "OK"
    restored = Subtask.from_dict(st.to_dict())
    assert restored.id == st.id
    assert restored.role == st.role
    assert restored.description == st.description
    assert restored.status == SubtaskStatus.DONE
    assert restored.result == "OK"
    assert restored.error is None


def test_from_dict_defaults_role_to_generic_if_absent():
    restored = Subtask.from_dict({"id": "abc123", "description": "x"})
    assert restored.role == WorkerRole.GENERIC
    assert restored.status == SubtaskStatus.PENDING
