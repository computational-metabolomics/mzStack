"""Getting data into an mzStack dataset.

:func:`mzml_to_mzstack` converts mzML into a ``native`` run, writing the peaks
into the dataset. :func:`create_mzpeak_dataset` registers external mzPeak
archives as ``mzpeak`` runs, writing only a derived index.
:func:`metabolights_to_mzstack` fetches a public MetaboLights study, or a
subset of one, and converts it.
"""

from __future__ import annotations

from .mzml import MZML_EXTENSIONS, iter_spectra, mzml_to_mzstack, read_spectra
from .native import NativeWriter

__all__ = [
    "MZML_EXTENSIONS",
    "NativeWriter",
    "iter_spectra",
    "mzml_to_mzstack",
    "read_spectra",
]


def __getattr__(name: str):
    # The mzPeak path pulls in archive handling; keep it out of the import
    # graph until it is asked for.
    if name in ("create_mzpeak_dataset", "add_mzpeak_archives"):
        from . import mzpeak

        return getattr(mzpeak, name)
    # Likewise MetaboLights, whose reader is an optional dependency.
    if name in (
        "metabolights_to_mzstack",
        "list_study_files",
        "select_files",
        "StudyFile",
    ):
        from . import metabolights

        return getattr(metabolights, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
