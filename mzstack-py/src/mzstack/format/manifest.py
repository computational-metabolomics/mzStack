"""Reading and writing ``mzStack.json``.

The manifest lists the dataset's runs: each run's kind (``mzpeak`` signal in
an external archive, or ``native`` signal in ``<dataset>/spectra``), where its
signal lives, how many spectra it holds, and which of its caches are current.
Kind is read from here, not from the directory layout: both kinds keep their
manifest in the same place.

Three encoding rules the tests pin: counts are JSON integers, absent scalars
are ``null``, and an empty ``projections`` is ``{}`` rather than ``[]``.
Unknown keys survive a read/write cycle, and the version gate is on the major
component alone.
"""

from __future__ import annotations

import json
import os
import time
from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import FormatError
from . import layout

__all__ = [
    "Manifest",
    "Run",
    "IdBlock",
    "dataset_kind",
    "write_native_manifest",
]


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _semver_major(version: Any) -> int | None:
    """Major component of a semantic version string, or ``None``."""
    try:
        return int(str(version).split(".", 1)[0])
    except (TypeError, ValueError):
        return None


class Run:
    """One run entry, a live view over its dict in the manifest.

    Mutating a ``Run`` mutates the manifest it came from.
    """

    __slots__ = ("_d",)

    def __init__(self, d: dict[str, Any]) -> None:
        self._d = d

    # -- identity

    @property
    def run_id(self) -> str:
        return str(self._d["run_id"])

    @property
    def kind(self) -> str:
        return str(self._d.get("kind") or layout.KIND_NATIVE)

    @property
    def path(self) -> str | None:
        p = self._d.get("path")
        return None if p is None else str(p)

    # -- id block

    @property
    def n_spectra(self) -> int:
        return int(self._d.get("n_spectra") or 0)

    @property
    def uid_base(self) -> int:
        return int(self._d.get("uid_base") or 1)

    @property
    def ingested_at(self) -> int:
        """Generation at which this run's index was built.

        Projections record the value they were built from, so re-ingesting one
        run invalidates only that run's caches.
        """
        return int(self._d.get("ingested_at") or 1)

    # -- signal

    @property
    def signal(self) -> dict[str, Any]:
        s = self._d.get("signal")
        return s if isinstance(s, dict) else {}

    @property
    def layout(self) -> str | None:
        v = self.signal.get("layout")
        return None if v is None else str(v)

    @property
    def profile(self) -> str | None:
        v = self.signal.get("profile")
        return None if v is None else str(v)

    @property
    def centroid(self) -> str | None:
        v = self.signal.get("centroid")
        return None if v is None else str(v)

    @property
    def partitioning(self) -> tuple[str, ...]:
        """Hive partitioning columns this run was written with.

        Informational: DuckDB discovers partitions from the directory names.
        """
        v = self.signal.get("partitioning")
        if v is None:
            return ()
        if isinstance(v, str):
            return (v,)
        return tuple(str(x) for x in v)

    # -- projections

    @property
    def projections(self) -> dict[str, Any]:
        p = self._d.get("projections")
        return p if isinstance(p, dict) else {}

    def has_projection(self, type: str) -> bool:
        """Is this run's projection of ``type`` current, i.e. built from the
        run as it stands now?"""
        p = self.projections.get(type)
        if not isinstance(p, dict):
            return False
        try:
            return int(p.get("generation")) == self.ingested_at
        except (TypeError, ValueError):
            return False

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"Run({self.run_id!r}, kind={self.kind!r}, "
            f"n_spectra={self.n_spectra}, uid_base={self.uid_base})"
        )


@dataclass(frozen=True)
class IdBlock:
    """The part of a `split_ids` request that falls in one run."""

    run: Run
    #: Global ``spectrum_id_`` values, in the order they were asked for.
    ids: tuple[int, ...]
    #: The same spectra as run-local 0-based indices, matching an mzPeak
    #: archive's own ``index`` column.
    local: tuple[int, ...]


