"""Tests for filtering and the id-restriction strategies."""

from __future__ import annotations

import numpy as np
import pytest

from mzml_builder import make_spectrum, write_mzml
from mzstack.ingest import mzml_to_mzstack
from mzstack.store import MAX_INLINE_IDS, MzStack


@pytest.fixture
def big_store(tmp_path):
    """Enough spectra that every *other* one still exceeds the inline limit."""
    n = 2 * MAX_INLINE_IDS + 200
    spectra = [
        make_spectrum(
            i,
            i + 1,
            1 if i % 2 == 0 else 2,
            float(i),
            [100.0 + i, 200.0 + i],
            [10.0, 20.0],
            polarity="positive" if i % 4 else "negative",
            precursor_mz=None if i % 2 == 0 else 500.0 + i,
        )
        for i in range(n)
    ]
    fl = write_mzml(tmp_path / "big.mzML", spectra)
    path = tmp_path / "big-store"
    mzml_to_mzstack(fl, path, row_group_size=64)
    return MzStack(path)


# --- basic filters -----------------------------------------------------------


def test_unfiltered_length_comes_from_the_manifest(simple_store):
    """No read needed: the manifest already knows."""
    assert len(simple_store) == 4


def test_filter_ms_level(simple_store):
    ms2 = simple_store.filter_ms_level(2)
    assert len(ms2) == 2
    assert list(ms2.spectra_data(columns=["msLevel"])["msLevel"]) == [2, 2]
    assert list(ms2.spectrum_ids) == [2, 4]


def test_filter_ms_level_accepts_several(simple_store):
    assert len(simple_store.filter_ms_level([1, 2])) == 4
    assert len(simple_store.filter_ms_level([3])) == 0


def test_filter_rt_is_in_seconds(simple_store):
    """The store's own unit, not MassQL's."""
    assert list(simple_store.filter_rt((60.0, 70.0)).spectrum_ids) == [1, 2]
    assert list(simple_store.filter_rt((0.0, 1.5)).spectrum_ids) == []


def test_filter_rt_with_ms_level_keeps_other_levels(simple_store):
    """Narrowing MS1 by time must not discard the MS2 scans alongside."""
    got = simple_store.filter_rt((70.0, 80.0), ms_level=1)
    # MS1 scans outside the window go; both MS2 scans stay.
    assert list(got.spectrum_ids) == [2, 3, 4]


def test_filters_compose(simple_store):
    got = simple_store.filter_ms_level(2).filter_rt((70.0, 80.0))
    assert list(got.spectrum_ids) == [4]


def test_filter_precursor_mz_values(simple_store):
    got = simple_store.filter_precursor_mz_values(195.0877, ppm=5)
    assert list(got.spectrum_ids) == [2, 4]
    assert list(simple_store.filter_precursor_mz_values(100.0).spectrum_ids) == []


def test_filter_precursor_mz_range(simple_store):
    assert list(
        simple_store.filter_precursor_mz_range((195.0, 196.0)).spectrum_ids
    ) == [
        2,
        4,
    ]


def test_filter_data_origin(simple_store, simple_mzml):
    origin = str(simple_mzml.resolve())
    assert len(simple_store.filter_data_origin(origin)) == 4
    assert len(simple_store.filter_data_origin("/nowhere.mzML")) == 0


def test_filter_polarity(simple_store):
    assert len(simple_store.filter_polarity(1)) == 4
    assert len(simple_store.filter_polarity(0)) == 0


def test_reset_drops_every_filter(simple_store):
    assert len(simple_store.filter_ms_level(2).reset()) == 4


def test_filters_do_not_mutate_the_original(simple_store):
    simple_store.filter_ms_level(2)
    assert len(simple_store) == 4


# --- signal-level filtering --------------------------------------------------


def test_filter_contains_mz(simple_store):
    got = simple_store.filter_contains_mz(300.0, tolerance=0.01)
    assert list(got.spectrum_ids) == [1]

    wider = simple_store.filter_contains_mz(300.0, tolerance=0.6)
    assert list(wider.spectrum_ids) == [1, 3]


