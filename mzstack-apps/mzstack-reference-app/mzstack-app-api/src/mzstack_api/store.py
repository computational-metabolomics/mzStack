"""Opening datasets, safely, from a threaded server.

`mzstack` reads through a single process-wide DuckDB connection
(`mzstack.duck.connection`), and that connection is only locked around setup,
not around queries. Two request threads calling `spectra_data` at the same
time interleave statements on it and get each other's rows back.

Every read therefore goes through `opened`, which holds one process-wide lock
for the whole operation. Filters are lazy -- nothing is read until the
terminal call -- so the lock spans the build-and-fetch, not just the fetch.

Dataset reads are consequently serial. DuckDB still parallelises each
individual query across cores.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from mzstack import duck, open_dataset
from mzstack.store import MzStack

__all__ = ["dataset_lock", "invalidate", "opened"]

#: Guards every use of the shared DuckDB connection. Re-entrant so that a
#: service can call another service without deadlocking.
dataset_lock = threading.RLock()


@contextmanager
def opened(path: str | Path, representation: str = "auto") -> Iterator[MzStack]:
    """A store for `path`, for the duration of the block.

    Opening is cheap -- it reads `mzStack.json` and nothing else -- so there is
    no cache to go stale when a dataset is rebuilt underneath us.
    """
    with dataset_lock:
        yield open_dataset(path, representation)


def invalidate(path: str | Path | None = None) -> None:
    """Drop cached DuckDB views for `path` after it changes on disk.

    Views are cached per path for the life of the process, so a dataset that
    gains runs would otherwise keep serving the old view.
    """
    with dataset_lock:
        duck.invalidate(path)