class Manifest:
    """An mzStack manifest.

    The parsed JSON is kept as the source of truth, so keys this
    implementation does not understand survive a read/write cycle.
    """

    __slots__ = ("_d",)

    def __init__(self, data: dict[str, Any]) -> None:
        self._d = data

    # -- construction ---------------------------------------------------------

    @classmethod
    def new(cls) -> Manifest:
        """An empty manifest for a dataset with no runs yet."""
        return cls(
            {
                "format": layout.FORMAT,
                "version": layout.VERSION,
                "generation": 1,
                "created": _utc_now(),
                "runs": [],
            }
        )

    @classmethod
    def read(cls, path: str | Path) -> Manifest:
        """Read and validate the manifest of the dataset at ``path``."""
        fl = layout.manifest_path(path)
        if not fl.is_file():
            raise FormatError(
                f"'{path}' is not an mzStack dataset: no {layout.MANIFEST_NAME}."
            )
        try:
            data = json.loads(fl.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise FormatError(f"Could not parse '{fl}': {e}") from e
        if not isinstance(data, dict):
            raise FormatError(f"'{fl}' does not contain a JSON object.")

        declared = data.get("format")
        if declared != layout.FORMAT:
            raise FormatError(
                f"'{fl}' declares format '{declared or '<missing>'}'; "
                f"expected '{layout.FORMAT}'."
            )

        # Same major version means the layout is one this code understands; a
        # later minor version may add fields.
        have = _semver_major(data.get("version"))
        want = _semver_major(layout.VERSION)
        if have is None or have != want:
            raise FormatError(
                f"Dataset '{path}' is mzStack version "
                f"{data.get('version') or '<missing>'}; this version of "
                f"mzstack reads {want}.x."
            )

        data["generation"] = int(data.get("generation") or 1)
        if not isinstance(data.get("runs"), list):
            data["runs"] = []
        return cls(data)

    def write(self, path: str | Path) -> Path:
        """Write the manifest atomically.

        Written to a temporary file in the same directory and renamed into
        place, so a reader never observes a half-written manifest and an
        interrupted write leaves the previous one intact.
        """
        d = Path(path)
        d.mkdir(parents=True, exist_ok=True)
        fl = layout.manifest_path(d)
        tmp = fl.with_name(f"{fl.name}.tmp-{os.getpid()}")
        try:
            tmp.write_text(
                json.dumps(self._d, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            os.replace(tmp, fl)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return fl

    # -- header ---------------------------------------------------------------

    @property
    def format(self) -> str:
        return str(self._d["format"])

    @property
    def version(self) -> str:
        return str(self._d["version"])

    @property
    def generation(self) -> int:
        return int(self._d["generation"])

    def bump_generation(self) -> Manifest:
        """Increment the generation counter, marking every cache keyed on it
        as stale."""
        self._d["generation"] = self.generation + 1
        return self

    # -- runs -----------------------------------------------------------------

    @property
    def runs(self) -> list[Run]:
        return [Run(r) for r in self._d["runs"]]

    def run(self, run_id: str) -> Run:
        for r in self._d["runs"]:
            if str(r.get("run_id")) == run_id:
                return Run(r)
        raise FormatError(f"No run '{run_id}' in this dataset.")

    @property
    def n_spectra(self) -> int:
        """Total number of spectra across all runs."""
        return sum(r.n_spectra for r in self.runs)

    @property
    def kind(self) -> str:
        """What kind of signal this dataset holds: ``mzpeak`` or ``native``.

        ``native`` for a dataset with no runs yet; a converter writes the
        spectra, counts them, and only then writes the manifest.

        Raises:
            FormatError: if the runs are of more than one kind.
        """
        kinds = {r.kind for r in self.runs}
        if not kinds:
            return layout.KIND_NATIVE
        if len(kinds) > 1:
            raise FormatError(
                "Dataset mixes run kinds ("
                + ", ".join(sorted(kinds))
                + "), which is not supported."
            )
        return next(iter(kinds))

    def next_uid_base(self) -> int:
        """The ``spectrum_id_`` a new run's spectra start at.

        Ids are allocated in contiguous blocks, one per run in the order added,
        so run *k* covers ``uid_base .. uid_base + n_spectra - 1``. An untouched
        dataset's ids are therefore exactly ``1 .. n_spectra`` and can be
        derived rather than scanned for, and adding a run never renumbers an
        existing one.
        """
        runs = self.runs
        if not runs:
            return 1
        return max(r.uid_base + r.n_spectra for r in runs)

    def add_run(
        self,
        run_id: str,
        kind: str,
        path: str | None,
        n_spectra: int,
        layout_: str,
        profile: str | None = None,
        centroid: str | None = None,
        partitioning: Sequence[str] = (),
    ) -> Manifest:
        """Append a run entry."""
        if kind not in layout.KINDS:
            raise FormatError(
                f"Unknown run kind '{kind}'; expected one of "
                + ", ".join(layout.KINDS)
                + "."
            )
        if any(str(r.get("run_id")) == run_id for r in self._d["runs"]):
            raise FormatError(f"A run with id '{run_id}' is already in this dataset.")

        signal: dict[str, Any] = {
            "layout": layout_,
            "profile": profile,
            "centroid": centroid,
        }
        if len(partitioning):
            signal["partitioning"] = [str(p) for p in partitioning]

        self._d["runs"].append(
            {
                "run_id": run_id,
                "kind": kind,
                "path": path,
                "n_spectra": int(n_spectra),
                "uid_base": self.next_uid_base(),
                "ingested_at": self.generation,
                "signal": signal,
                "projections": {},
            }
        )
        return self

    # -- projections ----------------------------------------------------------

    def set_projection(self, run_id: str, type: str) -> Manifest:
        """Record that a projection of ``type`` has been built for a run."""
        for r in self._d["runs"]:
            if str(r.get("run_id")) == run_id:
                p = r.get("projections")
                if not isinstance(p, dict):
                    p = {}
                    r["projections"] = p
                p[type] = {"generation": Run(r).ingested_at}
                return self
        raise FormatError(f"No run '{run_id}' in this dataset.")

    def drop_projection(self, run_id: str, type: str) -> Manifest:
        for r in self._d["runs"]:
            if str(r.get("run_id")) == run_id:
                p = r.get("projections")
                if isinstance(p, dict):
                    p.pop(type, None)
                return self
        return self

    def has_projection(
        self, type: str, run_ids: Iterable[str] | None = None
    ) -> list[str] | bool:
        """Which runs have a current projection of ``type``?

        Returns their ids, or -- given ``run_ids`` -- whether all of them do.
        """
        have = [r.run_id for r in self.runs if r.has_projection(type)]
        if run_ids is None:
            return have
        return set(run_ids).issubset(have)

    # -- id resolution --------------------------------------------------------

    def split_ids(self, ids: Sequence[int]) -> list[IdBlock]:
        """Map global ``spectrum_id_`` values onto the runs holding them.

        Returns one `IdBlock` per involved run, in the order the runs first
        appear in ``ids``.

        Raises:
            FormatError: if any id falls in no run.
        """
        runs = sorted(self.runs, key=lambda r: r.uid_base)
        if not runs or not len(ids):
            return []

        bases = [r.uid_base for r in runs]
        order: list[int] = []
        grouped: dict[int, list[int]] = {}
        bad: list[int] = []

        for sid in ids:
            # The last run whose block starts at or before `sid`.
            k = bisect_right(bases, sid) - 1
            if k < 0 or sid >= runs[k].uid_base + runs[k].n_spectra:
                bad.append(sid)
                continue
            if k not in grouped:
                grouped[k] = []
                order.append(k)
            grouped[k].append(sid)

        if bad:
            shown = ", ".join(str(b) for b in bad[:5])
            raise FormatError(f"spectrum id(s) outside the dataset: {shown}")

        out = []
        for k in order:
            block = grouped[k]
            base = runs[k].uid_base
            out.append(
                IdBlock(
                    run=runs[k],
                    ids=tuple(block),
                    local=tuple(sid - base for sid in block),
                )
            )
        return out

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"Manifest(version={self.version!r}, generation={self.generation}, "
            f"runs={len(self._d['runs'])}, n_spectra={self.n_spectra})"
        )


# --- module-level convenience ------------------------------------------------


def dataset_kind(path: str | Path) -> str:
    """What kind of signal the dataset at ``path`` holds, or ``native`` when
    there is no manifest yet."""
    if not layout.is_mzstack_dataset(path):
        return layout.KIND_NATIVE
    return Manifest.read(path).kind


def write_native_manifest(
    path: str | Path,
    n_spectra: int,
    partitioning: Sequence[str] = (),
) -> Path:
    """Write the manifest for a converted dataset: one run covering the whole
    ``<dataset>/spectra`` directory.

    Called at the end of every conversion path, once the spectra are counted.
    """
    m = Manifest.new().add_run(
        run_id=layout.NATIVE_RUN_ID,
        kind=layout.KIND_NATIVE,
        path=str(layout.spectra_path(path).resolve()),
        n_spectra=n_spectra,
        layout_=layout.LAYOUT_LIST,
        partitioning=partitioning,
    )
    return m.write(path)
