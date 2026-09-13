"""Paths and constants naming an mzStack dataset's files::

    <dataset>/
      mzStack.json                              the manifest
      index/spectra/run_id=<id>/part-0.parquet  one flat row per spectrum
      index/column_map.json                     CV terms obtained from archives
      index/projections/<type>/run_id=<id>/     rebuildable signal caches
      spectra/                                  signal, for `native` runs
      runs/run_id=<id>/                         only when a ZIP had to be unpacked

Everything under ``index/`` is derived. Source mzPeak archives are referenced
by path and never written to.
"""

from __future__ import annotations

import re
from pathlib import Path

# --- Format identity ---------------------------------------------------------

#: Manifest file name. Its presence is what makes a directory a dataset.
MANIFEST_NAME = "mzStack.json"

#: The format's name, as declared in the manifest's ``format`` field.
FORMAT = "mzStack"

#: The specification version written. Semantic; compatibility is judged on
#: the major component alone, so a later minor version may add fields.
VERSION = "0.1.0"

#: Run kinds.
#:
#: ``mzpeak``  signal lives in an external mzPeak archive, one row per data
#:             point;
#: ``native``  signal was converted from raw MS data files and lives in
#:             ``<dataset>/spectra`` as list columns beside the metadata.
KIND_MZPEAK = "mzpeak"
KIND_NATIVE = "native"
KINDS = (KIND_MZPEAK, KIND_NATIVE)

#: Signal layouts: ``list`` is a native run's list<double> columns, ``point``
#: mzPeak's one row per data point. ``chunk`` is recognised only to be rejected.
LAYOUT_LIST = "list"
LAYOUT_POINT = "point"
LAYOUT_CHUNK = "chunk"

#: The run id a converted dataset registers under: one run covering the whole
#: ``spectra/`` directory, with source files distinguished by ``dataOrigin``.
NATIVE_RUN_ID = "native"

# --- Sub-directories ---------------------------------------------------------

SPECTRA_DIR = "spectra"
INDEX_DIR = "index"
RUNS_DIR = "runs"
PROJECTIONS_DIR = "projections"
COLUMN_MAP_NAME = "column_map.json"

#: Per-sample metadata, one row per ``dataOrigin``. An mzstack extension, not
#: part of mzStack 0.1.0; additive, and ignored by readers that do not know it.
SAMPLE_METADATA_NAME = "sample_metadata.parquet"

# --- Write defaults ----------------------------------------------------------

#: Parquet compression for a native run's spectra. The derived index and
#: projections use zstd.
DEFAULT_COMPRESSION = "snappy"

#: Spectra per row group for a native run. Smaller groups prune range queries
#: more finely and add file metadata; larger ones compress better.
DEFAULT_ROW_GROUP_SIZE = 250

#: Spectra per row group in the derived metadata index.
INDEX_ROW_GROUP_SIZE = 4096

#: Data points per row group in a projection. Rows here are individual points
#: rather than whole spectra, so the group is much larger than the index's.
PROJECTION_ROW_GROUP_SIZE = 100_000

#: Projection types: ``mzsorted`` is ordered by m/z, ``scansorted`` by
#: (spectrum_id_, m/z). ``scansorted`` is an mzstack extension.
PROJECTION_MZSORTED = "mzsorted"
PROJECTION_SCANSORTED = "scansorted"


# --- Path helpers ------------------------------------------------------------


def metabolights_cache_dir() -> Path:
    """Where fetched MetaboLights studies are kept, one directory per study.

    Outside any dataset, so several datasets share one copy of a study and
    ``dataOrigin`` stays stable across runs.
    """
    return Path.home() / ".cache" / "mzstack" / "metabolights"


def manifest_path(path: str | Path) -> Path:
    """Path of the dataset manifest."""
    return Path(path) / MANIFEST_NAME


def spectra_path(path: str | Path) -> Path:
    """Directory holding a native run's spectra."""
    return Path(path) / SPECTRA_DIR


def index_path(path: str | Path) -> Path:
    """Root of the derived index. Everything below it is rebuildable."""
    return Path(path) / INDEX_DIR


def index_spectra_path(path: str | Path) -> Path:
    """Directory holding the derived per-spectrum metadata index."""
    return index_path(path) / SPECTRA_DIR


def column_map_path(path: str | Path) -> Path:
    """Path of the harvested CV column mapping."""
    return index_path(path) / COLUMN_MAP_NAME


def projection_path(path: str | Path, type: str) -> Path:
    """Directory holding every run's projection of ``type``."""
    return index_path(path) / PROJECTIONS_DIR / type


def projection_run_path(path: str | Path, type: str, run_id: str) -> Path:
    """Directory holding one run's projection of ``type``."""
    return projection_path(path, type) / f"run_id={run_id}"


def run_unpack_path(path: str | Path, run_id: str) -> Path:
    """Directory a ZIP archive is unpacked into, when one has to be."""
    return Path(path) / RUNS_DIR / f"run_id={run_id}"


def sample_metadata_path(path: str | Path) -> Path:
    """Path of the per-sample metadata table (an mzstack extension)."""
    return Path(path) / SAMPLE_METADATA_NAME


def is_mzstack_dataset(path: str | Path) -> bool:
    """Does ``path`` hold a manifest?

    What *kind* of signal the dataset holds is a separate question, answered
    from the manifest's contents: both kinds keep their manifest here, so the
    directory layout says nothing about it.
    """
    p = Path(path)
    return p.is_dir() and manifest_path(p).is_file()


_UNSAFE_RUN_ID = re.compile(r"[^A-Za-z0-9._-]")


def sanitise_run_id(name: str) -> str:
    """Replace anything outside ``[A-Za-z0-9._-]``, so ``name`` is usable as a
    ``run_id=<id>`` directory name and as a SQL partition value."""
    return _UNSAFE_RUN_ID.sub("_", name)
