"""Файловое хранилище сессий (диалогов).

Идентификатор сессии — таймштамп вида `YYYY-MM-DDTHH-MM-SS`. Совпадает с
именем файла без `.json`. Ротация хранит не более `max_sessions` файлов.
"""
from __future__ import annotations

import json
import os
import time
from typing import Callable, Optional


class FileSessionRepository:
    def __init__(self,
                 dir_path: str,
                 max_sessions: int = 20,
                 now_id: Optional[Callable[[], str]] = None,
                 now_label: Optional[Callable[[], str]] = None):
        self._dir       = dir_path
        self._max       = max_sessions
        self._now_id    = now_id    or (lambda: time.strftime("%Y-%m-%dT%H-%M-%S"))
        self._now_label = now_label or (lambda: time.strftime("%Y-%m-%d %H:%M"))

    # ── ids / paths ──────────────────────────────────────────────────────────

    def path_for(self, session_id: str) -> str:
        return os.path.join(self._dir, f"{session_id}.json")

    def _id_from_path(self, path: str) -> str:
        return os.path.splitext(os.path.basename(path))[0]

    # ── core ops ─────────────────────────────────────────────────────────────

    def save(self,
             session_id: Optional[str],
             messages: list,
             params: dict) -> str:
        os.makedirs(self._dir, exist_ok=True)
        if session_id is None:
            session_id = self._now_id()
        title = messages[0]["content"][:60].replace("\n", " ") if messages else ""
        data = {
            "title":      title,
            "model":      params.get("model"),
            "updated_at": self._now_label(),
            "params":     params,
            "messages":   messages,
        }
        with open(self.path_for(session_id), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self._prune()
        return session_id

    def list_all(self) -> list[dict]:
        sessions = []
        for path in sorted(self._list_files(), reverse=True):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            sessions.append({
                "id":         self._id_from_path(path),
                "path":       path,
                "title":      data.get("title", "—"),
                "model":      data.get("model", "?"),
                "updated_at": data.get("updated_at", ""),
                "count":      len(data.get("messages", [])),
                "params":     data.get("params", {}),
                "messages":   data.get("messages", []),
            })
        return sessions

    def delete(self, session_id: str) -> None:
        path = self.path_for(session_id)
        if os.path.exists(path):
            os.remove(path)

    # ── internals ────────────────────────────────────────────────────────────

    def _list_files(self) -> list:
        if not os.path.isdir(self._dir):
            return []
        return [
            os.path.join(self._dir, f)
            for f in os.listdir(self._dir)
            if f.endswith(".json")
        ]

    def _prune(self) -> None:
        files = sorted(self._list_files())
        for f in files[:-self._max]:
            try:
                os.remove(f)
            except OSError:
                pass
