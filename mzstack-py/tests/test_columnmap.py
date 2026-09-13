"""Tests for the mzPeak -> canonical column translation.

The generated SQL is executed rather than matched as a string: what matters is
the values that come out.
"""

from __future__ import annotations

import duckdb
import pyarrow as pa
import pytest

from mzstack.format.columnmap import (
    INDEX_PASSTHROUGH,
    MZPEAK_CONSUMED,
    quote_string,
    spectra_variables,
    view_select_sql,
)

# A full index row, using mzPeak's own column names as they appear on disk.
FULL = {
    "spectrum_id_": [1, 2],
    "run_id": ["QC01", "QC01"],
    "spectrum_index": [0, 1],
    "n_scans": [1, 1],
    "n_selected_ions": [0, 1],
    "n_precursors": [0, 1],
    "data_origin": ["/data/QC01.mzpeak", "/data/QC01.mzpeak"],
    "id": ["scan=1", "scan=2"],
    "ms_level": [1, 2],
    "time": [0.5, 1.5],  # MINUTES
    "scan_polarity": [1, -1],
    "spectrum_representation": ["MS:1000128", "MS:1000127"],
    "spectrum_type": ["MS:1000579", "MS:1000580"],
    "lowest_observed_mz": [100.0, 110.0],
    "highest_observed_mz": [900.0, 800.0],
    "base_peak_mz": [321.0, 222.0],
    "base_peak_intensity": [1e5, 2e4],
    "total_ion_current": [1e6, 3e5],
    "number_of_data_points": [1000, None],
    "number_of_peaks": [None, 250],
    "scan_window_lower_limit": [50.0, 50.0],
    "scan_window_upper_limit": [1000.0, 1000.0],
    "ion_injection_time": [12.5, 13.5],
    "filter_string": ["FTMS + p", "ITMS + c"],
    "instrument_configuration_id": [1, 1],
    "selected_ion_mz": [None, 500.25],
    "charge_state": [None, 2],
    "peak_intensity": [None, 9e4],
    "precursor_index": [None, 0],
    "isolation_window_target": [None, 500.5],
    "isolation_window_lower_offset": [None, 0.5],
    "isolation_window_upper_offset": [None, 1.5],
    "collision_energy": [None, 35.0],
}


def _query(data: dict, storage: str = "/store") -> list[dict]:
    """Run the generated view over ``data`` and return its rows."""
    tbl = pa.table(data)  # noqa: F841 -- referenced by DuckDB replacement scan
    sql = view_select_sql(data.keys(), "tbl", storage)
    con = duckdb.connect()
    try:
        con.register("tbl", tbl)
        result = con.execute(sql + ' ORDER BY "spectrum_id_"').to_arrow_table()
    finally:
        con.close()
    return result.to_pylist()


# --- units and encodings -----------------------------------------------------


def test_time_in_minutes_becomes_rtime_in_seconds():
    rows = _query(FULL)
    assert rows[0]["rtime"] == pytest.approx(30.0)
    assert rows[1]["rtime"] == pytest.approx(90.0)


def test_scan_polarity_is_translated_to_the_canonical_encoding():
    """mzPeak 1/-1 -> canonical 1/0, anything else null."""
    rows = _query(FULL)
    assert rows[0]["polarity"] == 1
    assert rows[1]["polarity"] == 0

    unknown = _query({**FULL, "scan_polarity": [0, 7]})
    assert unknown[0]["polarity"] is None
    assert unknown[1]["polarity"] is None


def test_spectrum_representation_curie_becomes_a_boolean():
    rows = _query(FULL)
    assert rows[0]["centroided"] is False  # MS:1000128 profile
    assert rows[1]["centroided"] is True  # MS:1000127 centroid

    other = _query({**FULL, "spectrum_representation": ["MS:1009999", None]})
    assert other[0]["centroided"] is None
    assert other[1]["centroided"] is None


def test_isolation_window_offsets_become_absolute_bounds():
    rows = _query(FULL)
    assert rows[1]["isolationWindowTargetMz"] == pytest.approx(500.5)
    assert rows[1]["isolationWindowLowerMz"] == pytest.approx(500.0)
    assert rows[1]["isolationWindowUpperMz"] == pytest.approx(502.0)


def test_spectrum_index_drives_both_acquisition_and_precursor_numbering():
    rows = _query(FULL)
    assert rows[0]["acquisitionNum"] == 0
    assert rows[1]["acquisitionNum"] == 1
    assert rows[1]["scanIndex"] == 1
    # The MS2's precursor is the MS1 at index 0, in the same numbering space.
    assert rows[1]["precScanNum"] == 0


# --- candidate resolution ----------------------------------------------------


def test_peaks_count_prefers_peaks_then_data_points():
    rows = _query(FULL)
    assert rows[0]["peaksCount"] == 1000  # only number_of_data_points
    assert rows[1]["peaksCount"] == 250  # number_of_peaks wins


def test_peaks_count_falls_back_when_only_one_source_exists():
    profile_only = {k: v for k, v in FULL.items() if k != "number_of_peaks"}
    rows = _query(profile_only)
    assert rows[0]["peaksCount"] == 1000
    assert rows[1]["peaksCount"] is None

    centroid_only = {k: v for k, v in FULL.items() if k != "number_of_data_points"}
    rows = _query(centroid_only)
    assert rows[1]["peaksCount"] == 250


