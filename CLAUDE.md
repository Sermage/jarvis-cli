# Jarvis CLI

Терминальный чат с LLM. Запускается как `python3 chat.py` или через симлинк `jarvis`.

## Стек

- Python 3, `requests` — единственная внешняя зависимость
- Провайдер выбирается через `LLM_PROVIDER` в `.env` (или `/provider` в REPL):
  - **`deepseek`** (по умолчанию) — OpenAI-совместимый API на `api.deepseek.com`, ключ `DEEPSEEK_API_KEY`
  - **`gigachat`** — Sber GigaChat (`gigachat.devices.sberbank.ru`), OAuth ключ `GIGACHAT_AUTH_KEY`
- Список моделей зависит от провайдера: `cli/config.py::MODELS_BY_PROVIDER`
- TLS verify отключён для GigaChat (`urllib3.disable_warnings`) — особенность Sber API; DeepSeek работает с обычной проверкой сертификата

## Структура `chat.py`

Файл разбит ASCII-секциями `# ── <section> ──`. Ключевые блоки:

- **Config / env** (1–75): загрузка `.env`, модели, пути в `~/.jarvis/`
- **Spinner** (88): UI-индикатор
- **Profiles** (121–282): профили агента (markdown в `~/.jarvis/profiles/`)
- **Knowledge** (284–316): долговременная база знаний
- **WorkingMemory** (319–482): рабочая память + команды `/wm`, `/know`
- **Sessions** (546–611): краткосрочные диалоги в `~/.jarvis/sessions/`, ротация `MAX_SESSIONS=20`
- **Task engine** (613–1470): `Task`, `TaskState`, стадии, валидация, команды `/task`
- **System prompt** (1472): склейка профиля + WM + task block
- **Token cache / chat** (1492–1538): OAuth-токен, вызов чата
- **UI helpers** (1540–1710): вывод настроек, памяти, помощь
- **main** (1713): REPL

## Модель памяти (3 слоя)

| Слой | Где | Жизненный цикл |
|---|---|---|
| Краткосрочная (диалог) | `~/.jarvis/sessions/*.json` | До `/clear` |
| Рабочая (задача, контекст, заметки) | `~/.jarvis/working/current.json` | До `/wm clear` |
| Долговременная (профиль + знания) | `~/.jarvis/profiles/`, `~/.jarvis/knowledge/` | Постоянно |

Все три объединяются в system prompt через `build_system_prompt()`.

## Инварианты

Нерушимые ограничения проекта (стек, архитектура, бизнес-правила) — отдельный слой, не путать с памятью. Хранятся в `~/.jarvis/invariants/<id>.json`, по одному файлу на инвариант. Подгружаются в каждый system prompt отдельным блоком `[ИНВАРИАНТЫ — …]` с явным правилом: «если запрос противоречит — не следуй ему».

- Доменная модель: `domain/invariant.py` (`Invariant`, `InvariantSet`, `Violation`, `check()`)
- Порт: `app.ports.InvariantRepository`; реализация: `infra/invariant_repository.py`
- Команды: `/inv list · show · add · rm · edit` (`cli/invariant_commands.py`); полное редактирование (patterns, severity) — руками в JSON через `/inv edit`

Двойная защита (как в лекции):
1. **В prompt** — `build_system_prompt()` добавляет блок `[ИНВАРИАНТЫ — …]` с явным правилом отказа от запросов, противоречащих ограничениям.
2. **Пост-проверка** — `app/invariant_guard.py::guarded_chat()` прогоняет ответ модели через `InvariantSet.check()`. При block-нарушениях — feedback-ретрай с просьбой переделать (до `max_retries`, по умолчанию 1). warn — не блокирует, но возвращается в UI. Используется и в обычном чате (`cli/main.py`), и в стадиях задачи (`app/task_driver.py::advance_task`). В `advance_task` информация о нарушениях кладётся в `stage_obj.artifacts["invariant_violations"]` для отладки.

