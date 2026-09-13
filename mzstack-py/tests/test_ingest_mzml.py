"""Tests for the mzML reader and native conversion."""

from __future__ import annotations

import numpy as np
import pytest

from mzml_builder import SIMPLE_SPECTRA, make_spectrum, write_mzml
from mzstack.format import layout
from mzstack.format.manifest import Manifest
from mzstack.ingest import mzml_to_mzstack, read_spectra
from mzstack.store import MzStack

# --- the parser --------------------------------------------------------------


def test_reads_every_spectrum_with_arrays_intact(simple_mzml):
    specs = list(read_spectra(simple_mzml))
    assert len(specs) == len(SIMPLE_SPECTRA)

    for spec, (level, rt, mz, it) in zip(specs, SIMPLE_SPECTRA, strict=True):
        assert spec["msLevel"] == level
        assert spec["rtime"] == pytest.approx(rt)
        np.testing.assert_allclose(spec["mz"], mz)
        np.testing.assert_allclose(spec["intensity"], it)


def test_retention_time_unit_is_read_not_guessed(tmp_path):
    """A value of 5.0 is 5 s or 300 s depending only on the unit accession."""
    seconds = write_mzml(
        tmp_path / "s.mzML",
        [make_spectrum(0, 1, 1, 5.0, [100.0], [1.0], rt_unit="second")],
    )
    minutes = write_mzml(
        tmp_path / "m.mzML",
        [make_spectrum(0, 1, 1, 5.0, [100.0], [1.0], rt_unit="minute")],
    )

    assert next(read_spectra(seconds))["rtime"] == pytest.approx(5.0)
    assert next(read_spectra(minutes))["rtime"] == pytest.approx(300.0)


def test_large_retention_time_in_minutes_is_not_rescaled(tmp_path):
    """A magnitude heuristic would read 400 minutes as seconds."""
    fl = write_mzml(
        tmp_path / "long.mzML",
        [make_spectrum(0, 1, 1, 400.0, [100.0], [1.0], rt_unit="minute")],
    )
    assert next(read_spectra(fl))["rtime"] == pytest.approx(24000.0)


def test_zlib_compressed_arrays_decode(tmp_path):
    mz = [100.5, 200.25, 300.125]
    it = [1.0, 2.0, 3.0]
    fl = write_mzml(
        tmp_path / "z.mzML",
        [make_spectrum(0, 1, 1, 1.0, mz, it, compress=True)],
    )
    spec = next(read_spectra(fl))
    np.testing.assert_allclose(spec["mz"], mz)
    np.testing.assert_allclose(spec["intensity"], it)


def test_polarity_and_representation_use_the_canonical_encoding(tmp_path):
    fl = write_mzml(
        tmp_path / "p.mzML",
        [
            make_spectrum(0, 1, 1, 1.0, [1.0], [1.0], polarity="positive"),
            make_spectrum(
                1, 2, 1, 2.0, [1.0], [1.0], polarity="negative", centroided=False
            ),
        ],
    )
    a, b = read_spectra(fl)
    assert (a["polarity"], a["centroided"]) == (1, True)
    assert (b["polarity"], b["centroided"]) == (0, False)


def test_isolation_window_offsets_become_absolute_bounds(simple_mzml):
    ms2 = [s for s in read_spectra(simple_mzml) if s["msLevel"] == 2][0]
    assert ms2["isolationWindowTargetMz"] == pytest.approx(195.0877)
    assert ms2["isolationWindowLowerMz"] == pytest.approx(194.5877)
    assert ms2["isolationWindowUpperMz"] == pytest.approx(196.5877)
    assert ms2["collisionEnergy"] == pytest.approx(35.0)


def test_acquisition_number_comes_from_the_spectrum_id(simple_mzml):
    specs = list(read_spectra(simple_mzml))
    assert [s["acquisitionNum"] for s in specs] == [1, 2, 3, 4]
    assert [s["scanIndex"] for s in specs] == [0, 1, 2, 3]


def test_summaries_are_derived_only_when_the_file_omits_them(simple_mzml):
    first = next(read_spectra(simple_mzml))
    assert first["totIonCurrent"] == pytest.approx(100.0)
    assert first["basePeakMz"] == pytest.approx(300.0)
    assert first["basePeakIntensity"] == pytest.approx(50.0)
    assert first["lowMz"] == pytest.approx(100.0)
    assert first["highMz"] == pytest.approx(300.0)
    assert first["peaksCount"] == 3


def test_an_empty_spectrum_is_kept_with_empty_arrays(tmp_path):
    fl = write_mzml(tmp_path / "e.mzML", [make_spectrum(0, 1, 1, 1.0, [], [])])
    spec = next(read_spectra(fl))
    assert spec["peaksCount"] == 0
    assert spec["mz"].size == 0
    assert spec["totIonCurrent"] == 0.0


# --- conversion --------------------------------------------------------------


def test_conversion_writes_a_readable_dataset(simple_mzml, tmp_path):
    path = tmp_path / "store"
    mzml_to_mzstack(simple_mzml, path)

    assert layout.is_mzstack_dataset(path)
    m = Manifest.read(path)
    assert m.kind == layout.KIND_NATIVE
    assert m.n_spectra == len(SIMPLE_SPECTRA)


