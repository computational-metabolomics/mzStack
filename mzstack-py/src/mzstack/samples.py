"""Per-sample metadata: one Parquet file at the dataset root, one row per
``dataOrigin``.

An mzstack extension, not part of mzStack 0.1.0, which describes acquisitions
but not which sample, class or injection order a run belongs to. Nothing else
depends on the file, and readers that do not know it ignore it.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import pyarrow as pa
import pyarrow.parquet as pq

from .format import layout

if TYPE_CHECKING:  # pragma: no cover
    import pandas as pd

__all__ = ["write_sample_metadata", "read_sample_metadata", "has_sample_metadata"]

#: Column linking a metadata row to the spectra it describes.
KEY = "dataOrigin"


def write_sample_metadata(
    path: str | Path,
    table: pd.DataFrame,
    files: Sequence[str | Path] | None = None,
) -> Path:
    """Write per-sample metadata for the dataset at ``path``.

    Args:
        path: the dataset directory.
        table: one row per sample. A ``dataOrigin`` column links rows to
            spectra; when absent it is derived from ``files``, resolved the
            same way the converter resolves them so the join matches.
        files: source files, in the same row order as ``table``.

    Returns:
        The path written.
    """
    frame = table.copy()
    if KEY not in frame.columns:
        if files is None:
            raise ValueError(
                f"`table` has no '{KEY}' column and no `files` were given to "
                "derive it from."
            )
        if len(files) != len(frame):
            raise ValueError(
                f"{len(files)} file(s) for {len(frame)} metadata row(s); "
                "they must correspond row for row."
            )
        frame[KEY] = [str(Path(f).resolve()) for f in files]

    # `dataOrigin` is the join key; the sheet's own `mzml_path` is dropped.
    frame = frame.drop(columns=["mzml_path"], errors="ignore")

    dest = layout.sample_metadata_path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), dest)
    return dest


def has_sample_metadata(path: str | Path) -> bool:
    return layout.sample_metadata_path(path).is_file()


def read_sample_metadata(path: str | Path) -> pd.DataFrame:
    """Per-sample metadata for the dataset at ``path``.

    Raises:
        FileNotFoundError: if the dataset carries none.
    """
    dest = layout.sample_metadata_path(path)
    if not dest.is_file():
        raise FileNotFoundError(f"'{path}' has no {layout.SAMPLE_METADATA_NAME}.")
    return pq.read_table(dest).to_pandas()
