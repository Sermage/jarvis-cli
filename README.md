# Jarvis CLI

Терминальный чат с [GigaChat](https://developers.sber.ru/studio) — интерактивный CLI с историей сессий и профилями агента.

## Установка

**1. Клонировать репо**

```bash
git clone https://github.com/Sermage/jarvis-cli.git
cd jarvis-cli
```

**2. Установить зависимости**

```bash
pip3 install requests
```

**3. Добавить ключ авторизации**

Получить ключ можно в [Sber Developer Studio](https://developers.sber.ru/studio).

```bash
cp .env.example .env
```

Открыть `.env` и вставить свой ключ:

```
GIGACHAT_AUTH_KEY=ваш_ключ_base64
```

**4. Запустить**

```bash
python3 chat.py
```

### Запуск командой `jarvis` (опционально)

```bash
chmod +x chat.py
sudo ln -sf "$(pwd)/chat.py" /usr/local/bin/jarvis
```

После этого достаточно набрать `jarvis` в любом терминале.

## Возможности

- **История сессий** — чаты сохраняются в `~/.jarvis/sessions/`, при следующем запуске можно продолжить любой
- **Профили агента** — системный промпт в markdown-файлах (`~/.jarvis/profiles/`)
- **Выбор модели** — GigaChat, GigaChat-Pro, GigaChat-Max и их v2-версии

## Команды

| Команда | Описание |
|---|---|
| `/model` | Выбрать модель |
| `/profile` | Сменить профиль агента |
| `/profile new` | Создать новый профиль |
| `/temp` | Задать temperature |
| `/tokens` | Задать max_tokens |
| `/settings` | Текущие настройки |
| `/clear` | Очистить текущую сессию |
| `/help` | Справка |
| `/quit` | Выход |

## Структура профиля

Профили хранятся в `~/.jarvis/profiles/*.md`. При первом запуске создаётся `default.md`.

```markdown
# Мой профиль

## Роль
Ты — Jarvis, ассистент-разработчик.

## Правила
- Отвечай кратко и по делу
- Используй русский язык

## Ограничения
- Не придумывай факты
```
