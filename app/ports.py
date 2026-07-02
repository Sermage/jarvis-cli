"""Порты слоя приложения.

Use cases в `app/` оперируют этими абстракциями, а не конкретными
реализациями `infra/`. Это позволяет подменять хранилища в тестах
фейками без `unittest.mock.patch` глобальных имён.
"""
from __future__ import annotations

from typing import Optional, Protocol

from domain.invariant import Invariant, InvariantSet
from domain.knowledge import KnowledgeEntry
from domain.mcp import McpServerConfig, McpTool, ToolResult
from domain.profile import Profile
from domain.retrieval import RetrievedChunk
from domain.task import Task
from domain.working_memory import WorkingMemory


class WorkingMemoryRepository(Protocol):
    """Хранилище рабочей памяти текущего сеанса."""

    def load(self) -> WorkingMemory: ...
    def save(self, wm: WorkingMemory) -> None: ...
    def clear(self) -> None: ...


class SessionRepository(Protocol):
    """Хранилище краткосрочной памяти (диалогов).

    Идентификатор сессии — строка, совпадающая с именем файла без расширения.
    """

    def save(self,
             session_id: Optional[str],
             messages: list,
             params: dict) -> str:
        """Сохранить сессию. Если session_id is None — создать новый и вернуть его."""
        ...

    def list_all(self) -> list[dict]:
        """Вернуть список сессий, отсортированных от свежей к старой."""
        ...

    def delete(self, session_id: str) -> None: ...

    def path_for(self, session_id: str) -> str:
        """Абсолютный путь к файлу сессии (нужен UI для отображения)."""
        ...


class LLMClient(Protocol):
    """Клиент LLM-провайдера. Скрывает аутентификацию и HTTP-транспорт.

    Используется поверх любого провайдера (DeepSeek, GigaChat, …); конкретная
    реализация выбирается в composition root по `LLM_PROVIDER`.
    """

    def chat(self,
             messages: list,
             params: dict,
             system_prompt: Optional[str] = None) -> str: ...


class ToolCallingLLMClient(LLMClient, Protocol):
    """Расширение LLMClient для провайдеров с поддержкой function/tool calling.

    Возвращает «сырой» message-объект OpenAI-формата:
        {"content": Optional[str], "tool_calls": list[dict]}
    Это нужно ToolRouter'у — он добавляет ответ ассистента в историю как
    есть (с массивом tool_calls), затем подкладывает tool-сообщения и снова
    вызывает модель. Реализуется только теми клиентами, чей API это умеет
    (сейчас — DeepSeek; GigaChat пока без тулов).
    """

    def chat_with_tools(self,
                        messages: list,
                        params: dict,
                        tools: list,
                        system_prompt: Optional[str] = None) -> dict: ...


class RetrievalEngine(Protocol):
    """Поиск релевантных фрагментов в индексе документов (RAG).

    Реализация (`infra/`) владеет чтением индекса и обращением к
    эмбеддинг-модели; `app/` знает только об этом протоколе.
    """

    def retrieve(self, query: str, top_k: int = 5) -> list[RetrievedChunk]:
        """Вернуть top_k наиболее близких к запросу фрагментов."""
        ...

    def is_ready(self) -> bool:
        """Готов ли движок (индекс на месте, зависимости доступны)."""
        ...


class McpClient(Protocol):
    """Подключение к одному MCP-серверу.

    Жизненный цикл: `start()` запускает транспорт и делает initialize-хендшейк,
    дальше можно вызывать `list_tools` и `call_tool`. `close()` гасит сервер.
    """
    server_id: str

    def start(self) -> None: ...
    def list_tools(self) -> list[McpTool]: ...
    def call_tool(self, name: str, arguments: dict) -> ToolResult: ...
    def close(self) -> None: ...


class McpRegistry(Protocol):
    """Реестр активных MCP-клиентов, индексируемых по `server_id`."""

    def clients(self) -> list[McpClient]: ...
    def get(self, server_id: str) -> Optional[McpClient]: ...
    def all_tools(self) -> list[McpTool]: ...
    def shutdown(self) -> None: ...


class McpConfigRepository(Protocol):
    """Хранилище конфигурации MCP-серверов (~/.jarvis/mcp/servers.json)."""

    def list_all(self) -> list[McpServerConfig]: ...
    def get(self, server_id: str) -> Optional[McpServerConfig]: ...
    def save(self, cfg: McpServerConfig) -> None: ...
    def delete(self, server_id: str) -> None: ...
    def set_enabled(self, server_id: str, enabled: bool) -> None: ...


class TaskRepository(Protocol):
    """Хранилище задач + указатель активной задачи."""

    def save(self, task: Task) -> None: ...
    def load(self, task_id: str) -> Optional[Task]: ...
    def list_all(self) -> list[Task]: ...
    def delete(self, task: Task) -> None: ...

    def set_active(self, task: Task) -> None: ...
    def get_active_id(self) -> Optional[str]: ...
    def get_active(self) -> Optional[Task]: ...
    def clear_active(self) -> None: ...

    def transition(self, task: Task, new_state: str, reason: str = "") -> None:
        """Сделать переход по машине состояний и сохранить."""
        ...


class ProfileRepository(Protocol):
    """Хранилище markdown-профилей агента."""

    def list_names(self) -> list[str]: ...
    def load(self, name: str) -> Optional[Profile]: ...
    def save(self, profile: Profile) -> None: ...
    def delete(self, name: str) -> None: ...
    def exists(self, name: str) -> bool: ...
    def ensure_default(self) -> Profile: ...
    def path_for(self, name: str) -> str:
        """Путь к md-файлу — нужен для запуска внешнего редактора."""
        ...


class KnowledgeRepository(Protocol):
    """Хранилище долговременной базы знаний."""

    def list_names(self) -> list[str]: ...
    def load(self, name: str) -> Optional[KnowledgeEntry]: ...
    def save(self, entry: KnowledgeEntry) -> None: ...
    def all_as_prompt(self) -> str:
        """Склейка всех записей для system prompt."""
        ...


class InvariantRepository(Protocol):
    """Хранилище инвариантов — нерушимых ограничений проекта.

    Инварианты хранятся отдельно от диалога и подгружаются в каждый
    system prompt, чтобы ассистент не мог их случайно нарушить.
    """

    def list_ids(self) -> list[str]: ...
    def load(self, invariant_id: str) -> Optional[Invariant]: ...
    def save(self, inv: Invariant) -> None: ...
    def delete(self, invariant_id: str) -> None: ...
    def exists(self, invariant_id: str) -> bool: ...
    def load_all(self) -> InvariantSet:
        """Все инварианты единым набором — то, что уходит в prompt."""
        ...
    def path_for(self, invariant_id: str) -> str:
        """Путь к файлу — нужен для запуска внешнего редактора."""
        ...
