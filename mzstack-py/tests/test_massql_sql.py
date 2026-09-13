"""The compiled MassQL path, held against the engine.

The compiler answers a query in SQL instead of handing massql frames of every
peak. That is only safe if the two paths are indistinguishable, so almost
every test here is differential: run the same query both ways and demand the
same frame -- same columns in the same order, same dtypes, same values.

A compiler bug does not raise; it returns plausible rows. These tests are the
only thing standing between that and a wrong answer.
"""

from __future__ import annotations

import pytest

from mzstack.errors import NotTranslatable
from mzstack.query.massql_sql import run_compiled

massql = pytest.importorskip("massql", reason="needs the massql extra")
pytestmark = pytest.mark.requires_massql


def assert_same(store, query):
    """The two engines must agree, frame for frame."""
    compiled = store.massql(query, engine="sql")
    engine = store.massql(query, engine="engine")

    assert list(compiled.columns) == list(engine.columns), query
    assert len(compiled) == len(engine), query
    if engine.empty:
        return

    # Sorted before comparing: `scanmz` comes out of a Python set in the
    # engine, so its row order carries no meaning.
    order = list(engine.columns)
    left = compiled.sort_values(order[0]).reset_index(drop=True)
    right = engine.sort_values(order[0]).reset_index(drop=True)
    for column in order:
        assert left[column].dtype == right[column].dtype, f"{query}: {column} dtype"
        assert left[column].equals(right[column]), f"{query}: {column} values"


def compiles(store, query) -> bool:
    """Did the compiler take this query, rather than decline it?"""
    try:
        run_compiled(store, query)
    except NotTranslatable:
        return False
    return True


# --- what the compiler takes -------------------------------------------------

COMPILED = [
    "QUERY scaninfo(MS2DATA)",
    "QUERY scaninfo(MS1DATA)",
    "QUERY scannum(MS2DATA)",
    "QUERY scannum(MS1DATA)",
    "QUERY scanmz(MS2DATA)",
    "QUERY scaninfo(MS2DATA) WHERE RTMIN=1.0",
    "QUERY scaninfo(MS2DATA) WHERE RTMAX=1.2",
    "QUERY scaninfo(MS2DATA) WHERE RTMIN=1.0 AND RTMAX=1.4",
    "QUERY scaninfo(MS1DATA) WHERE RTMIN=1.0 AND RTMAX=1.4",
    "QUERY scaninfo(MS2DATA) WHERE MS2PREC=195.0877",
    "QUERY scaninfo(MS2DATA) WHERE MS2PREC=195.0877:TOLERANCEMZ=0.01",
    "QUERY scaninfo(MS2DATA) WHERE MS2PREC=195.0877:TOLERANCEPPM=20",
    "QUERY scaninfo(MS2DATA) WHERE POLARITY=Positive",
    "QUERY scaninfo(MS2DATA) WHERE SCANMIN=2 AND SCANMAX=4",
    "QUERY scannum(MS2DATA) WHERE MS2PROD=110.0:TOLERANCEMZ=0.01",
    "QUERY scaninfo(MS2DATA) WHERE MS2PROD=110.0:TOLERANCEMZ=0.01",
    "QUERY scaninfo(MS1DATA) WHERE MS1MZ=100.0:TOLERANCEMZ=0.01",
    "QUERY scaninfo(MS2DATA) WHERE MS2PREC=195.0877 AND RTMIN=1.0 AND RTMAX=1.4",
    # Matches nothing: an empty result must also agree.
    "QUERY scaninfo(MS2DATA) WHERE MS2PREC=999.0",
    "QUERY scaninfo(MS2DATA) WHERE RTMIN=99",
    "QUERY scannum(MS2DATA) WHERE MS2PROD=999.0",
]


@pytest.mark.parametrize("query", COMPILED)
def test_compiled_and_engine_agree(simple_store, query):
    assert compiles(simple_store, query), f"expected to compile: {query}"
    assert_same(simple_store, query)


# --- what it declines --------------------------------------------------------

