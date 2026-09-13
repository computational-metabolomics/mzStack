"""Shared fixtures.

Fixtures build real mzML and real Parquet rather than mocking the readers, so
the encodings the tests exist to pin down -- base64 payloads, unit accessions,
list columns -- are actually exercised.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mzml_builder import SIMPLE_SPECTRA, make_spectrum, write_mzml
from mzstack import duck

__all__ = ["SIMPLE_SPECTRA", "make_spectrum", "write_mzml"]


@pytest.fixture
def simple_mzml(tmp_path: Path) -> Path:
    """Four spectra: MS1, MS2, MS1, MS2, retention times in seconds."""
    spectra = []
    for i, (level, rt, mz, it) in enumerate(SIMPLE_SPECTRA):
        spectra.append(
            make_spectrum(
                index=i,
                scan=i + 1,
                ms_level=level,
                rt=rt,
                mz=mz,
                intensity=it,
                precursor_mz=195.0877 if level == 2 else None,
                precursor_charge=1 if level == 2 else None,
                precursor_ref=(
                    f"controllerType=0 controllerNumber=1 scan={i}"
                    if level == 2
                    else None
                ),
                collision_energy=35.0 if level == 2 else None,
            )
        )
    return write_mzml(tmp_path / "simple.mzML", spectra)


@pytest.fixture
def simple_store(simple_mzml: Path, tmp_path: Path):
    """A native dataset holding `simple_mzml`."""
    from mzstack.ingest import mzml_to_mzstack
    from mzstack.store import MzStack

    path = tmp_path / "store"
    mzml_to_mzstack(simple_mzml, path)
    return MzStack(path)


@pytest.fixture(autouse=True)
def _reset_duckdb():
    """Each test gets its own connection and view registry.

    Views are named per connection and datasets are keyed by path; a tmp_path
    reused across tests would otherwise hit a stale view.
    """
    duck.close()
    yield
    duck.close()
