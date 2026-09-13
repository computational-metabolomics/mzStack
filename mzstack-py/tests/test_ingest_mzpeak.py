"""Tests for indexing mzPeak archives.

That the archives are read and never written is asserted by checksum.
"""

from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest
from mzpeak_builder import default_spectra, make_archive, zip_archive

from mzstack.errors import ArchiveError, FormatError
from mzstack.format import layout
from mzstack.format.manifest import Manifest
from mzstack.ingest.mzpeak import add_mzpeak_archives, create_mzpeak_dataset
from mzstack.mzpeak.archive import (
    member_names,
    read_archive,
    read_index,
    signal_columns,
)
from mzstack.store import MzStack


def checksums(directory) -> dict[str, str]:
    return {
        f.name: hashlib.md5(f.read_bytes()).hexdigest()
        for f in sorted(directory.iterdir())
        if f.is_file()
    }


@pytest.fixture
def archive(tmp_path):
    return make_archive(tmp_path / "QC01.mzpeak", run_id="QC01")


@pytest.fixture
def mzpeak_store(archive, tmp_path) -> MzStack:
    path = tmp_path / "store"
    create_mzpeak_dataset([archive], path, verbose=False)
    return MzStack(path)


# --- archive resolution ------------------------------------------------------


def test_members_are_resolved_by_content_not_file_name(archive):
    index = read_index(archive)
    assert member_names(index, "spectrum", "metadata") == ["spectra_metadata.parquet"]
    assert member_names(index, "spectrum", "peaks") == ["spectra_peaks.parquet"]
    assert member_names(index, "spectrum", "chromatogram") == []


@pytest.mark.parametrize("spelling", ["data_arrays", "data arrays"])
def test_both_spellings_of_a_data_kind_resolve(tmp_path, spelling):
    """The specification writes these inconsistently; both mean the same."""
    directory = make_archive(
        tmp_path / f"a-{spelling.replace(' ', '_')}.mzpeak",
        with_profile=True,
        data_kind_spelling=spelling,
    )
    index = read_index(directory)
    assert member_names(index, "spectrum", "data_arrays") == ["spectra_data.parquet"]
    assert read_archive(directory).profile is not None


def test_signal_columns_resolve_through_the_array_index(archive):
    """By CV term, not by guessing at column names."""
    resolved = read_archive(archive)
    columns = signal_columns(resolved.signal_file())

    # The columns live under a top-level `point` group, so a bare name would
    # not have found them.
    assert "point" in columns.mz
    assert "point" in columns.intensity
    assert "spectrum_index" in columns.index