DECLINED = [
    # Needs the summed spectrum, not scan-level facts.
    "QUERY scansum(MS2DATA)",
    # A bare data query returns the peaks themselves.
    "QUERY MS2DATA",
    "QUERY MS1DATA",
    # Qualifiers whose semantics are not reproduced.
    "QUERY scaninfo(MS2DATA) WHERE MS2PROD=110.0:INTENSITYPERCENT=10",
    "QUERY scaninfo(MS1DATA) WHERE MS1MZ=100.0:INTENSITYPERCENT=10",
]


@pytest.mark.parametrize("query", DECLINED)
def test_declined_queries_fall_back_and_still_agree(simple_store, query):
    assert not compiles(simple_store, query), f"expected a decline: {query}"
    # Declining is invisible: `engine="sql"` still answers, via the engine.
    assert_same(simple_store, query)


#: `X` makes massql enumerate candidate masses by re-reading the file itself,
#: which it cannot do for an mzStack dataset -- so neither path answers these.
#: The compiler must still decline rather than invent an answer the engine
#: could not have produced.
VARIABLE_QUERIES = [
    "QUERY scaninfo(MS2DATA) WHERE MS2PROD=X",
    "QUERY scaninfo(MS1DATA) WHERE MS1MZ=X",
]


@pytest.mark.parametrize("query", VARIABLE_QUERIES)
def test_variable_queries_are_declined_not_guessed(simple_store, query):
    assert not compiles(simple_store, query), f"expected a decline: {query}"
    # The engine path raises: massql reaches for the source file and does not
    # recognise it. The compiler declines rather than answering.
    with pytest.raises(Exception, match="File Format Not Supported"):
        simple_store.massql(query, engine="sql")


def test_declining_names_a_reason(simple_store):
    with pytest.raises(NotTranslatable, match="using the engine"):
        run_compiled(simple_store, "QUERY scansum(MS2DATA)")


# --- the traps ---------------------------------------------------------------


def test_ms1scan_survives_the_level_restriction(simple_store):
    """The MS1 link must be computed before MS2 rows are selected.

    Restricting the metadata to MS2 first leaves the running "previous MS1
    scan" window with no MS1 rows to find, and `ms1scan` silently comes back
    NULL for every row -- fast, and wrong.
    """
    result = simple_store.massql("QUERY scaninfo(MS2DATA)", engine="sql")
    assert result["ms1scan"].notna().all()
    assert list(result["ms1scan"]) == [1, 3]


def test_i_norm_ms1_is_null_when_the_linked_ms1_is_excluded(simple_store):
    """An RT window can drop the MS1 scan an MS2 scan points at."""
    # Spectra sit at 60, 66, 72 and 78 seconds: 1.05-1.35 min keeps the MS2 at
    # 66 s but excludes the MS1 at 60 s it is linked to.
    query = "QUERY scaninfo(MS2DATA) WHERE RTMIN=1.05 AND RTMAX=1.35"
    assert_same(simple_store, query)
    result = simple_store.massql(query, engine="sql")
    assert result["i_norm_ms1"].isna().any()


def test_i_is_the_peak_sum_not_the_header_tic(simple_store):
    """`i` must come from the arrays.

    The stored `totIonCurrent` is whatever the file's header claimed, and it
    disagrees with the arrays often enough to matter.
    """
    result = simple_store.massql("QUERY scaninfo(MS2DATA)", engine="sql")
    peaks = simple_store.filter_ms_level(2).peaks_data()
    assert list(result["i"]) == pytest.approx([float(i.sum()) for _, i in peaks])


def test_the_compiler_respects_a_predicate_filter(simple_store):
    assert_same(simple_store.filter_ms_level(2), "QUERY scaninfo(MS2DATA)")
    assert_same(simple_store.filter_rt((60.0, 70.0)), "QUERY scannum(MS2DATA)")


def test_the_compiler_respects_a_materialised_id_selection(simple_store):
    """`select_ids` narrows by ids, not by a predicate.

    A query layer reading only the predicate answers over the whole dataset
    and returns rows the caller excluded.
    """
    one = simple_store.select_ids([2])
    result = one.massql("QUERY scannum(MS2DATA)", engine="sql")
    assert list(result["scan"]) == [2]
    assert_same(one, "QUERY scaninfo(MS2DATA)")


def test_contains_mz_scopes_the_query(simple_store):
    """`filter_contains_mz` materialises ids too."""
    narrowed = simple_store.filter_contains_mz(110.0, tolerance=0.01)
    assert list(narrowed.spectrum_ids) == [4]
    result = narrowed.massql("QUERY scannum(MS2DATA)", engine="sql")
    assert list(result["scan"]) == [4]


