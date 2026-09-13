"""Dataset overview: what the CLI's ``mzstack info`` shows, as JSON."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mzstack import samples
from mzstack.format.manifest import Run
from mzstack.projections import PROJECTION_TYPES

from ..serialization import records, scalar
from ..store import opened

__all__ = ["describe", "projections", "sample_metadata", "summarise"]


def summarise(path: Path) -> dict[str, Any]:
    """The cheap facts about a dataset: manifest only, no Parquet read.

    Used for listings, where opening every dataset's signal would make the
    page load proportional to the size of the workspace.
    """
    with opened(path) as ds:
        manifest = ds.manifest
        return {
            "path": str(ds.path),
            "kind": ds.kind,
            "format": manifest.format,
            "version": manifest.version,
            "n_spectra": manifest.n_spectra,
            "n_runs": len(manifest.runs),
        }


def describe(path: Path) -> dict[str, Any]:
    """Everything the overview page shows about one dataset."""
    with opened(path) as ds:
        manifest = ds.manifest
        summary: dict[str, Any] = {
            "path": str(ds.path),
            "kind": ds.kind,
            "format": manifest.format,
            "version": manifest.version,
            "generation": manifest.generation,
            "n_spectra": manifest.n_spectra,
            "runs": [_run(r) for r in manifest.runs],
            "variables": ds.spectra_variables(),
            "has_sample_metadata": samples.has_sample_metadata(ds.path),
            "projections": _projections(manifest),
        }

        # One pass over a few small columns gives the whole "what is in here"
        # panel: which MS levels, over what retention time, in what polarity,
        # and across how many samples.
        #
        # `n_samples` is not `len(runs)`: converting several mzML files in one
        # go produces a single run holding every sample, so the count is the
        # number of distinct `dataOrigin` values. It decides whether a
        # chromatogram is split per sample.
        columns = ["msLevel", "rtime", "polarity"]
        has_origin = samples.KEY in summary["variables"]
        if has_origin:
            columns.append(samples.KEY)

        df = ds.spectra_data(columns=columns)
        summary["n_samples"] = (
            int(df[samples.KEY].nunique()) if has_origin and not df.empty else 0
        )
        if df.empty:
            summary["ms_levels"] = {}
            summary["rtime_range"] = None
            summary["polarities"] = []
        else:
            counts = df["msLevel"].value_counts().sort_index()
            summary["ms_levels"] = {
                str(scalar(level)): int(n) for level, n in counts.items()
            }
            summary["rtime_range"] = [
                scalar(df["rtime"].min()),
                scalar(df["rtime"].max()),
            ]
            summary["polarities"] = sorted(
                scalar(p) for p in df["polarity"].dropna().unique().tolist()
            )
        return summary


def sample_metadata(path: Path) -> dict[str, Any]:
    """Per-sample annotation, joined to spectra on ``dataOrigin``."""
    with opened(path) as ds:
        if not samples.has_sample_metadata(ds.path):
            return {"columns": [], "rows": [], "key": samples.KEY}
        df = samples.read_sample_metadata(ds.path)
        return {
            "columns": [str(c) for c in df.columns],
            "rows": records(df),
            "key": samples.KEY,
        }


def projections(path: Path) -> dict[str, Any]:
    """Which signal caches exist, and for which runs."""
    with opened(path) as ds:
        return {"types": list(PROJECTION_TYPES), **_projections(ds.manifest)}


def _projections(manifest) -> dict[str, Any]:
    """Per type: which runs have a current projection, and whether all do.

    A projection only speeds things up when *every* run has a current one --
    a partial projection is ignored by the reader -- so `complete` is what the
    UI acts on and `runs` is what it explains with.
    """
    run_ids = [r.run_id for r in manifest.runs]
    current = {}
    for type_ in PROJECTION_TYPES:
        have = list(manifest.has_projection(type_))
        current[type_] = {
            "runs": have,
            "complete": bool(run_ids) and set(run_ids).issubset(have),
        }
    return {"current": current}


def _run(run: Run) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "kind": run.kind,
        "path": run.path,
        "n_spectra": run.n_spectra,
        "uid_base": run.uid_base,
        "partitioning": list(run.partitioning or ()),
        "projections": {
            type_: run.has_projection(type_) for type_ in PROJECTION_TYPES
        },
    }
