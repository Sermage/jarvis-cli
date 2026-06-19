"""Файловое хранилище профилей агента."""
from __future__ import annotations

import os
from typing import Optional

from domain.profile import Profile, sanitize_profile_name


class FileProfileRepository:
    def __init__(self, dir_path: str):
        self._dir = dir_path

    def path_for(self, name: str) -> str:
        return os.path.join(self._dir, f"{sanitize_profile_name(name)}.md")

    def exists(self, name: str) -> bool:
        return os.path.exists(self.path_for(name))

    def list_names(self) -> list[str]:
        if not os.path.isdir(self._dir):
            return []
        return sorted(
            os.path.splitext(f)[0]
            for f in os.listdir(self._dir)
            if f.endswith(".md")
        )

    def load(self, name: str) -> Optional[Profile]:
        path = self.path_for(name)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return Profile(name=sanitize_profile_name(name), content=f.read().strip())

    def save(self, profile: Profile) -> None:
        os.makedirs(self._dir, exist_ok=True)
        with open(self.path_for(profile.name), "w", encoding="utf-8") as f:
            f.write(profile.content)

    def delete(self, name: str) -> None:
        path = self.path_for(name)
        if os.path.exists(path):
            os.remove(path)

    def ensure_default(self) -> Profile:
        existing = self.load("default")
        if existing is not None:
            return existing
        default = Profile.default()
        self.save(default)
        return default