def test_an_empty_selection_answers_empty(simple_store):
    empty = simple_store.select_ids([])
    assert empty.massql("QUERY scaninfo(MS2DATA)", engine="sql").empty


# --- MassQL's exact comparison semantics -------------------------------------
#
# These are the details the compiler has to copy rather than assume, and each
# one was wrong in the first draft. They are pinned separately from the
# differential tests so a failure says *which* rule broke.


def test_retention_time_bounds_are_exclusive(simple_store):
    """MassQL's RTMIN/RTMAX are `>` and `<`, not `>=` and `<=`."""
    # The MS2 scans sit at 66 s and 78 s, i.e. 1.1 and 1.3 minutes.
    on_the_boundary = simple_store.massql(
        "QUERY scannum(MS2DATA) WHERE RTMIN=1.1", engine="sql"
    )
    assert list(on_the_boundary["scan"]) == [4]
    assert_same(simple_store, "QUERY scannum(MS2DATA) WHERE RTMIN=1.1")
    assert_same(simple_store, "QUERY scannum(MS2DATA) WHERE RTMAX=1.3")


def test_scan_bounds_are_inclusive(simple_store):
    """...while SCANMIN/SCANMAX are, inconsistently, `>=` and `<=`."""
    result = simple_store.massql(
        "QUERY scannum(MS2DATA) WHERE SCANMIN=2 AND SCANMAX=2", engine="sql"
    )
    assert list(result["scan"]) == [2]
    assert_same(simple_store, "QUERY scannum(MS2DATA) WHERE SCANMIN=2 AND SCANMAX=2")


def test_the_default_tolerance_is_a_tenth(simple_store):
    """MassQL's default window is +-0.1, not +-0.01.

    A narrower default would quietly drop scans the engine matches.
    """
    # 195.0877 is the precursor; 195.15 is inside +-0.1 but outside +-0.01.
    query = "QUERY scannum(MS2DATA) WHERE MS2PREC=195.15"
    assert list(simple_store.massql(query, engine="sql")["scan"]) == [2, 4]
    assert_same(simple_store, query)


def test_ppm_wins_over_da_when_both_are_given(simple_store):
    """`_get_mz_tolerance` returns the ppm window and never looks at the Da one."""
    query = (
        "QUERY scannum(MS2DATA) "
        "WHERE MS2PREC=195.0877:TOLERANCEPPM=1:TOLERANCEMZ=10"
    )
    assert_same(simple_store, query)


def test_an_mz_prefilter_would_rescale_the_normalised_intensities(simple_store):
    """Why `massql_frames(mz_range=...)` stays unused.

    It drops peaks, and `i_norm` is normalised over the peaks that survive --
    so a window silently rescales it. This is the shape of bug a peak-level
    pushdown would introduce, pinned here so nobody wires one up.
    """
    from mzstack.query.massql import massql_frames

    # Scan 4 holds m/z 60, 90 and 110 with the base peak at 110, so a window
    # that stops short of it is where the rescaling shows.
    window = (55.0, 95.0)
    _, whole = massql_frames(simple_store)
    _, windowed = massql_frames(simple_store, mz_range=window)

    scan = 4
    kept = whole[(whole["scan"] == scan) & whole["mz"].between(*window)]
    same = windowed[windowed["scan"] == scan]
    assert list(kept["mz"]) == list(same["mz"])
    # Same peaks, different `i_norm`: the maximum moved.
    assert list(kept["i_norm"]) != list(same["i_norm"])
    assert max(same["i_norm"]) == 1.0


def test_a_precursor_condition_does_not_null_i_norm_ms1(simple_store):
    """MS2PREC narrows the MS1 frame by linkage, not by the precursor test.

    MS1 rows carry no precursor, so applying the condition to them directly
    would empty the frame and turn every `i_norm_ms1` into NaN.
    """
    query = "QUERY scaninfo(MS2DATA) WHERE MS2PREC=195.0877"
    result = simple_store.massql(query, engine="sql")
    assert result["i_norm_ms1"].notna().all()
    assert_same(simple_store, query)
