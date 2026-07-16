# Архитектура Jarvis CLI

Jarvis CLI — терминальный чат с LLM на Python 3. Единственная обязательная
внешняя зависимость для рантайма — `requests`. Проект движется от «всё в одном
`chat.py`» к чистой слоистой архитектуре.

## Слои

Зависимости направлены строго сверху вниз: верхний слой знает об абстракциях
нижнего, но не о его деталях.

1. **`cli/`** — REPL, парсинг и диспетчеризация команд, ввод/вывод. Здесь нет
   бизнес-логики и прямого I/O с диском или сетью. Точка сборки графа
   зависимостей (composition root) — `cli/main.py`.
2. **`app/`** — сценарии (use cases): «отправить сообщение», «сохранить
   знание», «продвинуть стадию задачи», «ответить на вопрос о проекте».
   Оркестрирует домен и порты, но сам не лезет в файлы и сеть.
3. **`domain/`** — чистые модели и правила: `Task`, `TaskState`,
   `WorkingMemory`, `Profile`, `Knowledge`, `RetrievalConfig`, `Invariant`.
   Без внешних библиотек и без I/O.
4. **`infra/`** — реализации портов: файловые репозитории под `~/.jarvis/`,
   LLM-клиенты (`DeepSeekClient`, `RequestsGigaChatClient`), FAISS/Ollama
   retrieval, MCP-реестр, ANSI-вывод, спиннер.

## Порты и Dependency Injection

Слой `app/` оперирует абстракциями из `app/ports.py` (`SessionRepository`,
`LLMClient`, `RetrievalEngine`, `McpRegistry`, `Clock` и т.д.), а не
конкретными `open()` и `requests`. Конкретные реализации создаются только в
`cli/main.py` и прокидываются в use cases через конструкторы и параметры
функций. Глобального состояния и module-level кэшей нет — всё передаётся явно.
В тестах порты подменяются фейками, реализующими тот же `typing.Protocol`.

## Провайдеры LLM

Провайдер выбирается через `LLM_PROVIDER` в `.env` или командой `/provider`:

- **`deepseek`** (по умолчанию) — OpenAI-совместимый API `api.deepseek.com`,
  ключ `DEEPSEEK_API_KEY`. Только для DeepSeek работает вызов инструментов
  (tool calling), поэтому MCP-тулы доступны именно на нём.
- **`gigachat`** — Sber GigaChat, OAuth-ключ `GIGACHAT_AUTH_KEY`. TLS-проверка
  отключена — особенность Sber API.
- **`ollama`** — локальные модели через `/local`.

## RAG и MCP

- **RAG**: `infra/rag_retrieval.py::FaissOllamaRetrievalEngine` читает готовый
  FAISS-индекс (`<strategy>.faiss` + `<strategy>.meta.json`) и ищет по нему,
  эмбеддя запрос локальной моделью Ollama `bge-m3` (1024-dim). Поверх базового
  движка `RetrievalPipeline` добавляет rewrite → fetch_k → порог → реранк →
  top_k. Настройки — `domain/retrieval.py::RetrievalConfig`, команда `/rag`.
- **MCP**: `infra/mcp_registry.py::StdioMcpRegistry` поднимает настроенные
  MCP-серверы (stdio/http), обнаруживает их тулы, а `ToolRouter` даёт модели
  их вызывать. Управление — команда `/mcp`.

## Тесты

`pytest` обязателен. Структура `tests/` зеркалит пакеты (`tests/app/`,
`tests/domain/`, `tests/infra/`, `tests/cli/`). Новый код без тестов не
считается готовым; зелёный `pytest` — условие сдачи.
