"""Adapter between an mzStack store and BLINK.

Pulls peak arrays from the store in the ``(2, n)`` shape BLINK takes, calls
its scoring functions, and maps the row and column indices in the result back
to ``spectrum_id_`` and ``ref_id``.

Covers the plain sparse-cosine path only; BLINK's network / REM scoring needs
a trained model this adapter does not load.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..errors import MzStackError
from ..reference import ReferenceLibrary, SpectrumSet, store_as_spectrum_set

if TYPE_CHECKING:  # pragma: no cover
    import pandas as pd


__all__ = ["blink_match", "as_spectrum_set"]


def _require_blink():
    try:
        import blink
    except ImportError as e:  # pragma: no cover - exercised by the extra
        raise MzStackError(
            "BLINK spectral matching needs the `blink` package. "
            "Install it with: pip install 'mzstack[blink]'"
        ) from e
    return blink


def as_spectrum_set(source: Any) -> SpectrumSet:
    """Coerce a store, a library or an existing set into a `SpectrumSet`."""
    if isinstance(source, SpectrumSet):
        return source
    if isinstance(source, ReferenceLibrary):
        return source.spectrum_set()
    if hasattr(source, "peaks_data") and hasattr(source, "spectrum_ids"):
        return store_as_spectrum_set(source)
    raise TypeError(
        "Expected an MzStack, a ReferenceLibrary or a SpectrumSet, "
        f"got {type(source).__name__}"
    )


def blink_match(
    query: Any,
    reference: Any,
    tolerance: float = 0.01,
    bin_width: float = 0.001,
    intensity_power: float = 0.5,
    min_score: float = 0.5,
    min_matches: int = 5,
    override_matches: int = 20,
    remove_self_connections: bool = False,
    top_n: int | None = None,
) -> pd.DataFrame:
    """Score ``query`` spectra against ``reference`` spectra with BLINK.

    Args:
        query: an `MzStack` selection, a `ReferenceLibrary`, or a
            `SpectrumSet`.
        reference: the same, matched against.
        tolerance: m/z tolerance in Da for two peaks to count as matching.
        bin_width: m/z binning width; set to the accuracy of the instrument.
        intensity_power: intensities are raised to this power before the
            spectra are unit-normalised. 0.5 is BLINK's default.
        min_score: drop hits scoring below this.
        min_matches: drop hits with fewer matching ions than this.
        override_matches: keep a hit below ``min_score`` anyway when it has
            at least this many matching ions.
        remove_self_connections: zero the diagonal, for when query and
            reference are the same spectra.
        top_n: keep only this many best hits per query spectrum.

    Returns:
        One row per hit: ``query_id`` and ``ref_id`` naming the spectra,
        ``score`` and ``matches`` from BLINK, and ``rank`` within each query
        spectrum. Empty, with those columns, when nothing matches.
    """
    import pandas as pd

    blink = _require_blink()

    q = as_spectrum_set(query)
    r = as_spectrum_set(reference)

    columns = ["query_id", "ref_id", "score", "matches", "rank"]
    if not len(q) or not len(r):
        return pd.DataFrame(columns=columns)

    discretized = blink.discretize_spectra(
        q.peaks,
        r.peaks,
        q.precursor_mz,
        r.precursor_mz,
        tolerance=tolerance,
        bin_width=bin_width,
        intensity_power=intensity_power,
    )
    scores = blink.score_sparse_spectra(discretized)
    scores = blink.filter_hits(
        scores,
        min_score=min_score,
        min_matches=min_matches,
        override_matches=override_matches,
    )

    matrix = blink.reformat_score_matrix(
        scores, remove_self_connections=remove_self_connections
    )
    hits = blink.make_output_df(matrix)
    if hits.empty:
        return pd.DataFrame(columns=columns)

    # BLINK reports positions in the two input lists.
    hits = hits.sparse.to_dense() if hasattr(hits, "sparse") else hits
    query_idx = hits["query"].to_numpy().astype(int)
    ref_idx = hits["ref"].to_numpy().astype(int)

    out = pd.DataFrame(
        {
            "query_id": q.ids[query_idx],
            "ref_id": r.ids[ref_idx],
            "score": hits["score"].to_numpy(dtype=float),
            "matches": hits["matches"].to_numpy(dtype=float).astype(int),
        }
    )

    out = out.sort_values(["query_id", "score"], ascending=[True, False], kind="stable")
    out["rank"] = out.groupby("query_id").cumcount() + 1
    if top_n is not None:
        out = out[out["rank"] <= top_n]
    return out.reset_index(drop=True)[columns]


def annotate_hits(
    hits: pd.DataFrame,
    reference: Any,
    columns: tuple[str, ...] = ("name", "inchikey", "smiles", "adduct", "precursor_mz"),
) -> pd.DataFrame:
    """Join reference metadata onto the hits from `blink_match`."""
    import pandas as pd

    if not isinstance(reference, ReferenceLibrary):
        return hits
    meta = reference.entries(columns=("ref_id", *columns))
    return pd.merge(hits, meta, on="ref_id", how="left")
