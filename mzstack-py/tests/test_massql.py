"""Tests for the MassQL layer.

The frame contracts are asserted against what massql's evaluator indexes into;
a frame that is merely plausible gives wrong answers rather than an error.
"""

from __future__ import annotations

import numpy as np
import pytest

from mzstack.query.massql import MS1_COLUMNS, MS2_COLUMNS, massql_frames
from mzstack.query.pushdown import extract_hints

massql = pytest.importorskip("massql", reason="needs the massql extra")
pytestmark = pytest.mark.requires_massql


# --- frame contracts ---------------------------------------------------------


def test_frames_have_exactly_the_columns_massql_declares(simple_store):
    ms1, ms2 = massql_frames(simple_store)
    assert list(ms1.columns) == list(MS1_COLUMNS)
    assert list(ms2.columns) == list(MS2_COLUMNS)


def test_frames_are_long_one_row_per_peak(simple_store):
    ms1, ms2 = massql_frames(simple_store)
    assert len(ms1) == 3 + 3  # the two MS1 scans
    assert len(ms2) == 2 + 3  # the two MS2 scans


def test_retention_time_is_converted_to_minutes(simple_store):
    """The store holds seconds; MassQL wants minutes."""
    ms1, ms2 = massql_frames(simple_store)
    assert sorted(ms1["rt"].unique()) == pytest.approx([1.0, 1.2])
    assert sorted(ms2["rt"].unique()) == pytest.approx([1.1, 1.3])


def test_polarity_uses_massql_encoding(simple_store):
    """Positive is 1 in both conventions; negative is 0 in the store, 2 here."""
    ms1, _ = massql_frames(simple_store)
    assert set(ms1["polarity"]) == {1}


def test_negative_polarity_becomes_two(tmp_path):
    from mzml_builder import make_spectrum, write_mzml
    from mzstack.ingest import mzml_to_mzstack
    from mzstack.store import MzStack

    fl = write_mzml(
        tmp_path / "neg.mzML",
        [make_spectrum(0, 1, 1, 60.0, [100.0], [5.0], polarity="negative")],
    )
    mzml_to_mzstack(fl, tmp_path / "store")
    ms1, _ = massql_frames(MzStack(tmp_path / "store"))
    assert set(ms1["polarity"]) == {2}


def test_i_norm_peaks_at_one_per_scan(simple_store):
    ms1, ms2 = massql_frames(simple_store)
    for frame in (ms1, ms2):
        assert frame.groupby("scan")["i_norm"].max().to_numpy() == pytest.approx(1.0)


def test_i_tic_norm_sums_to_one_per_scan(simple_store):
    """Normalised by the array's own sum, not the header TIC."""
    ms1, ms2 = massql_frames(simple_store)
    for frame in (ms1, ms2):
        sums = frame.groupby("scan")["i_tic_norm"].sum().to_numpy()
        assert sums == pytest.approx(np.ones_like(sums))


def test_i_tic_norm_ignores_a_wrong_header_tic(tmp_path):
    """A vendor TIC that disagrees with the array must not be used."""
    from mzml_builder import make_spectrum, write_mzml
    from mzstack.ingest import mzml_to_mzstack
    from mzstack.store import MzStack

    fl = write_mzml(
        tmp_path / "tic.mzML",
        [make_spectrum(0, 1, 1, 60.0, [100.0, 200.0], [30.0, 70.0])],
    )
    mzml_to_mzstack(fl, tmp_path / "store")
    store = MzStack(tmp_path / "store")

    ms1, _ = massql_frames(store)
    assert ms1["i_tic_norm"].to_numpy() == pytest.approx([0.3, 0.7])
    assert ms1["i_tic_norm"].sum() == pytest.approx(1.0)


def test_ms1scan_points_at_the_preceding_ms1(simple_store):
    """Reproduces massql's own running "previous MS1 scan"."""
    _, ms2 = massql_frames(simple_store)
    links = ms2.groupby("scan")["ms1scan"].first().to_dict()
    assert links == {2: 1, 4: 3}


def test_ms1scan_does_not_cross_file_boundaries(tmp_path):
    from mzml_builder import make_spectrum, write_mzml
    from mzstack.ingest import mzml_to_mzstack
    from mzstack.store import MzStack

    a = write_mzml(
        tmp_path / "a.mzML",
        [make_spectrum(0, 1, 1, 60.0, [100.0], [1.0])],
    )
    # A file that opens on an MS2, with no MS1 of its own before it.
    b = write_mzml(
        tmp_path / "b.mzML",
        [make_spectrum(0, 1, 2, 60.0, [50.0], [1.0], precursor_mz=200.0)],
    )
    mzml_to_mzstack([a, b], tmp_path / "store")

    _, ms2 = massql_frames(MzStack(tmp_path / "store"))
    # Spectrum 1 is in another file, so it must not be claimed as the parent.
    assert ms2["ms1scan"].isna().all()


def test_frames_respect_the_stores_own_filters(simple_store):
    ms1, ms2 = massql_frames(simple_store.filter_ms_level(2))
    assert ms1.empty
    assert set(ms2["scan"]) == {2, 4}


