"""mzstack -- storage, querying and spectral matching for mass spectrometry data.

An mzStack dataset is a directory identified by an ``mzStack.json`` manifest
holding one or more runs, each either ``native`` (converted from raw MS data
files, peaks stored alongside the metadata) or ``mzpeak`` (an index over
external HUPO-PSI mzPeak archives, which are read in place and never
modified).

    from mzstack import MzStack
    from mzstack.ingest import mzml_to_mzstack

    mzml_to_mzstack(["QC01.mzML"], "store/")

    ds = MzStack("store/")
    ds.filter_ms_level(2).filter_rt((60, 300))        # seconds
    ds.massql("QUERY scaninfo(MS2DATA) WHERE MS2PROD=226.18")
"""

from __future__ import annotations

from .errors import ArchiveError, FormatError, MzStackError, NotTranslatable
from .format import FORMAT, VERSION, Manifest, dataset_kind, is_mzstack_dataset
from .projections import build_projection, drop_projection
from .store import MzStack, open_dataset

__version__ = "0.1.0"

#: The version of the mzStack specification this release reads and writes.
FORMAT_VERSION = VERSION

__all__ = [
    "ArchiveError",
    "FORMAT",
    "FORMAT_VERSION",
    "FormatError",
    "Manifest",
    "MzStack",
    "MzStackError",
    "NotTranslatable",
    "__version__",
    "build_projection",
    "dataset_kind",
    "drop_projection",
    "is_mzstack_dataset",
    "open_dataset",
]


def __getattr__(name: str):
    # Optional layers, kept out of the import graph until asked for: the
    # query layer needs massql, matching needs blink.
    if name == "mzml_to_mzstack":
        from .ingest import mzml_to_mzstack

        return mzml_to_mzstack
    if name == "blink_match":
        from .match import blink_match

        return blink_match
    if name == "ReferenceLibrary":
        from .reference import ReferenceLibrary

        return ReferenceLibrary
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
