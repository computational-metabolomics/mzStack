"""The mzStack format: manifest, on-disk layout, column translation and schema.

Holds no mass-spectrometry or query logic.
"""

from __future__ import annotations

from . import columnmap, layout, schema
from .layout import FORMAT, VERSION, is_mzstack_dataset
from .manifest import (
    IdBlock,
    Manifest,
    Run,
    dataset_kind,
    write_native_manifest,
)

__all__ = [
    "FORMAT",
    "VERSION",
    "IdBlock",
    "Manifest",
    "Run",
    "columnmap",
    "dataset_kind",
    "is_mzstack_dataset",
    "layout",
    "schema",
    "write_native_manifest",
]
