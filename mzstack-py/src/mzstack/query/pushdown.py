"""Extracting prefilter bounds from a parsed MassQL query.

Bounds are a superset of what the engine matches; massql re-applies the full
predicate to the result.

The bounds are in MassQL's units -- retention time in minutes, polarity 1
positive / 2 negative -- and are applied to the translated frames.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["PrefilterHints", "extract_hints", "required_levels"]

#: MassQL's default m/z tolerance when a query names none
#: (`msql_engine_filters._get_mz_tolerance`). Must not be narrower than
#: massql's value: the prefilter has to be a superset of what the engine
#: matches.
_DEFAULT_TOLERANCE_DA = 0.1


@dataclass
class PrefilterHints:
    """Bounds that can safely be applied before massql sees the data."""

    #: Retention time in **minutes**.
    rt_range: tuple[float, float] | None = None
    #: Precursor m/z window, the union of every precursor constraint.
    precmz_range: tuple[float, float] | None = None
    #: 1 positive, 2 negative -- MassQL's encoding.
    polarity: int | None = None

    def __bool__(self) -> bool:
        return any(
            v is not None for v in (self.rt_range, self.precmz_range, self.polarity)
        )


def _to_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_polarity(v: Any) -> int | None:
    """MassQL's polarity encoding: 1 positive, 2 negative."""
    s = str(v).lower().strip()
    if s in {"positive", "positivepolarity", "pos", "+", "1"}:
        return 1
    if s in {"negative", "negativepolarity", "neg", "-", "-1", "2"}:
        return 2
    return None


def _extract_tolerance(mz: float, qualifiers: dict) -> float:
    """The widest tolerance across qualifiers, as an absolute m/z delta.

    A condition carrying both a Da and a ppm tolerance takes the wider of the
    two, keeping the window a superset of what the engine accepts.
    """
    widest: float | None = None
    for key, q in qualifiers.items():
        if not isinstance(q, dict):
            continue
        name = str(q.get("name", key)).lower()
        value = _to_float(q.get("value"))
        if value is None:
            continue
        if "ppm" in name:
            tol = abs(mz) * value * 1e-6
        elif "mz" in name:
            tol = value
        else:
            continue
        widest = tol if widest is None else max(widest, tol)
    return widest if widest is not None else _DEFAULT_TOLERANCE_DA


def required_levels(query: str) -> tuple[int, ...] | None:
    """MS levels whose **peaks** ``query`` actually reads.

    The frames are one row per peak, and on a profile dataset the MS1 half is
    routinely 99% of them.

    Errs towards asking for more: an unparseable query, or any condition whose
    value is a variable rather than a number, returns ``None``, meaning build
    every level.
    """
    try:
        from massql import msql_parser
    except ImportError:
        return None

    try:
        parsed = msql_parser.parse_msql(query)
    except Exception:  # noqa: BLE001 -- let the engine report the parse error
        return None

    datatype = str((parsed.get("querytype") or {}).get("datatype", "")).lower()
    needed: set[int] = set()
    if "ms1data" in datatype:
        needed.add(1)
    if "ms2data" in datatype:
        needed.add(2)

    for cond in parsed.get("conditions", []) or []:
        ctype = str(cond.get("type", "")).lower()
        # An `X` variable makes massql scan peak m/z values to enumerate
        # candidates, on either level. A non-numeric value gives nothing to
        # reason about, so every level is asked for.
        for value in cond.get("value", []) or []:
            if _to_float(value) is None:
                return None
        if "ms1" in ctype:
            needed.add(1)
        if "ms2" in ctype:
            needed.add(2)

    return tuple(sorted(needed)) or None


def extract_hints(query: str) -> PrefilterHints:
    """Parse ``query`` and extract prefilter bounds that are safe to apply.

    Empty hints when massql is absent or the query does not parse; the caller
    then builds unfiltered frames and the engine reports the parse error.
    """
    try:
        from massql import msql_parser
    except ImportError:
        return PrefilterHints()

    try:
        parsed = msql_parser.parse_msql(query)
    except Exception:  # noqa: BLE001 -- any parse failure means "no hints"
        return PrefilterHints()

    rt_min: float | None = None
    rt_max: float | None = None
    precmz_lo: float | None = None
    precmz_hi: float | None = None
    polarity: int | None = None

    for cond in parsed.get("conditions", []) or []:
        ctype = str(cond.get("type", "")).lower()
        values = cond.get("value", []) or []
        qualifiers = cond.get("qualifiers", {}) or {}

        if ctype == "rtmincondition" and values:
            rt_min = _to_float(values[0])
        elif ctype == "rtmaxcondition" and values:
            rt_max = _to_float(values[0])
        elif ctype == "ms2precursorcondition" and values:
            # One condition carries every alternative it was written with:
            # `MS2PREC=(100.0 OR 900.0)` parses to a single condition with two
            # values, OR-ed at the row level. The prefilter is their union.
            for value in values:
                mz = _to_float(value)
                if mz is None:
                    continue
                tol = _extract_tolerance(mz, qualifiers)
                lo, hi = mz - tol, mz + tol
                precmz_lo = lo if precmz_lo is None else min(precmz_lo, lo)
                precmz_hi = hi if precmz_hi is None else max(precmz_hi, hi)
        elif ctype == "polaritycondition" and values:
            polarity = _parse_polarity(values[0])

    hints = PrefilterHints(polarity=polarity)
    if rt_min is not None or rt_max is not None:
        hints.rt_range = (
            rt_min if rt_min is not None else float("-inf"),
            rt_max if rt_max is not None else float("inf"),
        )
    if precmz_lo is not None and precmz_hi is not None:
        hints.precmz_range = (precmz_lo, precmz_hi)
    return hints
