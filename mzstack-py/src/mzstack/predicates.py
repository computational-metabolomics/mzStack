"""Building the SQL predicates that push down into Parquet scans.

Predicates are strings, which a filtered store carries and re-attaches to a
later fetch.

Two details matter: numbers are formatted with ``%.17g`` so an m/z bound
round-trips through the SQL text exactly, and membership tests are wrapped in
``COALESCE(..., FALSE)`` so a NULL fails the test instead of making the whole
predicate NULL.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from .format.columnmap import quote_ident, quote_string

__all__ = [
    "sql_num",
    "pred_range",
    "pred_in",
    "pred_in_strings",
    "pred_and",
    "pred_or",
    "pred_not",
    "pred_is_null",
    "ppm_window",
    "pred_mz_windows",
]


def sql_num(x: float) -> str:
    """Format a number so it survives the round trip through SQL text.

    ``%.17g`` is the shortest format that reproduces any float64 exactly.
    """
    if isinstance(x, bool):  # bool is an int subclass; never meant here
        raise TypeError("expected a number, got bool")
    if isinstance(x, int):
        return str(x)
    if math.isnan(x):
        return "NULL"
    if math.isinf(x):
        return "'Infinity'::DOUBLE" if x > 0 else "'-Infinity'::DOUBLE"
    return f"{x:.17g}"


def pred_range(
    column: str,
    lower: float | None = None,
    upper: float | None = None,
) -> str | None:
    """``column`` within ``[lower, upper]``, or ``None`` if neither bound
    constrains anything.

    An infinite or missing bound is dropped: it prunes nothing but would still
    be evaluated per row.
    """
    parts = []
    if lower is not None and not (isinstance(lower, float) and math.isinf(lower)):
        parts.append(f"{quote_ident(column)} >= {sql_num(lower)}")
    if upper is not None and not (isinstance(upper, float) and math.isinf(upper)):
        parts.append(f"{quote_ident(column)} <= {sql_num(upper)}")
    if not parts:
        return None
    return "(" + " AND ".join(parts) + ")"


def pred_in(column: str, values: Sequence[float]) -> str | None:
    """``column`` is one of ``values``.

    ``COALESCE(..., FALSE)`` so a NULL fails the test rather than making the
    enclosing expression NULL.
    """
    if not len(values):
        return "FALSE"
    rendered = ", ".join(sql_num(v) for v in values)
    return f"COALESCE({quote_ident(column)} IN ({rendered}), FALSE)"


def pred_in_strings(column: str, values: Sequence[str]) -> str | None:
    """``column`` is one of ``values``, for string columns."""
    if not len(values):
        return "FALSE"
    rendered = ", ".join(quote_string(v) for v in values)
    return f"COALESCE({quote_ident(column)} IN ({rendered}), FALSE)"


def pred_is_null(column: str, negate: bool = False) -> str:
    op = "IS NOT NULL" if negate else "IS NULL"
    return f"({quote_ident(column)} {op})"


def _combine(parts: Iterable[str | None], op: str) -> str | None:
    kept = [p for p in parts if p]
    if not kept:
        return None
    if len(kept) == 1:
        return kept[0]
    return "(" + f" {op} ".join(kept) + ")"


def pred_and(*parts: str | None) -> str | None:
    """Conjunction, ignoring absent parts."""
    return _combine(parts, "AND")


def pred_or(*parts: str | None) -> str | None:
    """Disjunction, ignoring absent parts."""
    return _combine(parts, "OR")


def pred_not(part: str | None) -> str | None:
    if not part:
        return None
    return f"(NOT {part})"


def ppm_window(
    mz: float, tolerance: float = 0.0, ppm: float = 0.0
) -> tuple[float, float]:
    """Absolute bounds around ``mz`` for a tolerance in Da and/or ppm; when
    both are given the wider wins."""
    delta = max(abs(tolerance), abs(mz) * ppm / 1e6)
    return (mz - delta, mz + delta)


def pred_mz_windows(
    column: str,
    values: Sequence[float],
    tolerance: float = 0.0,
    ppm: float = 0.0,
) -> str | None:
    """``column`` falls in any of the windows around ``values``.

    One range per value, OR-ed into a single predicate, so the whole set is
    tested in one scan.
    """
    if not len(values):
        return "FALSE"
    windows = [
        pred_range(column, *ppm_window(v, tolerance=tolerance, ppm=ppm)) for v in values
    ]
    return pred_or(*windows)
