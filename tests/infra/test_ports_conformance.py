"""Проверяем, что инфраструктурные реализации удовлетворяют портам.

isinstance с Protocol работает только с runtime_checkable, поэтому здесь
явно проверяем наличие методов с нужной сигнатурой через структурный
дакфайл-чек (атрибуты вызываемы).
"""
import inspect

from app.ports import (
    GigaChatClient,
    SessionRepository,
    TaskRepository,
    WorkingMemoryRepository,
)
from infra.gigachat_client import RequestsGigaChatClient
from infra.session_repository import FileSessionRepository
from infra.task_repository import FileTaskRepository
from infra.working_memory_repository import FileWorkingMemoryRepository


def _methods(proto) -> set[str]:
    return {
        name
        for name, member in inspect.getmembers(proto)
        if not name.startswith("_") and callable(member)
    }


def test_file_working_memory_repository_covers_port(tmp_path):
    repo = FileWorkingMemoryRepository(file_path=str(tmp_path / "wm.json"))
    for name in _methods(WorkingMemoryRepository):
        assert callable(getattr(repo, name)), f"missing method: {name}"


def test_file_session_repository_covers_port(tmp_path):
    repo = FileSessionRepository(dir_path=str(tmp_path / "s"))
    for name in _methods(SessionRepository):
        assert callable(getattr(repo, name)), f"missing method: {name}"


def test_requests_gigachat_client_covers_port():
    client = RequestsGigaChatClient(
        auth_key="k", oauth_url="u", chat_url="c", scope="s",
    )
    for name in _methods(GigaChatClient):
        assert callable(getattr(client, name)), f"missing method: {name}"


def test_file_task_repository_covers_port(tmp_path):
    repo = FileTaskRepository(dir_path=str(tmp_path / "tasks"))
    for name in _methods(TaskRepository):
        assert callable(getattr(repo, name)), f"missing method: {name}"
