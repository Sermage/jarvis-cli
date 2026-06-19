"""Файловое хранилище долговременной базы знаний."""
from __future__ import annotations

import os
import re
import time
from typing import Callable, Optional

from domain.knowledge import KnowledgeEntry, sanitize_knowledge_name


_TIMESTAMP_RE = re.compile(r"^<!--\s*сохранено:\s*(.+?)\s*-->\n?")


class FileKnowledgeRepository:
    def __init__(self,
                 dir_path: str,
                 now: Optional[Callable[[], str]] = None):
        self._dir = dir_path
        self._now = now or (lambda: time.strftime("%Y-%m-%d %H:%M"))

    def _path(self, name: str) -> str:
        return os.path.join(self._dir, f"{sanitize_knowledge_name(name)}.md")

    def list_names(self) -> list[str]:
        if not os.path.isdir(self._dir):
            return []
        return sorted(
            os.path.splitext(f)[0]
            for f in os.listdir(self._dir)
            if f.endswith(".md")
        )

    def load(self, name: str) -> Optional[KnowledgeEntry]:
        path = self._path(name)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        saved_at = None
        m = _TIMESTAMP_RE.match(raw)
        if m:
            saved_at = m.group(1)
            raw = raw[m.end():]
        return KnowledgeEntry(
            name=sanitize_knowledge_name(name),
            content=raw,
            saved_at=saved_at,
        )

    def save(self, entry: KnowledgeEntry) -> None:
        os.makedirs(self._dir, exist_ok=True)
        if entry.saved_at is None:
            entry.saved_at = self._now()
        with open(self._path(entry.name), "w", encoding="utf-8") as f:
            f.write(entry.to_file_text())

    def all_as_prompt(self) -> str:
        names = self.list_names()
        if not names:
            return ""
        parts = []
        for name in names:
            entry = self.load(name)
            if entry is None:
                continue
            # Заголовок маркируем без timestamp — он только для on-disk и не нужен модели.
            parts.append(f"### {entry.name}\n{entry.content.strip()}")
        return "\n\n".join(parts)
