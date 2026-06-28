"""Проверяем, что инфраструктурные реализации удовлетворяют портам.

isinstance с Protocol работает только с runtime_checkable, поэтому здесь
явно проверяем наличие методов с нужной сигнатурой через структурный
дакфайл-чек (атрибуты вызываемы).
"""
import inspect

from app.ports import (
    KnowledgeRepository,
    LLMClient,
    McpConfigRepository,
    McpRegistry,
    ProfileRepository,
    SessionRepository,
    TaskRepository,
    ToolCallingLLMClient,
    WorkingMemoryRepository,
)
from infra.deepseek_client import DeepSeekClient
from infra.gigachat_client import RequestsGigaChatClient
from infra.knowledge_repository import FileKnowledgeRepository
from infra.mcp_config_repository import FileMcpConfigRepository
from infra.mcp_registry import StdioMcpRegistry
from infra.profile_repository import FileProfileRepository
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
    for name in _methods(LLMClient):
        assert callable(getattr(client, name)), f"missing method: {name}"


def test_deepseek_client_covers_port():
    client = DeepSeekClient(api_key="k", chat_url="https://api.deepseek.com/chat/completions")
    for name in _methods(LLMClient):
        assert callable(getattr(client, name)), f"missing method: {name}"


def test_file_task_repository_covers_port(tmp_path):
    repo = FileTaskRepository(dir_path=str(tmp_path / "tasks"))
    for name in _methods(TaskRepository):
        assert callable(getattr(repo, name)), f"missing method: {name}"


def test_file_profile_repository_covers_port(tmp_path):
    repo = FileProfileRepository(dir_path=str(tmp_path / "profiles"))
    for name in _methods(ProfileRepository):
        assert callable(getattr(repo, name)), f"missing method: {name}"


def test_file_knowledge_repository_covers_port(tmp_path):
    repo = FileKnowledgeRepository(dir_path=str(tmp_path / "knowledge"))
    for name in _methods(KnowledgeRepository):
        assert callable(getattr(repo, name)), f"missing method: {name}"


def test_file_mcp_config_repository_covers_port(tmp_path):
    repo = FileMcpConfigRepository(file_path=str(tmp_path / "mcp.json"))
    for name in _methods(McpConfigRepository):
        assert callable(getattr(repo, name)), f"missing method: {name}"


def test_stdio_mcp_registry_covers_port(tmp_path):
    repo = FileMcpConfigRepository(file_path=str(tmp_path / "mcp.json"))
    reg = StdioMcpRegistry(repo)
    for name in _methods(McpRegistry):
        assert callable(getattr(reg, name)), f"missing method: {name}"


def test_deepseek_client_covers_tool_calling_port():
    client = DeepSeekClient(api_key="k", chat_url="https://api.deepseek.com/chat/completions")
    for name in _methods(ToolCallingLLMClient):
        assert callable(getattr(client, name)), f"missing method: {name}"