def test_empty_frames_still_carry_their_columns(simple_store):
    ms1, ms2 = massql_frames(simple_store.filter_ms_level(3))
    assert list(ms1.columns) == list(MS1_COLUMNS)
    assert list(ms2.columns) == list(MS2_COLUMNS)
    assert ms1.empty and ms2.empty


# --- prefilter hints ---------------------------------------------------------


def test_hints_extract_a_retention_time_window():
    hints = extract_hints("QUERY scaninfo(MS2DATA) WHERE RTMIN=1.0 AND RTMAX=2.0")
    assert hints.rt_range == pytest.approx((1.0, 2.0))


def test_an_open_ended_window_is_left_unbounded():
    hints = extract_hints("QUERY scaninfo(MS2DATA) WHERE RTMIN=1.0")
    lo, hi = hints.rt_range
    assert lo == pytest.approx(1.0)
    assert hi == float("inf")


def test_hints_extract_a_precursor_window_with_ppm():
    hints = extract_hints("QUERY scaninfo(MS2DATA) WHERE MS2PREC=500.0:TOLERANCEPPM=20")
    lo, hi = hints.precmz_range
    assert lo == pytest.approx(500.0 - 0.01)
    assert hi == pytest.approx(500.0 + 0.01)


def test_alternatives_within_one_condition_are_all_covered():
    """`MS2PREC=(a OR b)` parses as one condition with two values, and the
    prefilter must cover both."""
    hints = extract_hints("QUERY scaninfo(MS2DATA) WHERE MS2PREC=(100.0 OR 900.0)")
    lo, hi = hints.precmz_range
    assert lo < 100.0 and hi > 900.0


def test_repeated_precursor_conditions_union_rather_than_intersect():
    hints = extract_hints(
        "QUERY scaninfo(MS2DATA) WHERE MS2PREC=100.0 AND MS2PREC=900.0"
    )
    lo, hi = hints.precmz_range
    assert lo < 100.0 and hi > 900.0


def test_a_precursor_prefilter_never_excludes_a_matching_scan(simple_store):
    """The end-to-end version of the same guarantee."""
    query = "QUERY scannum(MS2DATA) WHERE MS2PREC=(100.0 OR 195.0877)"
    with_pd = sorted(simple_store.massql(query, pushdown=True)["scan"])
    without = sorted(simple_store.massql(query, pushdown=False)["scan"])
    assert with_pd == without == [2, 4]


def test_hints_extract_polarity_in_massql_encoding():
    assert (
        extract_hints("QUERY scaninfo(MS2DATA) WHERE POLARITY=Positive").polarity == 1
    )
    assert (
        extract_hints("QUERY scaninfo(MS2DATA) WHERE POLARITY=Negative").polarity == 2
    )


def test_an_unparseable_query_yields_no_hints():
    assert not extract_hints("this is not MassQL")


def test_a_query_without_bounds_yields_no_hints():
    assert not extract_hints("QUERY scaninfo(MS2DATA)")


# --- end to end --------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("QUERY scannum(MS2DATA) WHERE MS2PROD=110.0", [4]),
        ("QUERY scannum(MS2DATA) WHERE MS2PREC=195.0877", [2, 4]),
        ("QUERY scannum(MS1DATA) WHERE MS1MZ=300.0:TOLERANCEMZ=0.6", [1, 3]),
        ("QUERY scannum(MS2DATA) WHERE MS2PROD=75.0 AND RTMIN=1.0 AND RTMAX=1.2", [2]),
        ("QUERY scannum(MS2DATA) WHERE MS2PROD=999.0", []),
    ],
)
def test_queries_return_the_right_scans(simple_store, query, expected):
    result = simple_store.massql(query)
    got = sorted(result["scan"].tolist()) if len(result) else []
    assert got == expected


def test_scans_are_reported_as_global_spectrum_ids(simple_store):
    """So a result joins straight back to the store."""
    result = simple_store.massql("QUERY scaninfo(MS2DATA) WHERE MS2PREC=195.0877")
    ids = sorted(result["scan"].tolist())
    assert ids == [2, 4]

    back = simple_store.select_ids(ids).spectra_data(
        columns=["spectrum_id_", "msLevel"]
    )
    assert set(back["msLevel"]) == {2}


def test_pushdown_does_not_change_the_answer(simple_store):
    query = "QUERY scannum(MS2DATA) WHERE MS2PREC=195.0877 AND RTMIN=1.0 AND RTMAX=1.2"
    with_pd = simple_store.massql(query, pushdown=True)
    without = simple_store.massql(query, pushdown=False)
    assert sorted(with_pd["scan"]) == sorted(without["scan"]) == [2]


def test_the_sql_engine_falls_back_and_agrees(simple_store):
    """`engine='sql'` declines what it cannot compile rather than guessing."""
    query = "QUERY scannum(MS2DATA) WHERE MS2PROD=110.0"
    assert sorted(simple_store.massql(query, engine="sql")["scan"]) == sorted(
        simple_store.massql(query, engine="engine")["scan"]
    )


def test_an_unknown_engine_is_rejected(simple_store):
    with pytest.raises(ValueError, match="engine must be"):
        simple_store.massql("QUERY scaninfo(MS2DATA)", engine="magic")
