"""Arrow schema for a native run's ``spectra/`` dataset.

One row per spectrum, in mzPeak's column vocabulary, with peaks in the same
file as the metadata as ``list<double>`` columns. Records are encoded into
this vocabulary by
:func:`mzstack.format.columnmap.encode_spectra_record` before they are
written.

Only the keys are required. A column the source did not supply is simply
absent, and the read view emits it as a typed ``NULL``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import pyarrow as pa

__all__ = [
    "PEAKS_TYPE",
    "ID_COLUMN",
    "PEAK_COLUMNS",
    "REQUIRED_COLUMNS",
    "COLUMN_TYPES",
    "column_type",
    "build_schema",
]

#: Peak arrays. The list's child field must stay named ``item`` -- pyarrow's
#: default -- for other Arrow readers of this format to load the column.
PEAKS_TYPE = pa.list_(pa.float64())

#: Primary key: 1-based, contiguous across the dataset, assigned in write
#: order. int32, which caps a dataset at ~2.1e9 spectra.
ID_COLUMN = "spectrum_id_"

PEAK_COLUMNS = ("mz", "intensity")

#: Columns every native run carries. ``spectrum_index`` is mzPeak's own
#: unique 0-based key, derived from `ID_COLUMN` at encode time.
REQUIRED_COLUMNS = (ID_COLUMN, "spectrum_index", *PEAK_COLUMNS)

#: Types for the on-disk columns this implementation knows about. A column
#: outside this table is still written -- a user-defined spectra variable --
#: but its type is inferred rather than fixed here.
COLUMN_TYPES: Mapping[str, pa.DataType] = {
    ID_COLUMN: pa.int32(),
    # Reserved: mzPeak has no column for either.
    "acquisition_num_": pa.int32(),
    "scan_index": pa.int32(),
    # mzPeak's spectrum metadata vocabulary.
    "spectrum_index": pa.int32(),
    "id": pa.string(),
    "ms_level": pa.int32(),
    "time": pa.float64(),  # MINUTES
    "scan_polarity": pa.int32(),  # 1 positive, -1 negative, null unknown
    "spectrum_representation": pa.string(),  # CURIE
    "spectrum_type": pa.string(),
    "lowest_observed_mz": pa.float64(),
    "highest_observed_mz": pa.float64(),
    "base_peak_mz": pa.float64(),
    "base_peak_intensity": pa.float64(),
    "total_ion_current": pa.float64(),
    "number_of_data_points": pa.int32(),
    "number_of_peaks": pa.int32(),
    "ion_injection_time": pa.float64(),
    "filter_string": pa.string(),
    "instrument_configuration_id": pa.int32(),
    "scan_window_lower_limit": pa.float64(),
    "scan_window_upper_limit": pa.float64(),
    "precursor_index": pa.int32(),
    "selected_ion_mz": pa.float64(),
    "charge_state": pa.int32(),
    "peak_intensity": pa.float64(),
    "collision_energy": pa.float64(),
    "isolation_window_target": pa.float64(),
    "isolation_window_lower_offset": pa.float64(),
    "isolation_window_upper_offset": pa.float64(),
    "data_origin": pa.string(),
    "mz": PEAKS_TYPE,
    "intensity": PEAKS_TYPE,
}


def column_type(name: str) -> pa.DataType | None:
    """Declared type for ``name``, or ``None`` when it is not a known one."""
    return COLUMN_TYPES.get(name)


def build_schema(columns: Iterable[str]) -> pa.Schema:
    """Schema for a native run carrying ``columns``.

    Required columns are added if missing and ordered first, so datasets built
    from different inputs agree on where the key and the peaks are. Columns
    outside `COLUMN_TYPES` are typed as strings.
    """
    wanted: list[str] = list(REQUIRED_COLUMNS)
    for c in columns:
        if c not in wanted:
            wanted.append(c)

    fields = [
        pa.field(name, COLUMN_TYPES.get(name, pa.string()), nullable=name != ID_COLUMN)
        for name in wanted
    ]
    return pa.schema(fields)
