"""Writing a native run: one row per spectrum, peaks as list columns.

Records arrive with canonical spectra-variable names and are encoded into
mzPeak's on-disk vocabulary here, so every writer gets it.

Buffers ``chunk_size`` spectra at a time, so a multi-gigabyte source file
converts in constant space, and writes each chunk as its own part file.

The manifest is written last, after the spectra are counted: a directory
without one is not yet a dataset, so a converter that fails half-way leaves
nothing that looks readable.
"""

from __future__ import annotations

import random
import string
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.dataset as pads

from ..format import layout
from ..format.columnmap import encode_spectra_record, encode_var_names
from ..format.manifest import write_native_manifest
from ..format.schema import ID_COLUMN, build_schema

__all__ = ["NativeWriter"]


def _token() -> str:
    stamp = time.strftime("%Y%m%d%H%M%S")
    rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"{stamp}-{rand}"


class NativeWriter:
    """Accumulates spectra and writes them as a native run.

    Use as a context manager; the manifest is written on a clean exit only.

    Args:
        path: dataset directory to create.
        partitioning: Hive partition columns, named canonically, e.g.
            ``("dataOrigin",)``; translated to their on-disk names.
        compression: Parquet codec for the spectra files.
        row_group_size: spectra per row group.
        chunk_size: spectra buffered before a part file is written.
    """

    def __init__(
        self,
        path: str | Path,
        partitioning: Sequence[str] = (),
        compression: str = layout.DEFAULT_COMPRESSION,
        row_group_size: int = layout.DEFAULT_ROW_GROUP_SIZE,
        chunk_size: int = 2000,
    ) -> None:
        self.path = Path(path)
        # The files are written with mzPeak column names, so the partitioning
        # keys have to match.
        self.partitioning = tuple(encode_var_names(partitioning))
        self.compression = compression
        self.row_group_size = int(row_group_size)
        self.chunk_size = max(int(chunk_size), self.row_group_size)

        self._rows: list[dict[str, Any]] = []
        self._columns: list[str] = []
        self._next_id = 1
        self._written = 0
        self._closed = False

    # -- context manager ------------------------------------------------------

    def __enter__(self) -> NativeWriter:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.close()

    # -- writing --------------------------------------------------------------

    def add(self, record: dict[str, Any]) -> int:
        """Buffer one spectrum, returning the ``spectrum_id_`` assigned to it.

        ``record`` uses canonical spectra-variable names; it is encoded into
        mzPeak's on-disk vocabulary here, after the id is assigned, since
        ``spectrum_index`` is derived from it.

        Ids are assigned in write order and are contiguous across the dataset,
        so a reader can derive them from the manifest rather than scanning.
        """
        sid = self._next_id
        self._next_id += 1
        record = encode_spectra_record({ID_COLUMN: sid, **record})

        for key in record:
            if key not in self._columns:
                self._columns.append(key)

        self._rows.append(record)
        if len(self._rows) >= self.chunk_size:
            self.flush()
        return sid

    def flush(self) -> None:
        """Write the buffered spectra as one part file."""
        if not self._rows:
            return

        schema = build_schema(self._columns)
        columns = {
            name: pa.array(
                [row.get(name) for row in self._rows],
                type=schema.field(name).type,
            )
            for name in schema.names
        }
        table = pa.table(columns, schema=schema)

        pads.write_dataset(
            table,
            base_dir=layout.spectra_path(self.path),
            format="parquet",
            partitioning=(
                pads.partitioning(
                    pa.schema([table.schema.field(c) for c in self.partitioning]),
                    flavor="hive",
                )
                if self.partitioning
                else None
            ),
            basename_template=f"part-{_token()}-{{i}}.parquet",
            existing_data_behavior="overwrite_or_ignore",
            max_rows_per_group=self.row_group_size,
            min_rows_per_group=self.row_group_size,
            file_options=pads.ParquetFileFormat().make_write_options(
                compression=self.compression
            ),
        )

        self._written += len(self._rows)
        self._rows = []

    def close(self) -> int:
        """Flush, write the manifest, and return the spectrum count."""
        if self._closed:
            return self._written
        self.flush()
        write_native_manifest(
            self.path,
            n_spectra=self._written,
            partitioning=self.partitioning,
        )
        self._closed = True
        return self._written

    @property
    def n_written(self) -> int:
        return self._written + len(self._rows)
