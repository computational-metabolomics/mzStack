"""A native dataset stores mzPeak's column vocabulary and value encodings.

These pin the on-disk shape and the round trip back through the view.
"""

from __future__ import annotations

import numpy as np
import pyarrow.dataset as pads
import pyarrow.parquet as pq
import pytest

from mzml_builder import make_spectrum, write_mzml
from mzstack.format import layout
from mzstack.ingest import mzml_to_mzstack
from mzstack.ingest.native import NativeWriter
from mzstack.store import MzStack

#: Every mapped variable, plus a user-defined one.
RICH = [
    {
        "msLevel": 1,
        "rtime": 60.0,  # seconds
        "polarity": 1,  # canonical: 1 positive, 0 negative
        "centroided": False,
        "totIonCurrent": 1000.0,
        "peaksCount": 2,
        "dataOrigin": "a.mzML",
        "dataStorage": "/somewhere/else",
        "flag": "x",
        "mz": np.array([100.0, 110.0]),
        "intensity": np.array([1.0, 2.0]),
    },
    {
        "msLevel": 2,
        "rtime": 120.0,
        "polarity": 1,
        "centroided": True,
        "precursorMz": 500.25,
        "precursorCharge": 2,
        "precursorIntensity": 1e5,
        "collisionEnergy": 27.0,
        "isolationWindowTargetMz": 500.5,
        "isolationWindowLowerMz": 500.0,
        "isolationWindowUpperMz": 501.5,
        "totIonCurrent": 2000.0,
        "peaksCount": 2,
        "dataOrigin": "a.mzML",
        "flag": "y",
        "mz": np.array([200.0, 210.0]),
        "intensity": np.array([3.0, 4.0]),
    },
    {
        "msLevel": 2,
        "rtime": 180.0,
        "polarity": 0,
        "centroided": True,
        "precursorMz": 700.5,
        "precursorCharge": 3,
        "precursorIntensity": 2e5,
        "collisionEnergy": 30.0,
        "isolationWindowTargetMz": 700.5,
        "isolationWindowLowerMz": 700.0,
        "isolationWindowUpperMz": 701.0,
        "totIonCurrent": 3000.0,
        "peaksCount": 2,
        "dataOrigin": "b.mzML",
        "flag": "z",
        "mz": np.array([300.0, 310.0]),
        "intensity": np.array([5.0, 6.0]),
    },
]


@pytest.fixture
def rich_store(tmp_path) -> MzStack:
    path = tmp_path / "rich"
    with NativeWriter(path) as writer:
        for record in RICH:
            writer.add(dict(record))
    return MzStack(path)


def raw_columns(path) -> list[str]:
    return pads.dataset(layout.spectra_path(path), format="parquet").schema.names


def raw_rows(path) -> list[dict]:
    """Scalar columns of the dataset's files, in id order."""
    files = sorted(layout.spectra_path(path).rglob("*.parquet"))
    tables = [pq.read_table(f) for f in files]
    rows: list[dict] = []
    for table in tables:
        rows.extend(table.drop(["mz", "intensity"]).to_pylist())
    return sorted(rows, key=lambda r: r["spectrum_id_"])


# --- on-disk names -----------------------------------------------------------


def test_files_carry_mzpeak_names(rich_store):
    names = set(raw_columns(rich_store.path))

    assert {
        "ms_level",
        "time",
        "scan_polarity",
        "spectrum_representation",
        "selected_ion_mz",
        "charge_state",
        "peak_intensity",
        "collision_energy",
        "isolation_window_target",
        "isolation_window_lower_offset",
        "isolation_window_upper_offset",
        "total_ion_current",
        "data_origin",
        "spectrum_index",
        "number_of_data_points",
        "spectrum_id_",
        "flag",
        "mz",
        "intensity",
    } <= names


def test_files_carry_no_canonical_names(rich_store):
    names = set(raw_columns(rich_store.path))

    assert not names & {
        "msLevel",
        "rtime",
        "polarity",
        "centroided",
        "precursorMz",
        "precursorIntensity",
        "isolationWindowLowerMz",
        "isolationWindowUpperMz",
        "dataOrigin",
        "dataStorage",
    }


# --- on-disk values ----------------------------------------------------------


def test_time_is_stored_in_minutes(rich_store):
    assert [r["time"] for r in raw_rows(rich_store.path)] == pytest.approx(
        [1.0, 2.0, 3.0]
    )


def test_polarity_is_stored_as_plus_or_minus_one(rich_store):
    assert [r["scan_polarity"] for r in raw_rows(rich_store.path)] == [1, 1, -1]


def test_representation_is_stored_as_a_curie(rich_store):
    assert [r["spectrum_representation"] for r in raw_rows(rich_store.path)] == [
        "MS:1000128",  # profile
        "MS:1000127",  # centroid
        "MS:1000127",
    ]


def test_spectrum_index_is_zero_based(rich_store):
    assert [r["spectrum_index"] for r in raw_rows(rich_store.path)] == [0, 1, 2]


