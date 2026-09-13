"""Tests for signal projections.

What matters is not that a projection is fast but that it is invisible: results
must be identical with it, without it, and with a stale one.
"""

from __future__ import annotations

import numpy as np
import pytest

from mzstack import duck
from mzstack.errors import MzStackError
from mzstack.format import layout
from mzstack.format.manifest import Manifest
from mzstack.projections import build_projection, drop_projection
from mzstack.signal import point_source
from mzstack.store import MzStack


def _reopen(store: MzStack) -> MzStack:
    """Re-read the dataset, so a manifest change on disk is picked up."""
    duck.invalidate(store.path)
    return MzStack(store.path)


# --- building and dropping ---------------------------------------------------


@pytest.mark.parametrize(
    "type", [layout.PROJECTION_SCANSORTED, layout.PROJECTION_MZSORTED]
)
def test_build_writes_a_projection_and_records_it(simple_store, type):
    build_projection(simple_store.path, type, verbose=False)

    files = list(layout.projection_path(simple_store.path, type).rglob("*.parquet"))
    assert files

    m = Manifest.read(simple_store.path)
    assert m.has_projection(type) == [layout.NATIVE_RUN_ID]


def test_projection_holds_one_row_per_data_point(simple_store):
    build_projection(simple_store.path, verbose=False)
    con = duck.connection()
    src = point_source(_reopen(simple_store))

    n = con.execute(f"SELECT count(*) FROM {src.sql}").fetchone()[0]
    # 3 + 2 + 3 + 3 peaks across the four spectra.
    assert n == 11


def test_scansorted_is_ordered_by_spectrum_then_mz(simple_store):
    build_projection(simple_store.path, layout.PROJECTION_SCANSORTED, verbose=False)
    fl = next(
        layout.projection_path(simple_store.path, layout.PROJECTION_SCANSORTED).rglob(
            "*.parquet"
        )
    )
    con = duck.connection()
    rows = con.execute(f"SELECT spectrum_id_, mz FROM read_parquet('{fl}')").fetchall()
    assert rows == sorted(rows)


def test_mzsorted_is_ordered_by_mz(simple_store):
    build_projection(simple_store.path, layout.PROJECTION_MZSORTED, verbose=False)
    fl = next(
        layout.projection_path(simple_store.path, layout.PROJECTION_MZSORTED).rglob(
            "*.parquet"
        )
    )
    con = duck.connection()
    mz = [r[0] for r in con.execute(f"SELECT mz FROM read_parquet('{fl}')").fetchall()]
    assert mz == sorted(mz)


def test_drop_removes_the_files_and_the_record(simple_store):
    build_projection(simple_store.path, verbose=False)
    drop_projection(simple_store.path, verbose=False)

    assert not layout.projection_path(
        simple_store.path, layout.PROJECTION_SCANSORTED
    ).exists()
    assert (
        Manifest.read(simple_store.path).has_projection(layout.PROJECTION_SCANSORTED)
        == []
    )


def test_the_two_types_are_independent(simple_store):
    build_projection(simple_store.path, layout.PROJECTION_SCANSORTED, verbose=False)
    build_projection(simple_store.path, layout.PROJECTION_MZSORTED, verbose=False)
    drop_projection(simple_store.path, layout.PROJECTION_SCANSORTED, verbose=False)

    m = Manifest.read(simple_store.path)
    assert m.has_projection(layout.PROJECTION_MZSORTED) == [layout.NATIVE_RUN_ID]
    assert m.has_projection(layout.PROJECTION_SCANSORTED) == []


def test_an_unknown_projection_type_is_rejected(simple_store):
    with pytest.raises(MzStackError, match="Unknown projection type"):
        build_projection(simple_store.path, "byintensity", verbose=False)


def test_building_for_an_unknown_run_is_rejected(simple_store):
    with pytest.raises(MzStackError, match="No run 'nope'"):
        build_projection(simple_store.path, runs=["nope"], verbose=False)


# --- a projection is invisible ----------------------------------------------


def test_peaks_are_identical_with_and_without_a_projection(simple_store):
    before = simple_store.peaks_data()
    build_projection(simple_store.path, verbose=False)
    after = _reopen(simple_store).peaks_data()

    assert len(before) == len(after)
    for (mz_a, i_a), (mz_b, i_b) in zip(before, after, strict=True):
        np.testing.assert_allclose(mz_a, mz_b)
        np.testing.assert_allclose(i_a, i_b)


def test_contains_mz_is_identical_with_and_without_a_projection(simple_store):
    before = list(simple_store.filter_contains_mz(300.0, tolerance=0.6).spectrum_ids)
    build_projection(simple_store.path, layout.PROJECTION_MZSORTED, verbose=False)
    after = list(
        _reopen(simple_store).filter_contains_mz(300.0, tolerance=0.6).spectrum_ids
    )
    assert before == after == [1, 3]


def test_massql_is_identical_with_and_without_a_projection(simple_store):
    pytest.importorskip("massql")
    query = "QUERY scannum(MS2DATA) WHERE MS2PROD=110.0"

    before = sorted(simple_store.massql(query)["scan"])
    build_projection(simple_store.path, verbose=False)
    after = sorted(_reopen(simple_store).massql(query)["scan"])
    assert before == after == [4]


def test_a_projection_is_used_once_built(simple_store):
    assert point_source(simple_store).kind == "native"
    build_projection(simple_store.path, verbose=False)
    assert point_source(_reopen(simple_store)).kind == "projection:scansorted"


def test_a_stale_projection_is_ignored(simple_store):
    """Re-ingesting a run invalidates its caches; results must not change."""
    build_projection(simple_store.path, verbose=False)
    store = _reopen(simple_store)
    assert point_source(store).kind == "projection:scansorted"

    # Simulate a re-ingest: the run's generation moves past the projection's.
    m = Manifest.read(simple_store.path)
    m._d["runs"][0]["ingested_at"] = m.generation + 5
    m.write(simple_store.path)

    store = _reopen(simple_store)
    assert point_source(store).kind == "native"
    peaks = store.peaks_data()
    assert len(peaks) == 4
    np.testing.assert_allclose(peaks[0][0], [100.0, 200.0, 300.0])


def test_rebuilding_replaces_rather_than_appends(simple_store):
    build_projection(simple_store.path, verbose=False)
    build_projection(simple_store.path, verbose=False)

    con = duck.connection()
    src = point_source(_reopen(simple_store))
    assert con.execute(f"SELECT count(*) FROM {src.sql}").fetchone()[0] == 11
