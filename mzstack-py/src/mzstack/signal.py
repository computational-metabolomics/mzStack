"""Reading peak arrays, and finding spectra that contain a given m/z.

Signal comes from the first available of: a ``scansorted`` projection, an
``mzsorted`` projection, or the run's own storage (``list<double>`` columns
for a native run, the external archive for an mzpeak one). All three give the
same answers; only the speed differs.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

import numpy as np

from . import duck
from .errors import MzStackError
from .format import layout
from .format.columnmap import quote_ident
from .predicates import pred_mz_windows, pred_range

if TYPE_CHECKING:  # pragma: no cover
    from .store import MzStack, Peaks

__all__ = [
    "point_source",
    "peaks_for_ids",
    "ids_containing_mz",
    "PointSource",
]


class PointSource:
    """A relation with one row per data point: ``spectrum_id_, mz, intensity``."""

    __slots__ = ("sql", "kind", "ordered_by")

    def __init__(self, sql: str, kind: str, ordered_by: str) -> None:
        self.sql = sql
        self.kind = kind
        self.ordered_by = ordered_by

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"PointSource(kind={self.kind!r}, ordered_by={self.ordered_by!r})"


def _projection_source(store: MzStack, type: str) -> PointSource | None:
    """A projection of ``type``, if every run in the dataset has a current one."""
    manifest = store.manifest
    run_ids = [r.run_id for r in manifest.runs]
    if not run_ids or not manifest.has_projection(type, run_ids):
        return None

    directory = layout.projection_path(store.path, type)
    if not directory.is_dir():
        return None

    ordered = "spectrum_id_" if type == layout.PROJECTION_SCANSORTED else "mz"
    return PointSource(
        "read_parquet(" + duck.parquet_glob(directory) + ", hive_partitioning = true)",
        kind=f"projection:{type}",
        ordered_by=ordered,
    )


def _native_source(store: MzStack) -> PointSource:
    """A native run's list columns, exploded to one row per data point.

    Both peak columns are unnested in one SELECT, which keeps m/z and
    intensity aligned.
    """
    view = quote_ident(duck.dataset_view(store.path))
    sql = (
        "(SELECT "
        f"{quote_ident('spectrum_id_')}, "
        'unnest("mz") AS "mz", '
        'unnest("intensity") AS "intensity" '
        f"FROM {view})"
    )
    return PointSource(sql, kind="native", ordered_by="spectrum_id_")


def point_source(store: MzStack, prefer: str | None = None) -> PointSource:
    """The best available one-row-per-point source for ``store``.

    Args:
        prefer: projection type to try first -- ``mzsorted`` when filtering on
            m/z, ``scansorted`` when grouping by spectrum.
    """
    order = [prefer] if prefer else []
    order += [
        t
        for t in (layout.PROJECTION_SCANSORTED, layout.PROJECTION_MZSORTED)
        if t != prefer
    ]
    for type in order:
        src = _projection_source(store, type)
        if src is not None:
            return src

    if store.kind == layout.KIND_NATIVE:
        return _native_source(store)

    from .mzpeak.signal import archive_source

    return archive_source(store)


def _id_restriction(ids: np.ndarray) -> str:
    """A predicate restricting a point source to ``ids``.

    A range rather than a membership test: it prunes row groups, and exact
    membership is settled by the caller. Exact already for a contiguous set.
    """
    if not len(ids):
        return "FALSE"
    return pred_range("spectrum_id_", int(ids.min()), int(ids.max())) or "TRUE"


def peaks_for_ids(store: MzStack, ids: np.ndarray) -> list[Peaks]:
    """Peak arrays for ``ids``, in the order given.

    One ordered fetch, grouped back into arrays with `numpy.searchsorted`,
    rather than a query per spectrum.
    """
    ids = np.asarray(ids, dtype=np.int64)
    if not len(ids):
        return []

    src = point_source(store, prefer=layout.PROJECTION_SCANSORTED)
    con = duck.connection()
    sql = (
        f'SELECT {quote_ident("spectrum_id_")}, "mz", "intensity" '
        f"FROM {src.sql} WHERE {_id_restriction(ids)} "
        f'ORDER BY {quote_ident("spectrum_id_")}, "mz"'
    )
    tbl = con.execute(sql).to_arrow_table()

    sid = tbl.column("spectrum_id_").to_numpy(zero_copy_only=False).astype(np.int64)
    mz = tbl.column("mz").to_numpy(zero_copy_only=False).astype(np.float64)
    it = tbl.column("intensity").to_numpy(zero_copy_only=False).astype(np.float64)

    # `sid` is ascending, so each spectrum's rows form one contiguous slice.
    starts = np.searchsorted(sid, ids, side="left")
    stops = np.searchsorted(sid, ids, side="right")
    return [(mz[a:b], it[a:b]) for a, b in zip(starts, stops, strict=True)]


def ids_containing_mz(
    store: MzStack,
    mz: Sequence[float],
    tolerance: float = 0.0,
    ppm: float = 20.0,
) -> np.ndarray:
    """Which of ``store``'s spectra contain a peak at any of ``mz``, as
    ascending ``spectrum_id_``.

    Runs in the storage engine rather than pulling peaks into Python.
    """
    if not len(mz):
        return np.empty(0, dtype=np.int64)

    selected = store.spectrum_ids
    if not len(selected):
        return np.empty(0, dtype=np.int64)

    src = point_source(store, prefer=layout.PROJECTION_MZSORTED)
    windows = pred_mz_windows("mz", list(mz), tolerance=tolerance, ppm=ppm)

    con = duck.connection()
    sql = (
        f"SELECT DISTINCT {quote_ident('spectrum_id_')} FROM {src.sql} "
        f"WHERE {windows} AND {_id_restriction(selected)} "
        f"ORDER BY {quote_ident('spectrum_id_')}"
    )
    found = (
        con.execute(sql)
        .to_arrow_table()
        .column(0)
        .to_numpy(zero_copy_only=False)
        .astype(np.int64)
    )

    # The range restriction above is a superset when the selection has gaps.
    if len(found) and len(selected) != (selected.max() - selected.min() + 1):
        found = found[np.isin(found, selected)]
    return found


def require_projection(store: MzStack, type: str) -> None:
    """Raise unless every run has a current projection of ``type``."""
    run_ids = [r.run_id for r in store.manifest.runs]
    if not store.manifest.has_projection(type, run_ids):
        raise MzStackError(
            f"This dataset has no usable '{type}' projection. "
            f"Build one with build_projection(path, '{type}')."
        )