def test_manifest_count_matches_the_rows_written(simple_store):
    """Ids are derived from this count, so a mismatch is silent corruption."""
    declared = simple_store.manifest.n_spectra
    actual = len(simple_store.spectra_data(columns=["spectrum_id_"]))
    assert declared == actual == len(SIMPLE_SPECTRA)


def test_spectrum_ids_are_one_based_and_contiguous(simple_store):
    ids = simple_store.spectra_data(columns=["spectrum_id_"])["spectrum_id_"]
    assert list(ids) == [1, 2, 3, 4]


def test_peaks_round_trip_through_parquet(simple_store):
    peaks = simple_store.peaks_data()
    assert len(peaks) == len(SIMPLE_SPECTRA)

    for (mz, it), (_, _, want_mz, want_it) in zip(peaks, SIMPLE_SPECTRA, strict=True):
        np.testing.assert_allclose(mz, want_mz)
        np.testing.assert_allclose(it, want_it)


def test_rtime_is_stored_in_seconds(simple_store):
    rt = simple_store.spectra_data(columns=["rtime"])["rtime"]
    assert list(rt) == pytest.approx([60.0, 66.0, 72.0, 78.0])


def test_precursor_reference_resolves_to_a_spectrum_id(simple_store):
    df = simple_store.spectra_data(columns=["spectrum_id_", "msLevel", "precScanNum"])
    ms2 = df[df["msLevel"] == 2]
    # Each MS2 was written with spectrumRef pointing at the preceding MS1.
    assert list(ms2["precScanNum"]) == [1, 3]


def test_data_origin_and_storage_are_recorded(simple_mzml, tmp_path):
    path = tmp_path / "store"
    mzml_to_mzstack(simple_mzml, path)
    df = MzStack(path).spectra_data(columns=["dataOrigin", "dataStorage"])

    assert set(df["dataOrigin"]) == {str(simple_mzml.resolve())}
    assert set(df["dataStorage"]) == {str(path.resolve())}


def test_multiple_files_share_one_contiguous_id_space(tmp_path):
    a = write_mzml(
        tmp_path / "a.mzML",
        [make_spectrum(i, i + 1, 1, float(i), [100.0], [1.0]) for i in range(3)],
    )
    b = write_mzml(
        tmp_path / "b.mzML",
        [make_spectrum(i, i + 1, 1, float(i), [200.0], [2.0]) for i in range(2)],
    )
    path = tmp_path / "store"
    mzml_to_mzstack([a, b], path)

    store = MzStack(path)
    df = store.spectra_data(columns=["spectrum_id_", "dataOrigin"]).sort_values(
        "spectrum_id_"
    )
    assert list(df["spectrum_id_"]) == [1, 2, 3, 4, 5]
    assert list(df["dataOrigin"]) == [str(a.resolve())] * 3 + [str(b.resolve())] * 2


def test_partitioning_by_data_origin_creates_one_directory_per_file(tmp_path):
    """The key is given canonically and written under its on-disk name."""
    a = write_mzml(tmp_path / "a.mzML", [make_spectrum(0, 1, 1, 1.0, [1.0], [1.0])])
    b = write_mzml(tmp_path / "b.mzML", [make_spectrum(0, 1, 1, 1.0, [2.0], [2.0])])
    path = tmp_path / "store"
    mzml_to_mzstack([a, b], path, partitioning=("dataOrigin",))

    dirs = list(layout.spectra_path(path).iterdir())
    assert len(dirs) == 2
    assert all(d.is_dir() and d.name.startswith("data_origin=") for d in dirs)

    m = Manifest.read(path)
    assert m.runs[0].partitioning == ("data_origin",)
    assert MzStack(path).manifest.n_spectra == 2
    assert len(MzStack(path).filter_data_origin(str(a.resolve()))) == 1


def test_row_group_size_is_honoured(tmp_path):
    import pyarrow.parquet as pq

    spectra = [make_spectrum(i, i + 1, 1, float(i), [100.0], [1.0]) for i in range(10)]
    fl = write_mzml(tmp_path / "many.mzML", spectra)
    path = tmp_path / "store"
    mzml_to_mzstack(fl, path, row_group_size=4)

    files = list(layout.spectra_path(path).rglob("*.parquet"))
    assert files
    groups = [pq.ParquetFile(f).metadata.num_row_groups for f in files]
    assert sum(groups) >= 3  # 10 spectra at 4 per group


def test_no_manifest_is_written_when_conversion_fails(tmp_path):
    """A half-written directory must not look like a readable dataset."""
    path = tmp_path / "store"
    with pytest.raises(FileNotFoundError):
        mzml_to_mzstack(tmp_path / "missing.mzML", path)
    assert not layout.manifest_path(path).exists()


def test_conversion_rejects_an_empty_file_list(tmp_path):
    with pytest.raises(ValueError, match="No input files"):
        mzml_to_mzstack([], tmp_path / "store")