def test_a_missing_source_yields_a_typed_null_not_a_missing_column():
    """Archives differ in which parameters they promote to columns."""
    minimal = {
        "spectrum_id_": [1],
        "spectrum_index": [0],
        "ms_level": [1],
        "time": [1.0],
    }
    rows = _query(minimal)
    (row,) = rows

    # Present, and null, rather than absent.
    for absent in (
        "precursorMz",
        "collisionEnergy",
        "filterString",
        "centroided",
        "polarity",
        "isolationWindowLowerMz",
    ):
        assert absent in row, f"{absent} disappeared"
        assert row[absent] is None
    assert row["rtime"] == pytest.approx(60.0)


def test_partial_inputs_do_not_produce_a_half_computed_window():
    """Lower offset present, upper absent: only the derivable bound appears."""
    data = {k: v for k, v in FULL.items() if k != "isolation_window_upper_offset"}
    rows = _query(data)
    assert rows[1]["isolationWindowLowerMz"] == pytest.approx(500.0)
    assert rows[1]["isolationWindowUpperMz"] is None


# --- passthrough and provenance ----------------------------------------------


def test_passthrough_columns_appear_only_when_present():
    rows = _query(FULL)
    for c in INDEX_PASSTHROUGH:
        assert c in rows[0]
    assert rows[0]["n_selected_ions"] == 0

    without = {k: v for k, v in FULL.items() if k not in ("n_scans", "run_id")}
    rows = _query(without)
    assert "n_scans" not in rows[0]
    assert "run_id" not in rows[0]
    assert "spectrum_id_" in rows[0]


def test_data_origin_and_data_storage_are_reported():
    rows = _query(FULL, storage="/store")
    assert rows[0]["dataOrigin"] == "/data/QC01.mzpeak"
    assert rows[0]["dataStorage"] == "/store"


def test_data_origin_is_null_when_the_index_has_none():
    data = {k: v for k, v in FULL.items() if k != "data_origin"}
    rows = _query(data)
    assert rows[0]["dataOrigin"] is None


def test_data_storage_literal_is_escaped():
    rows = _query(FULL, storage="/tmp/o'brien store")
    assert rows[0]["dataStorage"] == "/tmp/o'brien store"


def test_quote_string_handles_none_and_quotes():
    assert quote_string(None) == "NULL"
    assert quote_string("a'b") == "'a''b'"


# --- the declared variable set -----------------------------------------------


def test_spectra_variables_matches_what_the_view_returns():
    declared = spectra_variables(FULL.keys())
    (row,) = _query({k: v[:1] for k, v in FULL.items()})
    assert declared == list(row.keys())


def test_variable_set_does_not_shrink_when_optional_columns_are_absent():
    """The variable set must not depend on what a dataset carries."""
    full = set(spectra_variables(FULL.keys()))
    minimal = set(spectra_variables(["spectrum_id_", "ms_level", "time"]))
    # Only passthrough columns may drop out; every mapped variable stays.
    assert full - minimal <= set(INDEX_PASSTHROUGH) | set(FULL)


# --- pass-through -------------------------------------------------------------


def test_unmapped_columns_pass_through_under_their_own_names():
    """Peak columns and user-defined variables survive the view."""
    data = {
        **FULL,
        "mz": [100.0, 200.0],
        "intensity": [1.0, 2.0],
        "flag": ["x", "y"],
    }
    (row,) = _query({k: v[:1] for k, v in data.items()})

    assert row["mz"] == 100.0
    assert row["intensity"] == 1.0
    assert row["flag"] == "x"


def test_a_reserved_column_surfaces_as_its_variable_not_itself():
    """`acquisition_num_` is consumed by `acquisitionNum`, not re-exposed."""
    (row,) = _query({k: v[:1] for k, v in {**FULL, "acquisition_num_": [7, 8]}.items()})
    assert row["acquisitionNum"] == 7
    assert "acquisition_num_" not in row


def test_a_consumed_column_is_not_re_exposed():
    """The view must not return both `time` and `rtime`."""
    (row,) = _query({k: v[:1] for k, v in FULL.items()})
    for consumed in ("time", "ms_level", "scan_polarity", "data_origin"):
        assert consumed in MZPEAK_CONSUMED
        assert consumed not in row


def test_acquisition_number_prefers_the_reserved_column():
    """A native dataset keeps the real scan number; an archive has none."""
    with_reserved = _query({**FULL, "acquisition_num_": [42, 43]})
    assert with_reserved[0]["acquisitionNum"] == 42

    without = _query(FULL)
    assert without[0]["acquisitionNum"] == 0  # falls back to spectrum_index


def test_scan_index_prefers_the_reserved_column():
    """`spectrum_index` is dataset-unique, so it only answers scanIndex for a
    single-file run."""
    with_reserved = _query({**FULL, "scan_index": [5, 6]})
    assert with_reserved[0]["scanIndex"] == 5

    without = _query(FULL)
    assert without[0]["scanIndex"] == 0  # falls back to spectrum_index


def test_precursor_intensity_comes_from_peak_intensity():
    rows = _query(FULL)
    assert rows[1]["precursorIntensity"] == pytest.approx(9e4)
