"""Per-app singletons, reachable from blueprints without import cycles."""

from __future__ import annotations

from dataclasses import dataclass

from flask import Flask, current_app

from .config import Config
from .jobs import JobRegistry
from .registry import Registry

__all__ = ["Extensions", "config", "init_extensions", "jobs", "registry"]

KEY = "mzstack"


@dataclass(frozen=True)
class Extensions:
    config: Config
    registry: Registry
    jobs: JobRegistry


def init_extensions(app: Flask, cfg: Config) -> Extensions:
    ext = Extensions(
        config=cfg,
        registry=Registry(cfg),
        jobs=JobRegistry(max_workers=cfg.job_workers, history=cfg.job_history),
    )
    app.extensions[KEY] = ext
    return ext


def _current() -> Extensions:
    return current_app.extensions[KEY]


def config() -> Config:
    return _current().config


def registry() -> Registry:
    return _current().registry


def jobs() -> JobRegistry:
    return _current().jobs
