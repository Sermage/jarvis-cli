"""Доменная модель профиля агента.

Профиль — это именованный markdown-документ, описывающий роль, правила и
ограничения агента. Здесь живут только данные и чистые операции над ними
(нормализация имени, шаблоны). Файловые операции — в `infra/`.
"""
from __future__ import annotations

from typing import Optional


DEFAULT_PROFILE_CONTENT = """\
# Профиль агента

Ты — Jarvis, интеллектуальный ассистент-разработчик.

## Роль
Помогаешь с разработкой программного обеспечения: пишешь и объясняешь код,
находишь баги, предлагаешь архитектурные решения.

## Правила
- Отвечай на русском языке, если пользователь пишет по-русски
- Давай краткие и точные ответы
- Предпочитай конкретные примеры кода абстрактным объяснениям
- Если вопрос неоднозначен — уточни, прежде чем отвечать

## Ограничения
- Не придумывай факты — лучше скажи, что не знаешь
- Не генерируй вредоносный или небезопасный код
"""


PROFILE_TEMPLATE = """\
# {name}

## Роль
Опиши роль и личность агента.

## Правила
- Правило 1
- Правило 2

## Ограничения
- Ограничение 1
"""


def sanitize_profile_name(raw: str) -> str:
    """Привести имя профиля к виду, безопасному для файловой системы."""
    return raw.strip().replace(" ", "-").replace("/", "-")


class Profile:
    """Профиль агента: имя + содержимое markdown-файла."""

    def __init__(self, name: str, content: str):
        self.name    = name
        self.content = content

    @classmethod
    def default(cls) -> "Profile":
        return cls(name="default", content=DEFAULT_PROFILE_CONTENT)

    @classmethod
    def from_template(cls, name: str) -> "Profile":
        safe = sanitize_profile_name(name)
        return cls(name=safe, content=PROFILE_TEMPLATE.format(name=name))

    def is_empty(self) -> bool:
        return not self.content.strip()
