"""`MzStack`: reading and filtering an mzStack dataset.

A store is a path plus a selection. Filters read nothing: they accumulate a
SQL predicate and return a new store, so a later fetch pushes the predicate
into the Parquet scan instead of listing ids. Filters that must look at the
signal, such as `MzStack.filter_contains_mz`, materialise ids instead and the
store carries those.

Rows come back ordered by ``spectrum_id_``, unless an explicit non-ascending
id set was given, in which case that order is preserved.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from . import duck
from .errors import FormatError, MzStackError
from .format import layout
from .format.columnmap import quote_ident
from .format.manifest import Manifest
from .predicates import (
    pred_and,
    pred_in,
    pred_in_strings,
    pred_mz_windows,
    pred_not,
    pred_or,
    pred_range,
)

if TYPE_CHECKING:  # pragma: no cover
    import pandas as pd

__all__ = ["MzStack", "Peaks"]

#: Beyond this many scattered ids, inlining them into the predicate stops
#: paying and a registered temp table is cheaper.
MAX_INLINE_IDS = 1024

#: A peak list: parallel m/z and intensity arrays, ascending in m/z.
Peaks = tuple[np.ndarray, np.ndarray]


def _is_ascending(ids: Sequence[int]) -> bool:
    return all(a < b for a, b in zip(ids, ids[1:], strict=False))


def _is_contiguous(ids: Sequence[int]) -> bool:
    return bool(len(ids)) and ids[-1] - ids[0] == len(ids) - 1 and _is_ascending(ids)


class MzStack:
    """A view over an mzStack dataset.

    Args:
        path: the dataset directory, containing ``mzStack.json``.
        representation: which signal to read when a run holds both a profile
            and a centroid copy; ``auto`` prefers centroids.
    """

    __slots__ = ("_path", "_manifest", "_predicate", "_ids", "_representation")

    def __init__(
        self,
        path: str | Path,
        representation: str = "auto",
        *,
        _manifest: Manifest | None = None,
        _predicate: str | None = None,
        _ids: tuple[int, ...] | None = None,
    ) -> None:
        if representation not in ("auto", "profile", "centroid"):
            raise ValueError(
                "representation must be 'auto', 'profile' or 'centroid', "
                f"not {representation!r}"
            )
        self._path = Path(path).resolve()
        self._manifest = (
            _manifest if _manifest is not None else Manifest.read(self._path)
        )
        self._predicate = _predicate
        self._ids = _ids
        self._representation = representation

    # -- identity -------------------------------------------------------------

    @property
    def path(self) -> Path:
        return self._path

    @property
    def manifest(self) -> Manifest:
        return self._manifest

    @property
    def kind(self) -> str:
        """``native`` or ``mzpeak``."""
        return self._manifest.kind

    @property
    def representation(self) -> str:
        return self._representation

    def _view(self) -> str:
        return duck.dataset_view(self._path)

    def _with(
        self,
        predicate: str | None = None,
        ids: tuple[int, ...] | None = None,
    ) -> MzStack:
        return MzStack(
            self._path,
            self._representation,
            _manifest=self._manifest,
            _predicate=predicate,
            _ids=ids,
        )

    def _narrow(self, predicate: str | None) -> MzStack:
        """This store's rows that also satisfy ``predicate``."""
        if predicate is None:
            return self
        if self._ids is not None:
            # An id set cannot absorb a predicate without a read.
            ids = tuple(self._query_ids(extra=predicate))
            return self._with(ids=ids)
        return self._with(predicate=pred_and(self._predicate, predicate))

    # -- SQL assembly ---------------------------------------------------------

    def _where(self, extra: str | None = None) -> tuple[str, str, str | None]:
        """The FROM, WHERE and ORDER BY parts for the current selection.

        ``from_sql`` may join a registered temp table, which is how a large or
        arbitrarily-ordered id set is applied without inlining it.
        """
        view = quote_ident(self._view())
        ids = self._ids
        order = quote_ident("spectrum_id_")

        if ids is None:
            where = pred_and(self._predicate, extra)
            return view, (f"WHERE {where}" if where else ""), order

        if not ids:
            return view, "WHERE FALSE", order

        if _is_contiguous(ids):
            restriction = (
                f"{quote_ident('spectrum_id_')} BETWEEN {ids[0]} AND {ids[-1]}"
            )
        elif len(ids) <= MAX_INLINE_IDS and _is_ascending(ids):
            restriction = pred_in("spectrum_id_", ids)
        else:
            # Too many or out of order: join a temp table whose `ord_`
            # restores the caller's ordering.
            name = f"ids_{abs(hash((self._path, ids))) & 0xFFFFFFFF:08x}"
            duck.register_ids(ids, name)
            joined = (
                f"{view} AS s JOIN {quote_ident(name)} AS i "
                f"USING ({quote_ident('spectrum_id_')})"
            )
            where = pred_and(extra)
            return joined, (f"WHERE {where}" if where else ""), quote_ident("ord_")

        where = pred_and(restriction, extra)
        return view, f"WHERE {where}", order

    def _select(self, columns: str, extra: str | None = None) -> str:
        from_sql, where_sql, order_sql = self._where(extra)
        sql = f"SELECT {columns} FROM {from_sql} {where_sql}"
        if order_sql:
            sql += f" ORDER BY {order_sql}"
        return sql

    def _query_ids(self, extra: str | None = None) -> np.ndarray:
        con = duck.connection()
        sql = self._select(quote_ident("spectrum_id_"), extra)
        col = con.execute(sql).to_arrow_table().column(0)
        return col.to_numpy(zero_copy_only=False).astype(np.int64, copy=False)

    # -- size and variables ---------------------------------------------------

    def __len__(self) -> int:
        if self._predicate is None and self._ids is None:
            # No selection: the manifest knows, without a read.
            return self._manifest.n_spectra
        if self._ids is not None:
            return len(self._ids)
        con = duck.connection()
        from_sql, where_sql, _ = self._where()
        return int(
            con.execute(f"SELECT count(*) FROM {from_sql} {where_sql}").fetchone()[0]
        )

    def spectra_variables(self) -> list[str]:
        """Variables this dataset exposes."""
        return duck.table_columns(quote_ident(self._view()))

    @property
    def spectrum_ids(self) -> np.ndarray:
        """The ``spectrum_id_`` values currently selected, in result order."""
        if self._ids is not None:
            return np.asarray(self._ids, dtype=np.int64)
        if self._predicate is None:
            n = self._manifest.n_spectra
            return np.arange(1, n + 1, dtype=np.int64)
        return self._query_ids()

    # -- data -----------------------------------------------------------------

    def spectra_data(self, columns: Sequence[str] | None = None) -> pd.DataFrame:
        """Spectrum metadata, one row per spectrum.

        Peak columns are excluded unless named; use `peaks_data` for those.
        """
        available = self.spectra_variables()
        if columns is None:
            wanted = [c for c in available if c not in ("mz", "intensity")]
        else:
            missing = [c for c in columns if c not in available]
            if missing:
                raise MzStackError("No such spectra variable(s): " + ", ".join(missing))
            wanted = list(columns)

        select = ", ".join(quote_ident(c) for c in wanted)
        con = duck.connection()
        return con.execute(self._select(select)).df()

    def peaks_data(self) -> list[Peaks]:
        """Peak arrays, one ascending-in-m/z ``(mz, intensity)`` pair per
        selected spectrum."""
        if self.kind == layout.KIND_NATIVE:
            return self._peaks_from_lists()
        return self._peaks_from_points()

    def _peaks_from_lists(self) -> list[Peaks]:
        """Native runs: peaks are ``list<double>`` beside the metadata."""
        con = duck.connection()
        select = ", ".join(quote_ident(c) for c in ("mz", "intensity"))
        tbl = con.execute(self._select(select)).to_arrow_table()
        mz_col, i_col = tbl.column("mz"), tbl.column("intensity")

        out: list[Peaks] = []
        for mz_scalar, i_scalar in zip(mz_col, i_col, strict=True):
            mz = _as_float_array(mz_scalar)
            it = _as_float_array(i_scalar)
            out.append((mz, it))
        return out

    def _peaks_from_points(self) -> list[Peaks]:
        """mzPeak runs: signal is one row per data point, in the archives."""
        from .signal import peaks_for_ids

        return peaks_for_ids(self, self.spectrum_ids)

    # -- filters --------------------------------------------------------------

    def filter_ms_level(self, ms_level: int | Iterable[int]) -> MzStack:
        """Spectra of the given MS level(s)."""
        levels = [ms_level] if isinstance(ms_level, int) else list(ms_level)
        return self._narrow(pred_in("msLevel", levels))

    def filter_rt(
        self,
        rt: tuple[float, float],
        ms_level: int | Iterable[int] | None = None,
    ) -> MzStack:
        """Spectra with ``rtime`` in ``rt``, **in seconds**.

        With ``ms_level``, only those levels are filtered and spectra of other
        levels are kept whatever their retention time -- so MS1 scans can be
        narrowed without discarding the MS2 scans acquired alongside them.
        """
        lo, hi = rt
        in_range = pred_range("rtime", lo, hi)
        if ms_level is None:
            return self._narrow(in_range)

        levels = [ms_level] if isinstance(ms_level, int) else list(ms_level)
        in_level = pred_in("msLevel", levels)
        return self._narrow(pred_or(pred_and(in_range, in_level), pred_not(in_level)))

    def filter_data_origin(self, data_origin: str | Iterable[str]) -> MzStack:
        """Spectra from the given source file(s)."""
        origins = [data_origin] if isinstance(data_origin, str) else list(data_origin)
        return self._narrow(pred_in_strings("dataOrigin", origins))

    def filter_polarity(self, polarity: int | Iterable[int]) -> MzStack:
        """Spectra of the given polarity: 1 positive, 0 negative."""
        values = [polarity] if isinstance(polarity, int) else list(polarity)
        return self._narrow(pred_in("polarity", values))

    def filter_precursor_mz_range(self, mz: tuple[float, float]) -> MzStack:
        """Spectra whose precursor m/z falls in ``mz``."""
        return self._narrow(pred_range("precursorMz", mz[0], mz[1]))

    def filter_precursor_mz_values(
        self,
        mz: float | Sequence[float],
        tolerance: float = 0.0,
        ppm: float = 20.0,
    ) -> MzStack:
        """Spectra whose precursor m/z matches any of ``mz``, the windows
        OR-ed into one predicate so the set costs a single scan."""
        values = [mz] if isinstance(mz, int | float) else list(mz)
        return self._narrow(
            pred_mz_windows("precursorMz", values, tolerance=tolerance, ppm=ppm)
        )

    def filter_contains_mz(
        self,
        mz: float | Sequence[float],
        tolerance: float = 0.0,
        ppm: float = 20.0,
    ) -> MzStack:
        """Spectra containing a peak at any of ``mz``.

        Reads the signal, so this materialises ids rather than carrying a
        predicate. Uses an ``mzsorted`` or ``scansorted`` projection if one is
        current.
        """
        from .signal import ids_containing_mz

        values = [mz] if isinstance(mz, int | float) else list(mz)
        ids = ids_containing_mz(self, values, tolerance=tolerance, ppm=ppm)
        return self._with(ids=tuple(int(i) for i in ids))

    def select_ids(self, ids: Sequence[int]) -> MzStack:
        """A store holding exactly ``ids``, in the order given."""
        return self._with(ids=tuple(int(i) for i in ids))

    def reset(self) -> MzStack:
        """Drop every filter, returning the whole dataset."""
        return self._with()

    # -- MassQL ---------------------------------------------------------------

    def massql(self, query: str, engine: str = "sql", **kwargs: Any) -> pd.DataFrame:
        """Run a MassQL query over the selected spectra.

        Args:
            query: the MassQL query.
            engine: ``sql`` compiles to a single DuckDB statement where it can
                and falls back to massql's evaluator otherwise; ``engine``
                always uses the evaluator. The default answers a scan-level
                query without materialising peaks, which on a profile dataset
                is the difference between a second and a minute.
        """
        from .query.massql import run_query

        return run_query(self, query, engine=engine, **kwargs)

    # -- display --------------------------------------------------------------

    def __repr__(self) -> str:
        sel = "all"
        if self._ids is not None:
            sel = f"{len(self._ids)} ids"
        elif self._predicate is not None:
            sel = "filtered"
        return (
            f"MzStack({str(self._path)!r}, kind={self.kind!r}, "
            f"runs={len(self._manifest.runs)}, selection={sel})"
        )


def _as_float_array(scalar: Any) -> np.ndarray:
    """A pyarrow list scalar as a float64 numpy array."""
    if scalar is None or not scalar.is_valid:
        return np.empty(0, dtype=np.float64)
    values = scalar.values
    return np.asarray(values.to_numpy(zero_copy_only=False), dtype=np.float64)


def open_dataset(path: str | Path, representation: str = "auto") -> MzStack:
    """Open the mzStack dataset at ``path``.

    Raises:
        FormatError: if ``path`` holds no manifest.
    """
    if not layout.is_mzstack_dataset(path):
        raise FormatError(
            f"'{path}' is not an mzStack dataset: no {layout.MANIFEST_NAME}."
        )
    return MzStack(path, representation)
