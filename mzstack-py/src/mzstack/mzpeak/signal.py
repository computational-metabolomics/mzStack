"""Reading peaks out of the mzPeak archives a dataset indexes.

An archive stores a 0-based ``spectrum_index`` local to itself, so a run's
``uid_base`` is the offset to the dataset's global ``spectrum_id_``.

One query per archive rather than one per spectrum.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..format.columnmap import quote_ident, quote_string
from ..format.manifest import Run
from .archive import read_archive, signal_columns

if TYPE_CHECKING:  # pragma: no cover
    from ..signal import PointSource
    from ..store import MzStack

__all__ = ["run_points_sql", "archive_source"]


def run_points_sql(run: Run, representation: str = "auto") -> str:
    """One row per data point for an mzpeak run, with **global** ids.

    The archive stores a 0-based ``spectrum_index`` local to itself; the
    dataset addresses spectra by a global ``spectrum_id_``. The run's
    ``uid_base`` is the offset between them.
    """
    archive = read_archive(run.path)
    signal = archive.signal_file(representation)
    columns = signal_columns(signal)

    return (
        "SELECT CAST("
        f"{run.uid_base} + {columns.index} AS INTEGER) "
        f"AS {quote_ident('spectrum_id_')}, "
        f'{columns.mz} AS "mz", '
        f'{columns.intensity} AS "intensity" '
        f"FROM read_parquet({quote_string(str(signal))})"
    )


def archive_source(store: MzStack) -> PointSource:
    """A one-row-per-point relation spanning every archive in the dataset."""
    from ..signal import PointSource

    parts = [
        run_points_sql(run, store.representation)
        for run in store.manifest.runs
        if run.path is not None
    ]
    if not parts:
        # An empty dataset still needs a well-typed relation to join against.
        parts = [
            "SELECT CAST(NULL AS INTEGER) AS "
            + quote_ident("spectrum_id_")
            + ', CAST(NULL AS DOUBLE) AS "mz"'
            + ', CAST(NULL AS DOUBLE) AS "intensity" WHERE FALSE'
        ]

    return PointSource(
        "(" + " UNION ALL ".join(parts) + ")",
        kind="mzpeak:archive",
        ordered_by="spectrum_id_",
    )


def peaks_source_for_runs(store: MzStack, run_ids: list[str]) -> str:
    """A point relation restricted to the named runs."""
    parts = [
        run_points_sql(run, store.representation)
        for run in store.manifest.runs
        if run.run_id in run_ids and run.path is not None
    ]
    return "(" + " UNION ALL ".join(parts) + ")"