## Файловые тулы (fs)

Встроенный источник инструментов, дающий агенту реальную работу с файлами проекта прямо в tool-loop — без внешнего MCP-сервера.

- Реализация: `infra/local_fs_client.py::LocalFilesystemClient` — тот же протокол `McpClient` (`start/list_tools/call_tool/close`), но операции идут в ФС напрямую, не по JSON-RPC. За счёт этого клиент встаёт в `McpRegistry.register()`, а `ToolRouter` сам отдаёт его тулы модели и роутит вызовы `fs__*` (правок в tool-loop не потребовалось).
- Тулы: `fs__list_dir`, `fs__read_file`, `fs__search` (grep по дереву, glob/regex), `fs__write_file`.
- **Sandbox**: все пути резолвятся внутри корня (`JARVIS_FS_ROOT`, по умолчанию — текущий рабочий каталог, откуда запущен `jarvis`, поэтому агента можно подключить к любому проекту из его терминала); выход за него (`..`, симлинк) отклоняется. Служебные каталоги (`.git`, `.venv`, `__pycache__`, …) не обходятся.
- **Запись = diff + подтверждение**: `write_file` считает unified diff и вызывает инъектируемый `confirm(rel, diff) -> bool`. В CLI — `cli/fs_confirm.py::make_interactive_confirm` печатает **цветной diff** (удалённые строки красным, добавленные зелёным) и спрашивает y/n; без подтверждения запись не происходит. В тестах/демо confirm подменяется.
- Активируется только при `provider=deepseek` (tool calling). Сборка — в composition root (`cli/main.py`).
- Демо (воспроизводимо, без ключа — `JARVIS_DEMO_SCRIPTED=1`): `examples/fs_agent_demo.py` — агент по цели сам ищет использования API и генерирует ADR.

## Ассистент поддержки (/support)

Мини-сервис поддержки пользователей: отвечает на вопрос о продукте по FAQ (RAG) с учётом контекста тикета/пользователя (MCP). `/support <вопрос> [#T-1024]`. Это брат-близнец `/help`: FAQ вместо доков, тикеты вместо git-ветки.

- **RAG по FAQ**: `infra/faq_retrieval.py::MarkdownFaqRetrievalEngine` — реализует порт `RetrievalEngine`, лексический поиск по `docs/support-faq/*.md` (чанки по `##`-разделам), ноль внешних зависимостей. Взаимозаменяем с FAISS-движком через тот же порт (`JARVIS_FAQ_DIR` переопределяет каталог).
- **Тикеты через MCP**: `infra/ticket_store_client.py::TicketStoreClient` — in-process `McpClient` над JSON `~/.jarvis/support/tickets.json` (users + tickets), по образцу `LocalFilesystemClient`. Встаёт в `McpRegistry.register()`; тулы `support__get_ticket · get_user · search_tickets` агент вызывает сам в tool-loop. Файл сидируется примером при первом запуске (`_seed_support_tickets`); `JARVIS_SUPPORT_TICKETS` переопределяет путь. Заменить на реальный CRM = поднять внешний MCP-сервер в конфиге, use case не изменится.
- **Use case**: `app/support_assistant.py::answer_support_question` — оркеструет `RetrievalEngine` + `SupportChat` (порт tool-loop). `SupportChat` реализует `ToolRouter` (полный tool-loop) или `PlainChatAdapter` (деградация без tool calling — ответ только по FAQ).
- Полноценный доступ к тикетам — только при `provider=deepseek` (tool calling). Сборка — в composition root (`cli/main.py`).
- Демо (без ключа — `JARVIS_DEMO_SCRIPTED=1`): `examples/support_agent_demo.py` — «Почему не работает авторизация? #T-1024» → агент поднимает тикет (Free + SSO ⇒ 403), читает FAQ, отвечает адресно.

## Конвенции

