"""Configuration, read from the environment.

The app has no database and no persistent settings store: everything it needs
to know is a path or a limit, and both come from the environment so that a
deployment is a matter of exporting two variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["Config"]


def _env_path(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser().resolve() if value else default


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, not {value!r}") from exc


@dataclass
class Config:
    """Everything the app is allowed to touch, and how much of it at a time."""

    #: Datasets found here are listed automatically, and new ones are created
    #: here. Created on first use.
    workspace: Path = field(
        default_factory=lambda: _env_path(
            "MZSTACK_WORKSPACE", Path.home() / "mzstack-workspace"
        )
    )

    #: Directories the file browser may list. Nothing outside them is readable
    #: through the API, whatever path a client asks for.
    data_roots: tuple[Path, ...] = ()

    #: Peaks returned for one spectrum before the response is decimated.
    max_points: int = field(
        default_factory=lambda: _env_int("MZSTACK_MAX_POINTS", 20_000)
    )

    #: Rows returned by one table or query request.
    max_rows: int = field(default_factory=lambda: _env_int("MZSTACK_MAX_ROWS", 1_000))

    #: Threads available to run ingests. Ingest is I/O and DuckDB heavy, so a
    #: small number is plenty and keeps memory bounded.
    job_workers: int = field(
        default_factory=lambda: _env_int("MZSTACK_JOB_WORKERS", 2)
    )

    #: Jobs retained in memory, oldest evicted first.
    job_history: int = field(
        default_factory=lambda: _env_int("MZSTACK_JOB_HISTORY", 50)
    )

    def __post_init__(self) -> None:
        if not self.data_roots:
            raw = os.environ.get("MZSTACK_DATA_ROOTS", "")
            roots = [
                Path(p).expanduser().resolve() for p in raw.split(os.pathsep) if p
            ]
            # The workspace is always browsable: datasets get created in it, so
            # its contents are already exposed by the dataset endpoints.
            self.data_roots = tuple(dict.fromkeys([*roots, self.workspace]))

    @property
    def registry_file(self) -> Path:
        """Where datasets living outside the workspace are recorded."""
        return self.workspace / "datasets.json"

    def ensure_workspace(self) -> Path:
        self.workspace.mkdir(parents=True, exist_ok=True)
        return self.workspace
