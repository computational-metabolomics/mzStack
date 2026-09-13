"""Finding datasets, and giving them stable ids.

There is no database. A dataset is known either by sitting in the workspace
directory, or by having its path registered in ``<workspace>/datasets.json``.
Both are re-read on every listing: the filesystem is the source of truth, so a
dataset created by the CLI while the server is running shows up without a
restart.

Clients address datasets by id, never by path, so a request can only ever
reach a dataset the server already knows about.
"""

from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from mzstack import is_mzstack_dataset

from .config import Config
from .errors import BadRequest, NotFound

__all__ = ["DatasetEntry", "Registry", "slugify"]

_SLUG_STRIP = re.compile(r"[^a-zA-Z0-9._-]+")


def slugify(name: str) -> str:
    """A directory name as a URL-safe id."""
    slug = _SLUG_STRIP.sub("-", name).strip("-.")
    return slug or "dataset"


@dataclass(frozen=True)
class DatasetEntry:
    """A dataset the server can open."""

    id: str
    path: Path
    #: ``workspace`` if found by scanning, ``registered`` if from datasets.json.
    source: str

    def as_dict(self) -> dict[str, object]:
        return {"id": self.id, "path": str(self.path), "source": self.source}


class Registry:
    """The set of known datasets: the workspace, plus registered paths."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._lock = threading.Lock()

    # -- reading --------------------------------------------------------------

    def entries(self) -> list[DatasetEntry]:
        """Every known dataset, workspace first, ids unique."""
        found: dict[str, DatasetEntry] = {}

        for path in self._scan_workspace():
            entry = DatasetEntry(slugify(path.name), path, "workspace")
            found[entry.id] = entry

        for record in self._read_registry():
            path = Path(record["path"])
            if not is_mzstack_dataset(path):
                # Registered but since moved or deleted: skip it rather than
                # fail the whole listing.
                continue
            id_ = record.get("id") or slugify(path.name)
            if id_ in found and found[id_].path != path:
                id_ = self._disambiguate(id_, found)
            found.setdefault(id_, DatasetEntry(id_, path, "registered"))

        return sorted(found.values(), key=lambda e: e.id)

    def resolve(self, dataset_id: str) -> DatasetEntry:
        """The entry for `dataset_id`, or a 404."""
        for entry in self.entries():
            if entry.id == dataset_id:
                return entry
        raise NotFound(f"No such dataset: {dataset_id!r}")

    def path_for(self, dataset_id: str) -> Path:
        return self.resolve(dataset_id).path

    # -- writing --------------------------------------------------------------

    def register(self, path: str | Path) -> DatasetEntry:
        """Record a dataset that lives outside the workspace."""
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_dir():
            raise BadRequest(f"Not a directory: {resolved}")
        if not is_mzstack_dataset(resolved):
            raise BadRequest(f"Not an mzStack dataset (no mzStack.json): {resolved}")

        with self._lock:
            records = self._read_registry()
            for record in records:
                if Path(record["path"]) == resolved:
                    return DatasetEntry(record["id"], resolved, "registered")

            taken = {e.id for e in self.entries()}
            id_ = slugify(resolved.name)
            if id_ in taken:
                id_ = self._disambiguate(id_, taken)

            records.append(
                {
                    "id": id_,
                    "path": str(resolved),
                    "added_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            self._write_registry(records)
            return DatasetEntry(id_, resolved, "registered")

    def unregister(self, dataset_id: str) -> None:
        """Forget a registered dataset. The directory itself is untouched."""
        with self._lock:
            records = self._read_registry()
            kept = [r for r in records if r.get("id") != dataset_id]
            if len(kept) == len(records):
                raise NotFound(f"No registered dataset with id {dataset_id!r}")
            self._write_registry(kept)

    def workspace_path_for(self, name: str) -> Path:
        """Where a new dataset called `name` would be created.

        The name is a single directory component under the workspace: a client
        cannot steer creation elsewhere by passing a path.
        """
        slug = slugify(name)
        if slug != name.strip():
            raise BadRequest(
                f"Dataset name may only contain letters, digits, '.', '_' and "
                f"'-'; got {name!r}"
            )
        target = (self._config.ensure_workspace() / slug).resolve()
        if target.parent != self._config.workspace:
            raise BadRequest(f"Invalid dataset name: {name!r}")
        if target.exists():
            raise BadRequest(f"A dataset directory called {slug!r} already exists")
        return target

    # -- internals ------------------------------------------------------------

    def _scan_workspace(self) -> list[Path]:
        workspace = self._config.workspace
        if not workspace.is_dir():
            return []
        return sorted(
            child
            for child in workspace.iterdir()
            if child.is_dir() and is_mzstack_dataset(child)
        )

    def _read_registry(self) -> list[dict]:
        file = self._config.registry_file
        if not file.is_file():
            return []
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise BadRequest(f"{file} is not valid JSON: {exc}") from exc
        if not isinstance(data, list):
            raise BadRequest(f"{file} should hold a list of datasets")
        return [r for r in data if isinstance(r, dict) and r.get("path")]

    def _write_registry(self, records: list[dict]) -> None:
        file = self._config.ensure_workspace() / self._config.registry_file.name
        tmp = file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, file)

    @staticmethod
    def _disambiguate(id_: str, taken) -> str:
        n = 2
        while f"{id_}-{n}" in taken:
            n += 1
        return f"{id_}-{n}"
