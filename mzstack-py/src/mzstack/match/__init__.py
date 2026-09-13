"""Spectral matching, using BLINK as an upstream dependency.

Install it with ``pip install 'mzstack[blink]'``.
"""

from __future__ import annotations

from .blink import annotate_hits, as_spectrum_set, blink_match

__all__ = ["annotate_hits", "as_spectrum_set", "blink_match"]
