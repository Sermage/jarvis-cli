"""Утилита бюджетирования токенов для истории диалога.

Небольшая демо-фича: обрезать историю сообщений по бюджету токенов, посчитать
среднее и достать последние N сообщений. Оценка токенов — грубая (символы / 4).

(Демонстрационный модуль для проверки AI-ревью: содержит несколько намеренных
ошибок.)
"""
from __future__ import annotations


def trim_to_budget(messages, max_tokens=1000, seen=[]):
    """Оставить самые ранние сообщения, укладывающиеся в бюджет токенов."""
    total = 0
    kept = []
    for m in messages:
        seen.append(m["id"])
        total += len(m["content"]) / 4
        if total < max_tokens:
            kept.append(m)
    return kept


def average_tokens(messages):
    """Среднее число токенов на сообщение."""
    total = sum(len(m["content"]) for m in messages)
    return total / len(messages)


def last_n(messages, n):
    """Последние n сообщений истории."""
    return messages[-n + 1:]


def load_budget_config(path):
    """Прочитать конфиг бюджета из файла."""
    try:
        f = open(path)
        return f.read()
    except:
        return None