def test_filter_contains_mz_composes_with_metadata_filters(simple_store):
    got = simple_store.filter_ms_level(1).filter_contains_mz(100.0, tolerance=0.01)
    assert list(got.spectrum_ids) == [1, 3]

    none = simple_store.filter_ms_level(2).filter_contains_mz(100.0, tolerance=0.01)
    assert list(none.spectrum_ids) == []


def test_filter_contains_mz_accepts_several_values(simple_store):
    got = simple_store.filter_contains_mz([50.0, 250.0], tolerance=0.01)
    assert list(got.spectrum_ids) == [2, 3]


def test_a_metadata_filter_after_an_id_set_still_narrows(simple_store):
    got = simple_store.filter_contains_mz(100.0, tolerance=0.01).filter_ms_level(1)
    assert list(got.spectrum_ids) == [1, 3]


# --- id restriction strategies ----------------------------------------------


def test_the_three_id_shapes_agree(big_store):
    """Contiguous, inline and temp-table restrictions must not diverge."""
    all_ids = big_store.spectrum_ids

    contiguous = big_store.select_ids(all_ids[:50].tolist())
    inline = big_store.select_ids(all_ids[:50:2].tolist())
    scattered = big_store.select_ids(all_ids[::2].tolist())

    assert len(scattered.spectrum_ids) > MAX_INLINE_IDS

    for store, want in (
        (contiguous, all_ids[:50]),
        (inline, all_ids[:50:2]),
        (scattered, all_ids[::2]),
    ):
        df = store.spectra_data(columns=["spectrum_id_"])
        np.testing.assert_array_equal(df["spectrum_id_"].to_numpy(), want)


def test_an_explicit_id_order_is_preserved(big_store):
    ids = [7, 3, 900, 1, 250]
    df = big_store.select_ids(ids).spectra_data(columns=["spectrum_id_"])
    assert list(df["spectrum_id_"]) == ids


def test_peaks_align_with_metadata_rows(big_store):
    ids = [10, 4, 77]
    store = big_store.select_ids(ids)
    df = store.spectra_data(columns=["spectrum_id_"])
    peaks = store.peaks_data()

    assert list(df["spectrum_id_"]) == ids
    assert len(peaks) == len(ids)
    for sid, (mz, _) in zip(ids, peaks, strict=True):
        # Spectrum i (0-based) was written with m/z 100+i and 200+i.
        np.testing.assert_allclose(mz, [100.0 + sid - 1, 200.0 + sid - 1])


def test_an_empty_selection_returns_nothing(simple_store):
    empty = simple_store.select_ids([])
    assert len(empty) == 0
    assert empty.spectra_data(columns=["spectrum_id_"]).empty
    assert empty.peaks_data() == []


def test_large_filtered_selection_matches_a_direct_query(big_store):
    got = big_store.filter_ms_level(1)
    ids = got.spectrum_ids
    assert len(ids) > MAX_INLINE_IDS

    df = got.spectra_data(columns=["spectrum_id_", "msLevel"])
    assert set(df["msLevel"]) == {1}
    np.testing.assert_array_equal(df["spectrum_id_"].to_numpy(), ids)


# --- variables and errors ----------------------------------------------------


def test_spectra_variables_are_exposed(simple_store):
    variables = simple_store.spectra_variables()
    for expected in ("spectrum_id_", "msLevel", "rtime", "precursorMz", "dataOrigin"):
        assert expected in variables


def test_peak_columns_are_excluded_unless_asked_for(simple_store):
    df = simple_store.spectra_data()
    assert "mz" not in df.columns
    assert "intensity" not in df.columns
    assert "mz" in simple_store.spectra_data(columns=["mz"]).columns


def test_an_unknown_variable_is_rejected(simple_store):
    with pytest.raises(Exception, match="No such spectra variable"):
        simple_store.spectra_data(columns=["nosuchthing"])


def test_repr_reports_the_selection(simple_store):
    assert "selection=all" in repr(simple_store)
    assert "filtered" in repr(simple_store.filter_ms_level(2))
    assert "ids" in repr(simple_store.select_ids([1]))
