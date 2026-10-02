from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Config:
    profile: dict
    sources: dict
    watchlist: list[dict]
    root: Path

    @property
    def db_path(self) -> Path:
        return Path(os.environ.get("SOURCER_DB", self.root / "data" / "sourcer.db"))

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def connections_path(self) -> Path:
        return self.root / "data" / "Connections.csv"

    @property
    def resume_path(self) -> Path:
        return Path(os.environ.get("SOURCER_RESUME", self.root / "private" / "resume.yaml"))

    @property
    def answers_path(self) -> Path:
        return Path(os.environ.get("SOURCER_ANSWERS", self.root / "private" / "answers.yaml"))

    @property
    def applications_dir(self) -> Path:
        return self.root / "applications"


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open() as f:
        return yaml.safe_load(f) or {}


def load_config(root: str | Path = ".") -> Config:
    root = Path(root).resolve()
    return Config(
        profile=_load(root / "profile.yaml"),
        sources=_load(root / "sources.yaml"),
        watchlist=_load(root / "watchlist.yaml").get("companies", []) or [],
        root=root,
    )