def test_an_archive_without_an_index_file_is_rejected(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(ArchiveError, match="not an mzPeak archive"):
        read_archive(tmp_path / "empty")


def test_an_index_naming_a_missing_file_is_rejected(archive):
    (archive / "spectra_peaks.parquet").unlink()
    with pytest.raises(ArchiveError, match="does not exist"):
        read_archive(archive)


def test_the_chunked_layout_is_rejected_with_a_clear_message(tmp_path):
    directory = make_archive(tmp_path / "chunked.mzpeak", chunked=True)
    with pytest.raises(ArchiveError, match="chunked layout"):
        read_archive(directory)


def test_an_archive_with_no_signal_at_all_is_rejected(tmp_path):
    directory = make_archive(
        tmp_path / "meta-only.mzpeak", with_centroid=False, with_profile=False
    )
    with pytest.raises(ArchiveError, match="neither spectrum signal data nor peak"):
        read_archive(directory)


# --- ingest ------------------------------------------------------------------


def test_the_archive_is_not_modified(archive, tmp_path):
    """Indexing reads the archive; it never writes to it."""
    before = checksums(archive)
    create_mzpeak_dataset([archive], tmp_path / "store", verbose=False)
    assert checksums(archive) == before


def test_ingest_writes_only_an_index(mzpeak_store):
    assert mzpeak_store.kind == layout.KIND_MZPEAK
    # No `spectra/` directory: a native run's signal store must not appear.
    assert not layout.spectra_path(mzpeak_store.path).exists()
    assert list(layout.index_spectra_path(mzpeak_store.path).glob("run_id=*"))


def test_the_run_id_comes_from_the_archives_own_index(mzpeak_store):
    (run,) = mzpeak_store.manifest.runs
    assert run.run_id == "QC01"
    assert run.kind == layout.KIND_MZPEAK
    assert run.layout == layout.LAYOUT_POINT
    assert run.centroid == "spectra_peaks.parquet"


def test_run_id_is_a_directory_name_not_a_column(mzpeak_store):
    """DuckDB derives it from the path, so a run can be skipped unopened."""
    import pyarrow.parquet as pq

    fl = next(layout.index_spectra_path(mzpeak_store.path).rglob("*.parquet"))
    assert "run_id" not in pq.ParquetFile(fl).schema.names
    assert fl.parent.name == "run_id=QC01"


def test_time_in_minutes_becomes_rtime_in_seconds(mzpeak_store):
    rt = mzpeak_store.spectra_data(columns=["rtime"])["rtime"]
    assert list(rt) == pytest.approx([60.0, 66.0, 72.0, 78.0])


def test_the_curie_becomes_a_centroided_flag(mzpeak_store):
    assert all(mzpeak_store.spectra_data(columns=["centroided"])["centroided"])


def test_side_tables_are_flattened_with_their_counts(mzpeak_store):
    df = mzpeak_store.spectra_data(
        columns=["msLevel", "precursorMz", "collisionEnergy", "n_scans"]
    )
    ms2 = df[df["msLevel"] == 2]
    assert list(ms2["precursorMz"]) == pytest.approx([195.0877, 195.0877])
    # Nested under `activation` in the archive.
    assert list(ms2["collisionEnergy"]) == pytest.approx([35.0, 35.0])
    assert set(df["n_scans"]) == {1}


def test_isolation_offsets_become_absolute_bounds(mzpeak_store):
    df = mzpeak_store.spectra_data(
        columns=["msLevel", "isolationWindowLowerMz", "isolationWindowUpperMz"]
    )
    ms2 = df[df["msLevel"] == 2].iloc[0]
    assert ms2["isolationWindowLowerMz"] == pytest.approx(194.5877)
    assert ms2["isolationWindowUpperMz"] == pytest.approx(196.5877)


def test_peaks_are_read_from_the_archive(mzpeak_store):
    peaks = mzpeak_store.peaks_data()
    for (mz, intensity), spec in zip(peaks, default_spectra(), strict=True):
        np.testing.assert_allclose(mz, spec["mz"])
        np.testing.assert_allclose(intensity, spec["intensity"])


def test_filters_and_signal_search_work(mzpeak_store):
    assert list(mzpeak_store.filter_ms_level(2).spectrum_ids) == [2, 4]
    assert list(mzpeak_store.filter_rt((60.0, 70.0)).spectrum_ids) == [1, 2]
    assert list(
        mzpeak_store.filter_contains_mz(110.0, tolerance=0.01).spectrum_ids
    ) == [4]


def test_massql_runs_over_an_mzpeak_dataset(mzpeak_store):
    pytest.importorskip("massql")
    result = mzpeak_store.massql("QUERY scannum(MS2DATA) WHERE MS2PROD=110.0")
    assert sorted(result["scan"]) == [4]


def test_the_column_map_records_what_the_columns_mean(mzpeak_store):
    mapping = json.loads(layout.column_map_path(mzpeak_store.path).read_text())
    accessions = {row["accession"] for row in mapping}
    assert "MS:1000016" in accessions  # scan start time
    assert all(row["run_id"] == "QC01" for row in mapping)


# --- several archives --------------------------------------------------------


def test_archives_share_one_contiguous_id_space(tmp_path):
    a = make_archive(tmp_path / "A.mzpeak", run_id="A")
    b = make_archive(tmp_path / "B.mzpeak", run_id="B")
    path = tmp_path / "store"
    create_mzpeak_dataset([a, b], path, verbose=False)

    manifest = Manifest.read(path)
    assert [r.run_id for r in manifest.runs] == ["A", "B"]
    assert [r.uid_base for r in manifest.runs] == [1, 5]
    assert manifest.n_spectra == 8
    assert list(MzStack(path).spectrum_ids) == list(range(1, 9))


def test_adding_an_archive_does_not_renumber_the_existing_ones(tmp_path):
    a = make_archive(tmp_path / "A.mzpeak", run_id="A")
    b = make_archive(tmp_path / "B.mzpeak", run_id="B")
    path = tmp_path / "store"
    create_mzpeak_dataset([a], path, verbose=False)

    before = Manifest.read(path).run("A").uid_base
    add_mzpeak_archives(path, [b], verbose=False)
    assert Manifest.read(path).run("A").uid_base == before
    assert Manifest.read(path).run("B").uid_base == 5


def test_archives_with_differing_schemas_combine(tmp_path):
    """Writers promote different parameters; both must still be queryable."""
    a = make_archive(tmp_path / "A.mzpeak", run_id="A")
    b = make_archive(
        tmp_path / "B.mzpeak", run_id="B", omit=("base_peak_mz", "spectrum_type")
    )
    path = tmp_path / "store"
    create_mzpeak_dataset([a, b], path, verbose=False)

    df = MzStack(path).spectra_data(columns=["spectrum_id_", "basePeakMz"])
    assert len(df) == 8
    # Present for A, null for B, rather than failing the scan.
    assert df["basePeakMz"][:4].notna().all()
    assert df["basePeakMz"][4:].isna().all()


def test_peaks_span_archives_in_one_query_per_run(tmp_path):
    a = make_archive(tmp_path / "A.mzpeak", run_id="A")
    b = make_archive(tmp_path / "B.mzpeak", run_id="B")
    path = tmp_path / "store"
    create_mzpeak_dataset([a, b], path, verbose=False)

    peaks = MzStack(path).peaks_data()
    assert len(peaks) == 8
    expected = default_spectra() * 2
    for (mz, _), spec in zip(peaks, expected, strict=True):
        np.testing.assert_allclose(mz, spec["mz"])


# --- packing and kinds -------------------------------------------------------


def test_a_zipped_archive_is_unpacked_into_the_dataset(tmp_path):
    directory = make_archive(tmp_path / "src", run_id="Z")
    packed = zip_archive(directory, tmp_path / "Z.mzpeak")

    path = tmp_path / "store"
    create_mzpeak_dataset([packed], path, verbose=False)

    assert len(MzStack(path)) == 4
    assert layout.run_unpack_path(path, "Z").is_dir()


def test_copy_link_brings_the_archive_into_the_dataset(archive, tmp_path):
    path = tmp_path / "store"
    create_mzpeak_dataset([archive], path, link="copy", verbose=False)

    run = Manifest.read(path).run("QC01")
    assert str(path) in run.path
    assert len(MzStack(path)) == 4


def test_a_dataset_may_not_mix_run_kinds(simple_store, tmp_path):
    archive = make_archive(tmp_path / "QC.mzpeak")
    with pytest.raises(ArchiveError, match="may not mix run kinds"):
        add_mzpeak_archives(simple_store.path, [archive], verbose=False)


def test_creating_over_an_existing_dataset_is_refused(mzpeak_store, tmp_path):
    other = make_archive(tmp_path / "Other.mzpeak", run_id="Other")
    with pytest.raises(ArchiveError, match="already an mzStack dataset"):
        create_mzpeak_dataset([other], mzpeak_store.path, verbose=False)


def test_no_archives_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="No archives"):
        create_mzpeak_dataset([], tmp_path / "store", verbose=False)


def test_a_projection_over_archives_is_invisible(mzpeak_store):
    from mzstack import duck
    from mzstack.projections import build_projection

    before = mzpeak_store.peaks_data()
    build_projection(mzpeak_store.path, layout.PROJECTION_SCANSORTED, verbose=False)
    duck.invalidate(mzpeak_store.path)

    after = MzStack(mzpeak_store.path).peaks_data()
    for (mz_a, i_a), (mz_b, i_b) in zip(before, after, strict=True):
        np.testing.assert_allclose(mz_a, mz_b)
        np.testing.assert_allclose(i_a, i_b)


def test_reading_a_dataset_with_no_manifest_fails(tmp_path):
    with pytest.raises(FormatError, match="not an mzStack dataset"):
        MzStack(tmp_path)
