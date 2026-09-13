"""Chromatograms.

This is the one piece of domain logic the API adds. `mzstack` has every
ingredient -- per-spectrum summary columns, an m/z containment filter, peak
arrays -- but no function that assembles a trace. The assembly lives here and
uses only the library's public API.

Three traces:

* **TIC**, total ion current per spectrum, straight from the ``totIonCurrent``
  column the file already carries.
* **BPC**, the same for ``basePeakIntensity``.
* **XIC**, summed intensity inside an m/z window per spectrum, which has to be
  computed from the signal.

Any of the three can be split per sample with ``group_by="dataOrigin"``: one
trace per source file, rather than one line summed over every sample.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from mzstack.predicates import ppm_window
from mzstack.store import MzStack

from ..errors import BadRequest
from ..serialization import scalar
from ..store import opened

__all__ = ["GROUP_BY", "TYPES", "extract"]

TIC = "tic"
BPC = "bpc"
XIC = "xic"
TYPES = (TIC, BPC, XIC)

#: The only column a chromatogram can be split by: one sample per source file.
GROUP_BY = "dataOrigin"

_SUMMARY_COLUMN = {TIC: "totIonCurrent", BPC: "basePeakIntensity"}


def extract(
    path: Path,
    type_: str = TIC,
    ms_level: int = 1,
    rt_min: float | None = None,
    rt_max: float | None = None,
    mz: float | None = None,
    tolerance: float = 0.0,
    ppm: float = 20.0,
    group_by: str | None = None,
) -> dict[str, Any]:
    """One chromatogram, as parallel retention time and intensity arrays.

    Retention time is in seconds throughout, matching every other endpoint.

    With `group_by` set to `GROUP_BY`, the arrays move into a ``series`` list
    with one entry per sample instead of sitting at the top level. Everything
    else about the response is the same, so the ungrouped shape is untouched
    for callers that do not ask for the split.
    """
    if type_ not in TYPES:
        raise BadRequest(
            f"Unknown chromatogram type {type_!r}; expected one of "
            + ", ".join(TYPES)
        )
    if type_ == XIC and mz is None:
        raise BadRequest("An extracted ion chromatogram needs an 'mz' value.")
    if group_by is not None and group_by != GROUP_BY:
        raise BadRequest(
            f"Cannot group a chromatogram by {group_by!r}; the only "
            f"supported grouping is {GROUP_BY!r}."
        )

    with opened(path) as ds:
        base = _narrow(ds, ms_level, rt_min, rt_max)
        if group_by is not None and group_by not in base.spectra_variables():
            raise BadRequest(
                f"This dataset has no {group_by!r} column, so its "
                f"chromatogram cannot be split per sample."
            )
        if type_ == XIC:
            frame, extra = _xic(base, mz, tolerance, ppm, group_by)
        else:
            frame, extra = _summary(base, _SUMMARY_COLUMN[type_], group_by)

    result = _shape(frame, group_by)
    result.update(extra)
    result.update(
        {
            "type": type_,
            "ms_level": ms_level,
            "mz": mz,
            "tolerance": tolerance,
            "ppm": ppm,
        }
    )
    return result


def _narrow(
    ds: MzStack, ms_level: int, rt_min: float | None, rt_max: float | None
) -> MzStack:
    selected = ds.filter_ms_level(ms_level)
    if rt_min is not None or rt_max is not None:
        lo = rt_min if rt_min is not None else float("-inf")
        hi = rt_max if rt_max is not None else float("inf")
        if lo > hi:
            raise BadRequest(f"rt_min ({lo}) is above rt_max ({hi})")
        selected = selected.filter_rt((lo, hi), ms_level=ms_level)
    return selected


def _columns(group_by: str | None, *extra: str) -> list[str]:
    """The spectra columns a trace needs, plus the grouping one when asked."""
    return ["spectrum_id_", "rtime", *extra, *([group_by] if group_by else [])]


def _summary(
    selected: MzStack, column: str, group_by: str | None
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """A trace read straight off a per-spectrum column."""
    available = selected.spectra_variables()
    if column not in available:
        raise BadRequest(
            f"This dataset has no {column!r} column, so that chromatogram "
            f"cannot be drawn. Try an extracted ion chromatogram instead."
        )
    df = selected.spectra_data(columns=_columns(group_by, column))
    if df.empty:
        return df, {}
    df = df.sort_values("rtime")
    df["intensity"] = df[column].to_numpy(dtype=float, na_value=0.0)
    return df, {}


def _xic(
    selected: MzStack,
    mz: float,
    tolerance: float,
    ppm: float,
    group_by: str | None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Summed intensity in an m/z window, per spectrum.

    The retention time axis is every spectrum in the selection, not only the
    matching ones: a trace drawn from matches alone would join peak apex to
    peak apex across the gaps instead of returning to baseline. Grouped, that
    holds per series -- each sample keeps its own full axis, so one sample
    missing the compound draws a flat line rather than disappearing.
    """
    axis = selected.spectra_data(columns=_columns(group_by))
    if axis.empty:
        return axis, {}
    axis = axis.sort_values("rtime")

    # Narrow to the spectra that hold a peak in the window before reading any
    # signal; on a large dataset this is the difference between reading a
    # handful of peak lists and reading all of them.
    hits = selected.filter_contains_mz(mz, tolerance=tolerance, ppm=ppm)
    lo, hi = ppm_window(mz, tolerance=tolerance, ppm=ppm)

    summed: dict[int, float] = {}
    hit_ids = hits.spectrum_ids
    if len(hit_ids):
        for spectrum_id, (peak_mz, peak_intensity) in zip(
            hit_ids.tolist(), hits.peaks_data(), strict=True
        ):
            window = (peak_mz >= lo) & (peak_mz <= hi)
            summed[int(spectrum_id)] = float(peak_intensity[window].sum())

    ids = [int(v) for v in axis["spectrum_id_"].tolist()]
    axis["intensity"] = [summed.get(i, 0.0) for i in ids]
    # Carried rather than recomputed, so `n_matching` can be counted per
    # series once the frame is split.
    axis["matched"] = [i in summed for i in ids]
    return axis, {"mz_window": [float(lo), float(hi)]}


def _shape(frame: pd.DataFrame, group_by: str | None) -> dict[str, Any]:
    """The frame as the response body: one trace, or one per group."""
    if group_by is None:
        return _series(frame)
    if frame.empty:
        return {"group_by": group_by, "series": []}
    # Sorted by name so a sample keeps its colour between redraws.
    return {
        "group_by": group_by,
        "series": [
            {"name": str(name), **_series(part)}
            for name, part in sorted(
                frame.groupby(group_by, sort=False, dropna=False),
                key=lambda pair: str(pair[0]),
            )
        ],
    }


def _series(frame: pd.DataFrame) -> dict[str, Any]:
    """One trace's parallel arrays."""
    if frame.empty:
        return _empty()
    series: dict[str, Any] = {
        "rtime": [scalar(v) for v in frame["rtime"].tolist()],
        "intensity": [scalar(v) for v in frame["intensity"].tolist()],
        "spectrum_ids": [int(v) for v in frame["spectrum_id_"].tolist()],
        "n_points": int(len(frame)),
    }
    if "matched" in frame.columns:
        series["n_matching"] = int(frame["matched"].sum())
    return series


def _empty() -> dict[str, Any]:
    return {"rtime": [], "intensity": [], "spectrum_ids": [], "n_points": 0}
