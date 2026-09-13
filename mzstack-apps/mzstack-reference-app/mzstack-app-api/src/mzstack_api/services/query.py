"""Running MassQL queries against a dataset.

A MassQL result is a peak-level frame in MassQL's own vocabulary -- one row per
matching peak, retention time in minutes -- a different table from the one
`/spectra` returns for the same question. This service does not return it. It
takes the frame's ``scan`` column, which holds the dataset-global
``spectrum_id_``, and reads those ids back through the spectra path.

The result is the scans a query selected, described exactly as the spectra
table describes them: the same columns, in the same units, as a field filter.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..errors import BadRequest
from ..serialization import records
from ..store import opened
from .spectra import columns_for

__all__ = ["ENGINES", "SCAN_COLUMN", "run"]

#: ``sql`` compiles the query to a single DuckDB statement where it can and
#: falls back to massql's evaluator otherwise; ``engine`` always evaluates.
#: The default matters here: a scan-level query over profile data takes well
#: under a second compiled and tens of seconds through the evaluator.
ENGINES = ("sql", "engine")

#: The id column in MassQL's own frame, mirroring ``spectrum_id_``.
SCAN_COLUMN = "scan"

#: The id column in the response. The same one `/spectra` reports; the rows
#: are the same rows.
ID_COLUMN = "spectrum_id_"


def run(
    path: Path,
    query: str,
    engine: str = "sql",
    ms_level: int | None = None,
    rt_min: float | None = None,
    rt_max: float | None = None,
    limit: int = 1000,
    offset: int = 0,
) -> dict[str, Any]:
    """The scans `query` selects, as a page of the spectra table.

    Pre-filtering matters here: MassQL builds frames over whatever the store
    selects, so an MS-level or retention-time filter applied first is read as
    a smaller frame rather than filtered afterwards.
    """
    if not query.strip():
        raise BadRequest("The query is empty.")
    if engine not in ENGINES:
        raise BadRequest(
            f"Unknown engine {engine!r}; expected one of " + ", ".join(ENGINES)
        )

    with opened(path) as ds:
        selected = ds
        if ms_level is not None:
            selected = selected.filter_ms_level(ms_level)
        if rt_min is not None or rt_max is not None:
            lo = rt_min if rt_min is not None else float("-inf")
            hi = rt_max if rt_max is not None else float("inf")
            if lo > hi:
                raise BadRequest(f"rt_min ({lo}) is above rt_max ({hi})")
            selected = selected.filter_rt((lo, hi), ms_level=ms_level)

        # A missing massql install raises MzStackError with the exact install
        # command; the error handler passes it through to the user.
        #
        # MassQL has no cursor: the whole frame is evaluated on every call, so
        # paging re-runs the query rather than resuming one.
        df = selected.massql(query, engine=engine)

        scans: list[int] = []
        if SCAN_COLUMN in df.columns:
            scans = sorted({int(v) for v in df[SCAN_COLUMN].dropna().tolist()})

        # A query matching peaks rather than scans yields several rows per
        # spectrum; the table counts spectra, which is what a row addresses.
        #
        # `limit` is the size of a page and `total` the whole result, so a
        # caller reaches every match by paging, as through /spectra. There is
        # no truncation flag.
        total = len(scans)
        page_ids = scans[offset : offset + limit]

        wanted = columns_for(ds, None)
        shape = {
            "columns": wanted,
            "total": total,
            "limit": limit,
            "offset": offset,
            # What the query itself selected, which is the one thing a filter
            # has no equivalent of: every id, not only this page's.
            "scan_column": ID_COLUMN if scans else None,
            "spectrum_ids": scans,
        }
        if not page_ids:
            return {**shape, "rows": []}

        # The ids are dataset-global and the frame already respects the
        # pre-filters, so this reads from `ds` rather than narrowing twice.
        rows = ds.select_ids(page_ids).spectra_data(columns=wanted)
        return {**shape, "rows": records(rows)}