def test_peaks_count_is_stored_as_number_of_data_points(rich_store):
    assert [r["number_of_data_points"] for r in raw_rows(rich_store.path)] == [2, 2, 2]


def test_isolation_window_is_stored_as_target_plus_offsets(rich_store):
    rows = raw_rows(rich_store.path)
    assert [r["isolation_window_lower_offset"] for r in rows] == pytest.approx(
        [None, 0.5, 0.5], nan_ok=True
    )
    assert [r["isolation_window_upper_offset"] for r in rows] == pytest.approx(
        [None, 1.0, 0.5], nan_ok=True
    )


# --- round trip through the view ---------------------------------------------


def test_the_view_hands_canonical_names_and_units_back(rich_store):
    got = rich_store.spectra_data(
        columns=[
            "msLevel",
            "rtime",
            "polarity",
            "centroided",
            "precursorMz",
            "precursorCharge",
            "precursorIntensity",
            "collisionEnergy",
            "isolationWindowTargetMz",
            "isolationWindowLowerMz",
            "isolationWindowUpperMz",
            "totIonCurrent",
            "peaksCount",
            "dataOrigin",
            "flag",
        ]
    )

    assert list(got["msLevel"]) == [1, 2, 2]
    assert list(got["rtime"]) == pytest.approx([60.0, 120.0, 180.0])
    assert list(got["polarity"]) == [1, 1, 0]
    assert list(got["centroided"]) == [False, True, True]
    nan = float("nan")
    assert list(got["precursorMz"]) == pytest.approx([nan, 500.25, 700.5], nan_ok=True)
    assert list(got["precursorIntensity"]) == pytest.approx(
        [nan, 1e5, 2e5], nan_ok=True
    )
    assert list(got["isolationWindowLowerMz"]) == pytest.approx(
        [nan, 500.0, 700.0], nan_ok=True
    )
    assert list(got["isolationWindowUpperMz"]) == pytest.approx(
        [nan, 501.5, 701.0], nan_ok=True
    )
    assert list(got["totIonCurrent"]) == pytest.approx([1000.0, 2000.0, 3000.0])
    assert list(got["peaksCount"]) == [2, 2, 2]
    assert list(got["dataOrigin"]) == ["a.mzML", "a.mzML", "b.mzML"]
    assert list(got["flag"]) == ["x", "y", "z"]


def test_data_storage_is_the_dataset_path_not_what_was_written(rich_store):
    """The record supplied a different `dataStorage`; the view overrides it."""
    got = rich_store.spectra_data(columns=["dataStorage"])
    assert set(got["dataStorage"]) == {str(rich_store.path)}


def test_peaks_survive_the_encoding(rich_store):
    peaks = rich_store.peaks_data()
    np.testing.assert_allclose(peaks[0][0], [100.0, 110.0])
    np.testing.assert_allclose(peaks[2][1], [5.0, 6.0])


def test_filters_still_push_down(rich_store):
    assert len(rich_store.filter_ms_level(2)) == 2
    assert len(rich_store.filter_rt((90.0, 200.0))) == 2
    assert len(rich_store.filter_data_origin("b.mzML")) == 1
    assert len(rich_store.filter_precursor_mz_range((500.0, 600.0))) == 1
    assert list(rich_store.filter_contains_mz(310.0, tolerance=0.01).spectrum_ids) == [
        3
    ]


def test_massql_runs_over_the_encoded_dataset(rich_store):
    pytest.importorskip("massql")
    result = rich_store.massql("QUERY scannum(MS2DATA) WHERE MS2PROD=310.0")
    assert sorted(result["scan"]) == [3]


# --- spectrum_index vs scan_index --------------------------------------------


def test_spectrum_index_is_dataset_unique_while_scan_index_is_per_file(tmp_path):
    """mzPeak's key must be the run's own; `scanIndex` restarts per file."""
    files = [
        write_mzml(
            tmp_path / f"{name}.mzML",
            [
                make_spectrum(i, i + 1, 1, float(i), [100.0 + i], [1.0])
                for i in range(3)
            ],
        )
        for name in ("a", "b")
    ]
    path = tmp_path / "store"
    mzml_to_mzstack(files, path)

    rows = raw_rows(path)
    assert [r["spectrum_index"] for r in rows] == [0, 1, 2, 3, 4, 5]
    assert [r["scan_index"] for r in rows] == [0, 1, 2, 0, 1, 2]

    # And the view reports each under its own variable.
    got = MzStack(path).spectra_data(columns=["scanIndex", "acquisitionNum"])
    assert list(got["scanIndex"]) == [0, 1, 2, 0, 1, 2]
    assert list(got["acquisitionNum"]) == [1, 2, 3, 1, 2, 3]


def test_a_record_without_scan_index_still_gets_a_spectrum_index(rich_store):
    """`RICH` supplies no `scanIndex`, so only the derived key is written."""
    assert "scan_index" not in raw_columns(rich_store.path)
    assert [r["spectrum_index"] for r in raw_rows(rich_store.path)] == [0, 1, 2]
    assert list(rich_store.spectra_data(columns=["scanIndex"])["scanIndex"]) == [
        0,
        1,
        2,
    ]
