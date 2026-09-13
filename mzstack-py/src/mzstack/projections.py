"""Building and dropping signal projections.

A projection is a re-ordered copy of a run's signal, one row per data point:
``mzsorted`` by m/z, ``scansorted`` by ``(spectrum_id_, mz)``. In that order
Parquet's per-block statistics prune a filter on the sort column.

A projection is a cache: derivable, droppable, and never required for
correctness.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from pathlib import Path

from . import duck
from .errors import MzStackError
from .format import layout
from .format.columnmap import quote_ident, quote_string
from .format.manifest import Manifest

__all__ = ["build_projection", "drop_projection", "PROJECTION_TYPES"]

PROJECTION_TYPES = (layout.PROJECTION_SCANSORTED, layout.PROJECTION_MZSORTED)

#: What each type is ordered by, as a SQL ORDER BY clause.
_ORDER_BY = {
    layout.PROJECTION_SCANSORTED: '"spectrum_id_", "mz"',
    layout.PROJECTION_MZSORTED: '"mz"',
}


def _native_points_sql(dataset: Path) -> str:
    """One row per data point for a native run, from its list columns."""
    view = quote_ident(duck.dataset_view(dataset))
    return (
        "SELECT "
        f"{quote_ident('spectrum_id_')}, "
        'unnest("mz") AS "mz", '
        'unnest("intensity") AS "intensity" '
        f"FROM {view}"
    )


def _run_points_sql(dataset: Path, manifest: Manifest, run_id: str) -> str:
    """One row per data point for one run, whatever its kind."""
    run = manifest.run(run_id)
    if run.kind == layout.KIND_NATIVE:
        return _native_points_sql(dataset)

    from .mzpeak.signal import run_points_sql

    return run_points_sql(run)


def build_projection(
    path: str | Path,
    type: str = layout.PROJECTION_SCANSORTED,
    runs: Iterable[str] | None = None,
    verbose: bool = True,
) -> Path:
    """Write a re-ordered copy of a dataset's signal.

    Args:
        path: the dataset directory.
        type: ``scansorted`` or ``mzsorted``.
        runs: which runs to build for; every run by default.
        verbose: report each run as it is built.

    Returns:
        The dataset path.
    """
    if type not in PROJECTION_TYPES:
        raise MzStackError(
            f"Unknown projection type {type!r}; expected one of "
            + ", ".join(PROJECTION_TYPES)
        )

    dataset = Path(path).resolve()
    manifest = Manifest.read(dataset)
    wanted = list(runs) if runs is not None else [r.run_id for r in manifest.runs]
    if not wanted:
        raise MzStackError(f"No matching runs in '{dataset}'.")

    con = duck.connection()
    for run_id in wanted:
        run = manifest.run(run_id)  # raises if unknown
        dest_dir = layout.projection_run_path(dataset, type, run.run_id)
        # Rebuild from scratch, so a partial projection is never left behind
        # matching the current generation.
        shutil.rmtree(dest_dir, ignore_errors=True)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / "part-0.parquet"

        con.execute(
            "COPY ("
            + _run_points_sql(dataset, manifest, run.run_id)
            + f" ORDER BY {_ORDER_BY[type]}"
            + ") TO "
            + quote_string(str(dest))
            + " (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE "
            + str(layout.PROJECTION_ROW_GROUP_SIZE)
            + ")"
        )
        manifest.set_projection(run.run_id, type)
        if verbose:
            print(f"  {run.run_id}: {type} projection built")

    manifest.write(dataset)
    duck.invalidate(dataset)
    return dataset


def drop_projection(
    path: str | Path,
    type: str = layout.PROJECTION_SCANSORTED,
    runs: Iterable[str] | None = None,
    verbose: bool = True,
) -> Path:
    """Delete a projection, freeing its disk. Results are unaffected."""
    dataset = Path(path).resolve()
    manifest = Manifest.read(dataset)
    wanted = list(runs) if runs is not None else [r.run_id for r in manifest.runs]

    for run_id in wanted:
        shutil.rmtree(
            layout.projection_run_path(dataset, type, run_id), ignore_errors=True
        )
        manifest.drop_projection(run_id, type)
        if verbose:
            print(f"  {run_id}: {type} projection dropped")

    directory = layout.projection_path(dataset, type)
    if directory.is_dir() and not any(directory.iterdir()):
        directory.rmdir()

    manifest.write(dataset)
    duck.invalidate(dataset)
    return dataset
