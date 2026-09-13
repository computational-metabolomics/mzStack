"""Reading HUPO-PSI mzPeak archives.

The point layout needs only pyarrow and DuckDB, so this package has no extra
dependency of its own.
"""

from __future__ import annotations

from .archive import (
    Archive,
    SignalColumns,
    is_archive,
    read_archive,
    signal_columns,
    unpack_archive,
)

__all__ = [
    "Archive",
    "SignalColumns",
    "is_archive",
    "read_archive",
    "signal_columns",
    "unpack_archive",
]
