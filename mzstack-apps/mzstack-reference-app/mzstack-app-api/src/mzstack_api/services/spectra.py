"""Browsing spectra: filtering, paging, and one spectrum's peaks.

The filter vocabulary here is exactly `MzStack`'s. Nothing is reinterpreted:
`rt` is in seconds and `polarity` is 1/0, as the mzStack view defines them,
and the UI converts for display rather than the API converting for storage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mzstack.store import MzStack

from ..errors import BadRequest, NotFound
from ..serialization import decimate, records, scalar
from ..store import opened

__all__ = ["DEFAULT_COLUMNS", "SpectraFilter", "columns_for", "one", "page"]

#: Shown by default. The full set runs to 30-odd columns, most of them empty
#: for any given instrument, which makes an unusable first screen.
DEFAULT_COLUMNS = (
    "spectrum_id_",
    "msLevel",
    "rtime",
    "polarity",
    "precursorMz",
    "totIonCurrent",
    "basePeakMz",
    "peaksCount",
    "dataOrigin",
)


@dataclass
class SpectraFilter:
    """A selection, in the terms `MzStack` filters accept."""

    ms_level: list[int] = field(default_factory=list)
    rt_min: float | None = None
    rt_max: float | None = None
    polarity: list[int] = field(default_factory=list)
    data_origin: list[str] = field(default_factory=list)
    precursor_mz: list[float] = field(default_factory=list)
    precursor_mz_min: float | None = None
    precursor_mz_max: float | None = None
    contains_mz: list[float] = field(default_factory=list)
    tolerance: float = 0.0
    ppm: float = 20.0

    def apply(self, ds: MzStack) -> MzStack:
        """`ds` narrowed by this filter. Nothing is read: filters are lazy."""
        if self.ms_level:
            ds = ds.filter_ms_level(self.ms_level)
        if self.rt_min is not None or self.rt_max is not None:
            lo = self.rt_min if self.rt_min is not None else float("-inf")
            hi = self.rt_max if self.rt_max is not None else float("inf")
            if lo > hi:
                raise BadRequest(f"rt_min ({lo}) is above rt_max ({hi})")
            # Restricting to the selected MS levels keeps other levels in the
            # result whatever their retention time -- MzStack's own semantics.
            ds = ds.filter_rt((lo, hi), ms_level=self.ms_level or None)
        if self.polarity:
            ds = ds.filter_polarity(self.polarity)
        if self.data_origin:
            ds = ds.filter_data_origin(self.data_origin)
        if self.precursor_mz_min is not None or self.precursor_mz_max is not None:
            lo = (
                self.precursor_mz_min
                if self.precursor_mz_min is not None
                else float("-inf")
            )
            hi = (
                self.precursor_mz_max
                if self.precursor_mz_max is not None
                else float("inf")
            )
            ds = ds.filter_precursor_mz_range((lo, hi))
        if self.precursor_mz:
            ds = ds.filter_precursor_mz_values(
                self.precursor_mz, tolerance=self.tolerance, ppm=self.ppm
            )
        if self.contains_mz:
            # The only filter that reads the signal, so it goes last: every
            # cheap predicate above has already narrowed what it must scan.
            ds = ds.filter_contains_mz(
                self.contains_mz, tolerance=self.tolerance, ppm=self.ppm
            )
        return ds


def page(
    path: Path,
    filter_: SpectraFilter,
    columns: list[str] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """One page of the spectra table.

    `MzStack` has no LIMIT, so paging goes through ids: unfiltered they are a
    plain range and cost nothing, and filtered they are one id query rather
    than a full table read.
    """
    with opened(path) as ds:
        selected = filter_.apply(ds)
        ids = selected.spectrum_ids
        total = int(len(ids))

        page_ids = ids[offset : offset + limit]
        wanted = columns_for(ds, columns)
        if len(page_ids) == 0:
            return {
                "columns": wanted,
                "rows": [],
                "total": total,
                "limit": limit,
                "offset": offset,
            }

        df = selected.select_ids(page_ids.tolist()).spectra_data(columns=wanted)
        return {
            "columns": wanted,
            "rows": records(df),
            "total": total,
            "limit": limit,
            "offset": offset,
        }


def one(path: Path, spectrum_id: int, max_points: int) -> dict[str, Any]:
    """One spectrum's metadata and peak arrays, ready to plot."""
    with opened(path) as ds:
        if spectrum_id < 1 or spectrum_id > ds.manifest.n_spectra:
            raise NotFound(
                f"No spectrum {spectrum_id} in this dataset "
                f"(ids run from 1 to {ds.manifest.n_spectra})"
            )
        selected = ds.select_ids([spectrum_id])
        peaks = selected.peaks_data()
        if not peaks:
            raise NotFound(f"No spectrum {spectrum_id} in this dataset")

        mz, intensity = peaks[0]
        mz_out, intensity_out, downsampled = decimate(mz, intensity, max_points)

        df = selected.spectra_data()
        meta = records(df)[0] if not df.empty else {}
        return {
            "spectrum_id": spectrum_id,
            "metadata": meta,
            "mz": mz_out,
            "intensity": intensity_out,
            "n_peaks": int(len(mz)),
            "downsampled": downsampled,
            "base_peak": _base_peak(mz, intensity),
        }


def _base_peak(mz, intensity) -> dict[str, Any] | None:
    if len(mz) == 0:
        return None
    i = int(intensity.argmax())
    return {"mz": scalar(mz[i]), "intensity": scalar(intensity[i])}


def columns_for(ds: MzStack, columns: list[str] | None) -> list[str]:
    """The columns to read, defaulted and checked against what `ds` has.

    Public: the MassQL path projects its hits onto this same set, so both
    query modes return one table.
    """
    available = ds.spectra_variables()
    if not columns:
        # Peaks are fetched separately, and the defaults may not all exist
        # (mzPeak runs expose a different subset than native ones).
        return [c for c in DEFAULT_COLUMNS if c in available]
    missing = [c for c in columns if c not in available]
    if missing:
        raise BadRequest(
            "No such spectra variable(s): "
            + ", ".join(missing)
            + ". Available: "
            + ", ".join(available)
        )
    return columns
