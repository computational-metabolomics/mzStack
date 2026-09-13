"""The MassQL query layer.

Two execution paths with the same answers: the engine path builds the frames
massql declares and lets massql evaluate them, and the sql path compiles a
query to one DuckDB statement where it can, falling back to the engine where
it cannot.
"""

from __future__ import annotations

from .massql import MS1_COLUMNS, MS2_COLUMNS, massql_frames, run_query
from .pushdown import PrefilterHints, extract_hints

__all__ = [
    "MS1_COLUMNS",
    "MS2_COLUMNS",
    "PrefilterHints",
    "extract_hints",
    "massql_frames",
    "run_query",
]
