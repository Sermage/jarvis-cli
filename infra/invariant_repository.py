"""Файловое хранилище инвариантов.

Один инвариант = один JSON-файл `~/.jarvis/invariants/<id>.json`.
Формат подобран так, чтобы файл легко редактировался руками:

    {
        "id": "kotlin-only",
        "title": "Стек — только Kotlin",
        "rule": "Бэкенд и фронтенд на Kotlin. Java/Scala запрещены.",
        "severity": "block",
        "enabled": true,
        "forbidden_patterns": ["\\bJava\\b", "AsyncTask"],
        "required_patterns":  []
    }
"""
from __future__ import annotations

import json
import os
from typing import Optional

from domain.invariant import (
    Invariant,
    InvariantSet,
    InvariantSeverity,
    sanitize_invariant_id,
)


class FileInvariantRepository:
    def __init__(self, dir_path: str):
        self._dir = dir_path

    def _path(self, invariant_id: str) -> str:
        return os.path.join(self._dir, f"{sanitize_invariant_id(invariant_id)}.json")

    def path_for(self, invariant_id: str) -> str:
        return self._path(invariant_id)

    def exists(self, invariant_id: str) -> bool:
        return os.path.exists(self._path(invariant_id))

    def list_ids(self) -> list[str]:
        if not os.path.isdir(self._dir):
            return []
        return sorted(
            os.path.splitext(f)[0]
            for f in os.listdir(self._dir)
            if f.endswith(".json") and not f.startswith(".")
        )

    def load(self, invariant_id: str) -> Optional[Invariant]:
        path = self._path(invariant_id)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return _from_dict(data, fallback_id=sanitize_invariant_id(invariant_id))

    def save(self, inv: Invariant) -> None:
        os.makedirs(self._dir, exist_ok=True)
        with open(self._path(inv.id), "w", encoding="utf-8") as f:
            json.dump(_to_dict(inv), f, ensure_ascii=False, indent=2)
            f.write("\n")

    def delete(self, invariant_id: str) -> None:
        path = self._path(invariant_id)
        if os.path.exists(path):
            os.remove(path)

    def load_all(self) -> InvariantSet:
        invs: list[Invariant] = []
        for inv_id in self.list_ids():
            try:
                inv = self.load(inv_id)
            except (json.JSONDecodeError, ValueError):
                # Битый файл не должен валить весь набор — пропускаем.
                continue
            if inv is not None:
                invs.append(inv)
        return InvariantSet.from_iterable(invs)


def _to_dict(inv: Invariant) -> dict:
    return {
        "id":                 inv.id,
        "title":              inv.title,
        "rule":               inv.rule,
        "severity":           inv.severity.value,
        "enabled":            inv.enabled,
        "forbidden_patterns": list(inv.forbidden_patterns),
        "required_patterns":  list(inv.required_patterns),
    }


def _from_dict(data: dict, fallback_id: str) -> Invariant:
    severity_raw = data.get("severity", "block")
    try:
        severity = InvariantSeverity(severity_raw)
    except ValueError:
        severity = InvariantSeverity.BLOCK
    return Invariant(
        id=sanitize_invariant_id(str(data.get("id") or fallback_id)),
        title=str(data.get("title", "")).strip(),
        rule=str(data.get("rule", "")).strip(),
        severity=severity,
        enabled=bool(data.get("enabled", True)),
        forbidden_patterns=tuple(data.get("forbidden_patterns") or ()),
        required_patterns=tuple(data.get("required_patterns") or ()),
    )
