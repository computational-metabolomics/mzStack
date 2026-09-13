"""Turning pandas and numpy into JSON the browser can actually parse.

Two hazards live here. `NaN` is not valid JSON but pandas produces it for
every absent value, and numpy scalars are not JSON serialisable at all.
`records` handles both, so no endpoint does.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

__all__ = ["decimate", "records", "scalar", "table"]


def scalar(value: Any) -> Any:
    """One cell, as a JSON-safe Python value.

    Absent values reach here in four shapes -- ``None``, ``float('nan')``,
    ``pd.NaT`` and ``pd.NA`` -- depending on the column's dtype, and only the
    first survives JSON. They all become ``null``.
    """
    if value is None:
        return None
    if isinstance(value, np.ndarray):
        return [scalar(v) for v in value.tolist()]
    if isinstance(value, (list, tuple)):
        return [scalar(v) for v in value]
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        # Not a scalar pandas understands; carry on and let the checks below
        # decide.
        pass
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        # +-inf has no JSON spelling either.
        return None
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return value


def records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """A DataFrame as a list of JSON-safe row dicts."""
    columns = list(df.columns)
    return [
        {col: scalar(value) for col, value in zip(columns, row, strict=True)}
        for row in df.itertuples(index=False, name=None)
    ]


def table(df: pd.DataFrame) -> dict[str, Any]:
    """A DataFrame as columns plus rows, the shape the UI's table expects."""
    return {"columns": [str(c) for c in df.columns], "rows": records(df)}


def decimate(
    mz: np.ndarray, intensity: np.ndarray, max_points: int
) -> tuple[list[float], list[float], bool]:
    """Peak arrays as plain lists, thinned to at most `max_points` points.

    Profile spectra routinely carry hundreds of thousands of points, most of
    them baseline. Thinning by bucket keeps the tallest peak in each bucket
    rather than every n-th point, so a plotted spectrum keeps its peaks and
    only loses the shape of the noise between them.
    """
    n = len(mz)
    if n <= max_points:
        return mz.tolist(), intensity.tolist(), False

    # One bucket per output point; take the most intense point in each.
    edges = np.linspace(0, n, max_points + 1, dtype=np.int64)
    keep = np.empty(max_points, dtype=np.int64)
    for i in range(max_points):
        lo, hi = edges[i], edges[i + 1]
        if hi <= lo:
            keep[i] = lo if lo < n else n - 1
        else:
            keep[i] = lo + int(np.argmax(intensity[lo:hi]))
    keep = np.unique(keep)
    return mz[keep].tolist(), intensity[keep].tolist(), True