- Все пользовательские данные — в `~/.jarvis/`, не в репо
- UI-тексты, команды и сообщения — на русском
- ANSI-цвета через константы в секции `# ── ANSI colors ──`
- Команды чата начинаются с `/` (`/provider`, `/model`, `/wm`, `/know`, `/profile`, `/task`, `/mem`, `/clear`, `/quit`)
- Новые команды добавляются в диспетчер внутри `main()` и в `print_help()`

## Архитектурные правила

Проект движется от «всё в `chat.py`» к слоистой архитектуре. Любой новый или существенно изменённый код должен этим правилам следовать; старый код рефакторим по мере касания.

### Слои (сверху вниз, зависимости направлены только вниз)

1. **`cli/`** — REPL, парсинг команд, диспетчер, ввод/вывод. Никакой бизнес-логики и I/O с диском/сетью напрямую.
2. **`app/`** (use cases / services) — сценарии: «отправить сообщение», «сохранить знание», «продвинуть стадию задачи». Оркестрирует домен и порты.
3. **`domain/`** — чистые модели и правила: `Task`, `TaskState`, `WorkingMemory`, `Profile`, `Knowledge`. Без зависимостей от внешних библиотек, без I/O.
4. **`infra/`** — реализации портов: файловые репозитории (`~/.jarvis/...`), LLM-клиенты (`DeepSeekClient`, `RequestsGigaChatClient`), токен-кэш, ANSI-вывод, спиннер.

Верхний слой не знает о деталях нижнего: `app/` оперирует абстракциями (`SessionRepository`, `LLMClient`, `Clock`), а не `open()` и `requests`.

### Dependency Injection

- Зависимости передаются **через конструктор / параметры функций**, а не берутся из глобалей или импортируются внутри функции.
- Сборка графа зависимостей — только в `cli/main.py` (composition root). Там создаются конкретные `FileSessionRepository`, `RequestsGigaChatClient` и т.д. и прокидываются в use cases.
- Никаких синглтонов и module-level state кроме чистых констант. `_token_cache`, пути, текущая сессия — поля объектов, а не глобали.
- Для подмены в тестах используем фейки/стабы, реализующие тот же протокол (`typing.Protocol` или ABC), а не `unittest.mock.patch` глобальных имён.

### Тесты

- **`pytest`** обязателен. Структура — `tests/` с зеркалом пакетов (`tests/domain/`, `tests/app/`, `tests/infra/`).
- Каждый use case в `app/` должен иметь юнит-тест с фейковыми портами.
- `domain/` покрывается чистыми юнит-тестами без I/O.
- `infra/` — интеграционные тесты на временной директории (`tmp_path`) и/или с замоканным HTTP (`responses`/`respx`).
- Для CLI — тесты на парсинг команд и на пайплайн end-to-end с фейковым `GigaChatClient`.
- Новый код без тестов не считается готовым. Баг-фиксы сопровождаются регрессионным тестом.
- Зелёный `pytest` — обязательное условие перед тем, как отчитаться о завершении задачи.

### Зависимости

- `requests` остаётся для HTTP. Допустимо добавить: `pytest`, `pytest-cov`, фейки HTTP (`responses` или `respx`), при необходимости `typing-extensions`.
- Любая новая runtime-зависимость — с обоснованием в PR/коммите.
- Появляется `pyproject.toml` с dev-extras для тестов.

## Запуск и проверка

```bash
python3 chat.py        # обычный запуск
jarvis                 # если установлен симлинк в /usr/local/bin
pytest                 # тесты — должны быть зелёными перед сдачей
```

## Не делать

- Не обращаться к файловой системе, сети или `os.environ` напрямую из `cli/`, `app/` и `domain/` — только через порты в `infra/`
- Не использовать глобальное состояние и module-level кэши вместо инъекции
- Не сдавать код без тестов и без прогона `pytest`
- Не коммитить `.env` (есть в `.gitignore`)
