"""Tests for the BLINK matching adapter and the reference library."""

from __future__ import annotations

import numpy as np
import pytest

from mzml_builder import make_spectrum, write_mzml
from mzstack.errors import MzStackError
from mzstack.ingest import mzml_to_mzstack
from mzstack.reference import ReferenceLibrary, msp_to_reference
from mzstack.store import MzStack

MSP = """NAME: Caffeine
PRECURSORMZ: 195.0877
PRECURSORTYPE: [M+H]+
IONMODE: Positive
INCHIKEY: RYYVLZVUVIJVGH-UHFFFAOYSA-N
SMILES: Cn1cnc2n(C)c(=O)n(C)c(=O)c12
FORMULA: C8H10N4O2
Num Peaks: 4
100.0 10.0
138.0660 40.0
195.0877 100.0
42.0 5.0

NAME: Glucose
PRECURSORMZ: 181.0707
IONMODE: Negative
Num Peaks: 3
89.0 20.0
119.0 50.0
181.0707 90.0

"""


@pytest.fixture
def library(tmp_path) -> ReferenceLibrary:
    msp = tmp_path / "lib.msp"
    msp.write_text(MSP)
    return ReferenceLibrary(msp_to_reference(msp, tmp_path / "lib"))


# --- the reference library ---------------------------------------------------


def test_msp_converts_to_a_parquet_library(library):
    assert len(library) == 2
    entries = library.entries()
    assert list(entries["name"]) == ["Caffeine", "Glucose"]
    assert list(entries["n_peaks"]) == [4, 3]


def test_library_captures_the_annotation_fields(library):
    caffeine = library.entries().iloc[0]
    assert caffeine["precursor_mz"] == pytest.approx(195.0877)
    assert caffeine["inchikey"] == "RYYVLZVUVIJVGH-UHFFFAOYSA-N"
    assert caffeine["formula"] == "C8H10N4O2"
    assert caffeine["adduct"] == "[M+H]+"
    assert caffeine["polarity"] == 1


def test_ion_mode_maps_to_the_canonical_polarity(library):
    assert list(library.entries()["polarity"]) == [1, 0]


def test_spectrum_set_arrays_are_sorted_by_mz(library):
    spectra = library.spectrum_set()
    assert len(spectra) == 2

    caffeine = spectra.peaks[0]
    assert caffeine.shape == (2, 4)
    # The MSP listed 42.0 last; peaks come back ascending.
    np.testing.assert_allclose(caffeine[0], [42.0, 100.0, 138.0660, 195.0877])
    np.testing.assert_allclose(caffeine[1], [5.0, 10.0, 40.0, 100.0])
    assert spectra.ids.tolist() == [0, 1]


def test_spectrum_set_can_be_restricted(library):
    spectra = library.spectrum_set(ref_ids=[1])
    assert len(spectra) == 1
    assert spectra.ids.tolist() == [1]
    assert spectra.peaks[0].shape == (2, 3)


def test_a_directory_without_the_two_files_is_rejected(tmp_path):
    with pytest.raises(MzStackError, match="not a reference library"):
        ReferenceLibrary(tmp_path)


# --- matching ----------------------------------------------------------------

blink = pytest.importorskip("blink", reason="needs the blink extra")
pytestmark = pytest.mark.requires_blink


@pytest.fixture
def match_store(tmp_path) -> MzStack:
    """Two near-identical MS2 spectra plus one unrelated to them."""
    peaks = [
        ([100.0, 150.0, 200.0, 250.0, 300.0, 350.0], [10, 20, 30, 40, 50, 60]),
        ([100.0, 150.0, 200.0, 250.0, 300.0, 350.0], [11, 21, 31, 41, 51, 61]),
        ([501.0, 502.0, 503.0, 504.0, 505.0, 506.0], [10, 20, 30, 40, 50, 60]),
    ]
    spectra = [
        make_spectrum(
            i, i + 1, 2, float(i), mz, [float(v) for v in it], precursor_mz=400.0
        )
        for i, (mz, it) in enumerate(peaks)
    ]
    fl = write_mzml(tmp_path / "m.mzML", spectra)
    mzml_to_mzstack(fl, tmp_path / "store")
    return MzStack(tmp_path / "store").filter_ms_level(2)


def test_similar_spectra_score_highly_and_unrelated_ones_do_not(match_store):
    from mzstack.match import blink_match

    hits = blink_match(
        match_store,
        match_store,
        min_score=0.5,
        min_matches=3,
        remove_self_connections=True,
    )
    pairs = set(zip(hits["query_id"], hits["ref_id"], strict=True))
    assert pairs == {(1, 2), (2, 1)}
    assert (hits["score"] > 0.99).all()
    assert (hits["matches"] == 6).all()


def test_self_connections_are_removed_only_when_asked(match_store):
    from mzstack.match import blink_match

    with_self = blink_match(match_store, match_store, min_score=0.5, min_matches=3)
    assert (with_self["query_id"] == with_self["ref_id"]).any()

    without = blink_match(
        match_store,
        match_store,
        min_score=0.5,
        min_matches=3,
        remove_self_connections=True,
    )
    assert not (without["query_id"] == without["ref_id"]).any()


def test_hits_are_ranked_best_first_per_query(match_store):
    from mzstack.match import blink_match

    hits = blink_match(match_store, match_store, min_score=0.5, min_matches=3)
    for _, group in hits.groupby("query_id"):
        assert list(group["rank"]) == list(range(1, len(group) + 1))
        assert list(group["score"]) == sorted(group["score"], reverse=True)


def test_top_n_limits_hits_per_query(match_store):
    from mzstack.match import blink_match

    hits = blink_match(match_store, match_store, min_score=0.5, min_matches=3, top_n=1)
    assert (hits["rank"] == 1).all()
    assert len(hits) == len(set(hits["query_id"]))


def test_ids_are_store_spectrum_ids_so_hits_join_back(match_store):
    from mzstack.match import blink_match

    hits = blink_match(match_store, match_store, min_score=0.5, min_matches=3)
    ids = sorted(set(hits["query_id"]))
    back = match_store.select_ids(ids).spectra_data(columns=["spectrum_id_", "msLevel"])
    assert list(back["spectrum_id_"]) == ids
    assert set(back["msLevel"]) == {2}


def test_min_score_filters_hits(match_store):
    from mzstack.match import blink_match

    strict = blink_match(
        match_store,
        match_store,
        min_score=0.999999,
        min_matches=3,
        remove_self_connections=True,
    )
    assert strict.empty


def test_matching_a_store_against_a_library(match_store, library):
    from mzstack.match import annotate_hits, blink_match

    hits = blink_match(match_store, library, min_score=0.0, min_matches=1)
    assert set(hits.columns) == {"query_id", "ref_id", "score", "matches", "rank"}

    annotated = annotate_hits(hits, library)
    if not annotated.empty:
        assert "name" in annotated.columns
        assert set(annotated["name"]) <= {"Caffeine", "Glucose"}


def test_an_empty_selection_yields_no_hits_but_keeps_the_columns(match_store):
    from mzstack.match import blink_match

    hits = blink_match(match_store.select_ids([]), match_store)
    assert hits.empty
    assert list(hits.columns) == ["query_id", "ref_id", "score", "matches", "rank"]


def test_an_unsupported_source_is_rejected(match_store):
    from mzstack.match import blink_match

    with pytest.raises(TypeError, match="Expected an MzStack"):
        blink_match(match_store, "not a spectrum source")
