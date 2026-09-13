"""DuckDB connection and the per-dataset view registry.

One in-memory connection per process, keyed by the pid that created it. A
forked child inherits a handle to the parent's database and drops the
reference without closing it.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import duckdb

from .format import layout
from .format.columnmap import quote_ident, view_select_sql
from .format.manifest import Manifest

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Iterable

__all__ = [
    "connection",
    "dataset_view",
    "invalidate",
    "close",
    "table_columns",
    "parquet_glob",
]


class _State:
    """Package-level DuckDB state, rebuilt after a fork."""

    def __init__(self) -> None:
        self.con: duckdb.DuckDBPyConnection | None = None
        self.pid: int | None = None
        #: normalised dataset path -> SQL view name
        self.views: dict[str, str] = {}
        self.counter: int = 0
        self.lock = threading.Lock()

    def reset_for_new_process(self) -> None:
        # Drop every reference without touching the parent's database.
        self.con = None
        self.views = {}
        self.counter = 0


_state = _State()


def _configure(con: duckdb.DuckDBPyConnection) -> None:
    """Session settings applied once per connection.

    ``preserve_insertion_order`` stays on; fetches return rows in
    spectrum-id order.
    """
    # Off by default, which re-decodes every Parquet footer on every query.
    settings = ["SET parquet_metadata_cache = true"]

    threads = os.environ.get("MZSTACK_THREADS")
    if threads:
        settings.append(f"SET threads = {int(threads)}")
    memory = os.environ.get("MZSTACK_MEMORY_LIMIT")
    if memory:
        settings.append(f"SET memory_limit = '{memory}'")

    for s in settings:
        try:
            con.execute(s)
        except duckdb.Error:
            # A setting this build does not know about is skipped.
            pass


def connection() -> duckdb.DuckDBPyConnection:
    """The process-level DuckDB connection, opened on first use."""
    pid = os.getpid()
    if _state.con is not None and _state.pid == pid:
        return _state.con

    with _state.lock:
        if _state.con is not None and _state.pid == pid:
            return _state.con
        if _state.con is not None and _state.pid != pid:
            _state.reset_for_new_process()

        con = duckdb.connect(":memory:")
        _configure(con)
        _state.con = con
        _state.pid = pid
        _state.views = {}
        return con


def close() -> None:
    """Close the connection and forget every view. Mainly for tests."""
    with _state.lock:
        if _state.con is not None and _state.pid == os.getpid():
            _state.con.close()
        _state.con = None
        _state.pid = None
        _state.views = {}


def invalidate(path: str | Path | None = None) -> None:
    """Forget the cached view for ``path``, or for every dataset."""
    with _state.lock:
        if path is None:
            _state.views = {}
            return
        for key in (str(path), str(Path(path).resolve())):
            _state.views.pop(key, None)


def _next_view_name() -> str:
    _state.counter += 1
    return f"spectra_{_state.counter}"


def parquet_glob(directory: str | Path) -> str:
    """Recursive Parquet glob for ``directory``, as a SQL string literal."""
    pattern = str(Path(directory) / "**" / "*.parquet")
    return "'" + pattern.replace("'", "''") + "'"


def _read_parquet(directory: str | Path, *, union_by_name: bool = False) -> str:
    """SQL expression reading every Parquet file under ``directory``."""
    args = [parquet_glob(directory), "hive_partitioning = true"]
    if union_by_name:
        # Datasets differ in which optional columns they carry; union by
        # name so missing ones read as NULL rather than failing the scan.
        args.append("union_by_name = true")
    return "read_parquet(" + ", ".join(args) + ")"


def table_columns(source: str) -> list[str]:
    """Column names of a SQL relation, without reading any rows."""
    con = connection()
    return [d[0] for d in con.execute(f"SELECT * FROM {source} LIMIT 0").description]


def _dataset_sql(path: Path, manifest: Manifest) -> str:
    """The SELECT presenting a dataset with canonical column names.

    One translation serves both kinds: an mzPeak-backed dataset's derived
    index and a native dataset's ``spectra/`` files are both written in
    mzPeak's column vocabulary. Only where the files live differs.
    """
    directory = (
        layout.index_spectra_path(path)
        if manifest.kind == layout.KIND_MZPEAK
        else layout.spectra_path(path)
    )
    source = _read_parquet(directory, union_by_name=True)
    return view_select_sql(table_columns(source), source, str(path))


def dataset_view(path: str | Path) -> str:
    """Register (once) a view over a dataset's Parquet files; return its name.

    A non-normalised path resolves too, paying a ``resolve()`` on the cache
    miss only.
    """
    con = connection()
    key = str(path)
    view = _state.views.get(key)
    if view is not None:
        return view

    resolved = str(Path(path).resolve())
    if resolved != key:
        view = _state.views.get(resolved)
        if view is not None:
            _state.views[key] = view
            return view

    p = Path(resolved)
    manifest = Manifest.read(p)
    sql = _dataset_sql(p, manifest)

    with _state.lock:
        view = _next_view_name()
        con.execute(f"CREATE OR REPLACE VIEW {quote_ident(view)} AS {sql}")
        _state.views[resolved] = view
        _state.views[key] = view
    return view


def register_ids(ids: Iterable[int], name: str) -> None:
    """Register a temp table of spectrum ids, for a set too large or too
    scattered to inline into a predicate.

    The ``ord_`` column lets the join restore the caller's ordering.
    """
    import pyarrow as pa

    ids = list(ids)
    tbl = pa.table(  # noqa: F841 -- referenced by DuckDB replacement scan
        {
            "spectrum_id_": pa.array(ids, pa.int32()),
            "ord_": pa.array(range(len(ids)), pa.int32()),
        }
    )
    con = connection()
    con.register(name, tbl)
