"""Tests for the mzStack manifest."""

from __future__ import annotations

import json

import pytest

from mzstack.errors import FormatError
from mzstack.format import layout
from mzstack.format.manifest import (
    Manifest,
    dataset_kind,
    write_native_manifest,
)


def _mzpeak_manifest() -> Manifest:
    return (
        Manifest.new()
        .add_run(
            run_id="QC01",
            kind=layout.KIND_MZPEAK,
            path="/data/QC01.mzpeak",
            n_spectra=10,
            layout_=layout.LAYOUT_POINT,
            profile="spectra_data.parquet",
            centroid="spectra_peaks.parquet",
        )
        .add_run(
            run_id="QC02",
            kind=layout.KIND_MZPEAK,
            path="/data/QC02.mzpeak",
            n_spectra=5,
            layout_=layout.LAYOUT_POINT,
            profile="spectra_data.parquet",
        )
    )


# --- header and version gate -------------------------------------------------


def test_new_manifest_declares_format_and_version():
    m = Manifest.new()
    assert m.format == layout.FORMAT == "mzStack"
    assert m.version == layout.VERSION
    assert m.generation == 1
    assert m.runs == []
    assert m.n_spectra == 0


def test_read_missing_manifest_is_fatal(tmp_path):
    (tmp_path / "spectra").mkdir()
    with pytest.raises(FormatError, match="not an mzStack dataset"):
        Manifest.read(tmp_path)


def test_read_rejects_another_format(tmp_path):
    (tmp_path / layout.MANIFEST_NAME).write_text(
        json.dumps({"format": "somethingElse", "version": "0.1.0"})
    )
    with pytest.raises(FormatError, match="declares format"):
        Manifest.read(tmp_path)


def test_read_rejects_a_different_major_version(tmp_path):
    Manifest.new().write(tmp_path)
    fl = layout.manifest_path(tmp_path)
    d = json.loads(fl.read_text())
    d["version"] = "1.0.0"
    fl.write_text(json.dumps(d))
    with pytest.raises(FormatError, match="mzStack version 1.0.0"):
        Manifest.read(tmp_path)


def test_read_accepts_a_later_minor_version_with_unknown_fields(tmp_path):
    Manifest.new().write(tmp_path)
    fl = layout.manifest_path(tmp_path)
    d = json.loads(fl.read_text())
    d["version"] = "0.9.3"
    d["someFutureField"] = {"nested": [1, 2, 3]}
    fl.write_text(json.dumps(d))

    m = Manifest.read(tmp_path)
    assert m.version == "0.9.3"


def test_unknown_fields_survive_a_round_trip(tmp_path):
    m = _mzpeak_manifest()
    m.write(tmp_path)

    fl = layout.manifest_path(tmp_path)
    d = json.loads(fl.read_text())
    d["futureTopLevel"] = "keep me"
    d["runs"][0]["futureRunField"] = [1, 2]
    fl.write_text(json.dumps(d))

    # Read, mutate something unrelated, write back.
    m2 = Manifest.read(tmp_path)
    m2.set_projection("QC01", layout.PROJECTION_MZSORTED)
    m2.write(tmp_path)

    d2 = json.loads(fl.read_text())
    assert d2["futureTopLevel"] == "keep me"
    assert d2["runs"][0]["futureRunField"] == [1, 2]
    assert d2["runs"][0]["projections"]["mzsorted"]["generation"] == 1


# --- JSON encoding ------------------------------------------------------------


def test_counts_are_written_as_json_integers(tmp_path):
    """Counts are JSON integers; ids are derived from them."""
    _mzpeak_manifest().write(tmp_path)
    raw = json.loads(layout.manifest_path(tmp_path).read_text())

    assert isinstance(raw["generation"], int)
    for run in raw["runs"]:
        for key in ("n_spectra", "uid_base", "ingested_at"):
            assert isinstance(run[key], int), f"{key} is {type(run[key])}"
            assert not isinstance(run[key], bool)


def test_absent_scalars_are_null_and_empty_projections_are_an_object(tmp_path):
    _mzpeak_manifest().write(tmp_path)
    raw = json.loads(layout.manifest_path(tmp_path).read_text())

    # QC02 was added without a centroid file.
    assert raw["runs"][1]["signal"]["centroid"] is None
    # `{}`, not `[]`: an empty JSON array reads back as a list, not a map.
    assert raw["runs"][0]["projections"] == {}
    assert isinstance(raw["runs"][0]["projections"], dict)


def test_partitioning_is_omitted_when_empty_and_a_list_otherwise(tmp_path):
    m = Manifest.new().add_run(
        run_id="a",
        kind=layout.KIND_NATIVE,
        path="/x/spectra",
        n_spectra=1,
        layout_=layout.LAYOUT_LIST,
    )
    m.add_run(
        run_id="b",
        kind=layout.KIND_NATIVE,
        path="/y/spectra",
        n_spectra=1,
        layout_=layout.LAYOUT_LIST,
        partitioning=["dataOrigin"],
    )
    m.write(tmp_path)
    raw = json.loads(layout.manifest_path(tmp_path).read_text())

    assert "partitioning" not in raw["runs"][0]["signal"]
    assert raw["runs"][1]["signal"]["partitioning"] == ["dataOrigin"]


def test_write_is_atomic_and_leaves_no_temporary_behind(tmp_path):
    Manifest.new().write(tmp_path)
    _mzpeak_manifest().write(tmp_path)

    names = {p.name for p in tmp_path.iterdir()}
    assert names == {layout.MANIFEST_NAME}
    assert Manifest.read(tmp_path).n_spectra == 15


# --- runs and id allocation --------------------------------------------------


def test_uid_base_blocks_are_contiguous():
    m = _mzpeak_manifest()
    runs = m.runs
    assert runs[0].uid_base == 1
    assert runs[0].n_spectra == 10
    assert runs[1].uid_base == 11
    assert m.next_uid_base() == 16
    # The full id vector of an untouched dataset is exactly 1..n.
    assert m.n_spectra == 15


def test_adding_a_run_never_renumbers_an_existing_one():
    m = _mzpeak_manifest()
    before = [(r.run_id, r.uid_base) for r in m.runs]
    m.add_run(
        run_id="QC03",
        kind=layout.KIND_MZPEAK,
        path="/data/QC03.mzpeak",
        n_spectra=7,
        layout_=layout.LAYOUT_POINT,
    )
    after = [(r.run_id, r.uid_base) for r in m.runs]
    assert after[: len(before)] == before
    assert after[-1] == ("QC03", 16)


def test_duplicate_run_id_is_rejected():
    m = _mzpeak_manifest()
    with pytest.raises(FormatError, match="already in this dataset"):
        m.add_run(
            run_id="QC01",
            kind=layout.KIND_MZPEAK,
            path="/data/other.mzpeak",
            n_spectra=1,
            layout_=layout.LAYOUT_POINT,
        )


def test_unknown_run_kind_is_rejected():
    with pytest.raises(FormatError, match="Unknown run kind"):
        Manifest.new().add_run(
            run_id="x",
            kind="hdf5",
            path=None,
            n_spectra=1,
            layout_=layout.LAYOUT_POINT,
        )


def test_ingested_at_records_the_generation_the_run_was_added_at():
    m = Manifest.new()
    m.add_run("a", layout.KIND_MZPEAK, "/a", 2, layout.LAYOUT_POINT)
    m.bump_generation()
    m.add_run("b", layout.KIND_MZPEAK, "/b", 2, layout.LAYOUT_POINT)

    assert m.run("a").ingested_at == 1
    assert m.run("b").ingested_at == 2
    assert m.generation == 2


# --- dataset kind ------------------------------------------------------------


def test_kind_is_read_from_the_runs_not_the_directory(tmp_path):
    _mzpeak_manifest().write(tmp_path)
    # A `spectra/` directory alongside must not change the answer.
    (tmp_path / layout.SPECTRA_DIR).mkdir()
    assert dataset_kind(tmp_path) == layout.KIND_MZPEAK


def test_kind_of_a_manifestless_directory_is_native(tmp_path):
    """The converters write spectra first and the manifest last."""
    assert dataset_kind(tmp_path) == layout.KIND_NATIVE


def test_a_dataset_may_not_mix_run_kinds():
    m = Manifest.new()
    m.add_run("a", layout.KIND_MZPEAK, "/a", 1, layout.LAYOUT_POINT)
    m.add_run("b", layout.KIND_NATIVE, "/b", 1, layout.LAYOUT_LIST)
    with pytest.raises(FormatError, match="mixes run kinds"):
        _ = m.kind


def test_write_native_manifest_registers_one_run(tmp_path):
    write_native_manifest(tmp_path, n_spectra=42, partitioning=["dataOrigin"])

    m = Manifest.read(tmp_path)
    assert m.kind == layout.KIND_NATIVE
    assert m.n_spectra == 42
    (run,) = m.runs
    assert run.run_id == layout.NATIVE_RUN_ID
    assert run.layout == layout.LAYOUT_LIST
    assert run.uid_base == 1
    assert run.partitioning == ("dataOrigin",)
    assert run.path is not None and run.path.endswith(layout.SPECTRA_DIR)


# --- projections -------------------------------------------------------------


def test_projection_is_usable_only_while_its_generation_matches():
    m = _mzpeak_manifest()
    assert m.has_projection(layout.PROJECTION_MZSORTED) == []

    m.set_projection("QC01", layout.PROJECTION_MZSORTED)
    assert m.has_projection(layout.PROJECTION_MZSORTED) == ["QC01"]
    assert m.has_projection(layout.PROJECTION_MZSORTED, ["QC01"]) is True
    assert m.has_projection(layout.PROJECTION_MZSORTED, ["QC01", "QC02"]) is False


def test_reingesting_a_run_invalidates_only_its_own_projections(tmp_path):
    m = _mzpeak_manifest()
    m.set_projection("QC01", layout.PROJECTION_MZSORTED)
    m.set_projection("QC02", layout.PROJECTION_MZSORTED)
    m.write(tmp_path)

    # Re-ingest QC01: bump the generation and rewrite that run's entry.
    raw = json.loads(layout.manifest_path(tmp_path).read_text())
    raw["generation"] = 2
    raw["runs"][0]["ingested_at"] = 2
    layout.manifest_path(tmp_path).write_text(json.dumps(raw))

    m2 = Manifest.read(tmp_path)
    assert m2.has_projection(layout.PROJECTION_MZSORTED) == ["QC02"]


def test_scansorted_and_mzsorted_are_tracked_independently():
    m = _mzpeak_manifest()
    m.set_projection("QC01", layout.PROJECTION_SCANSORTED)

    assert m.has_projection(layout.PROJECTION_SCANSORTED) == ["QC01"]
    assert m.has_projection(layout.PROJECTION_MZSORTED) == []

    m.drop_projection("QC01", layout.PROJECTION_SCANSORTED)
    assert m.has_projection(layout.PROJECTION_SCANSORTED) == []


def test_dropping_an_absent_projection_is_a_no_op():
    m = _mzpeak_manifest()
    m.drop_projection("QC01", layout.PROJECTION_MZSORTED)
    m.drop_projection("nosuchrun", layout.PROJECTION_MZSORTED)


def test_setting_a_projection_on_an_unknown_run_raises():
    with pytest.raises(FormatError, match="No run 'nope'"):
        _mzpeak_manifest().set_projection("nope", layout.PROJECTION_MZSORTED)


# --- split_ids ---------------------------------------------------------------


def test_split_ids_maps_to_runs_and_zero_based_local_indices():
    m = _mzpeak_manifest()  # QC01: 1..10, QC02: 11..15
    blocks = m.split_ids([1, 5, 11, 15])

    assert [b.run.run_id for b in blocks] == ["QC01", "QC02"]
    assert blocks[0].ids == (1, 5)
    assert blocks[0].local == (0, 4)
    assert blocks[1].ids == (11, 15)
    assert blocks[1].local == (0, 4)


def test_split_ids_keeps_the_order_runs_first_appear():
    m = _mzpeak_manifest()
    blocks = m.split_ids([12, 3, 13])

    assert [b.run.run_id for b in blocks] == ["QC02", "QC01"]
    assert blocks[0].ids == (12, 13)
    assert blocks[1].ids == (3,)


def test_split_ids_rejects_ids_outside_the_dataset():
    m = _mzpeak_manifest()
    with pytest.raises(FormatError, match="outside the dataset"):
        m.split_ids([0])
    with pytest.raises(FormatError, match="outside the dataset"):
        m.split_ids([16])


def test_split_ids_rejects_a_gap_between_blocks():
    """A short run must not swallow ids belonging to no run at all."""
    m = Manifest.new()
    m.add_run("a", layout.KIND_MZPEAK, "/a", 3, layout.LAYOUT_POINT)  # 1..3
    # Hand-build a gap: b starts at 10 rather than 4.
    m._d["runs"][-1]["n_spectra"] = 3
    m.add_run("b", layout.KIND_MZPEAK, "/b", 2, layout.LAYOUT_POINT)
    m._d["runs"][-1]["uid_base"] = 10  # b covers 10..11

    assert [b.run.run_id for b in m.split_ids([2, 10])] == ["a", "b"]
    with pytest.raises(FormatError, match="outside the dataset"):
        m.split_ids([5])


def test_split_ids_of_nothing_is_nothing():
    assert _mzpeak_manifest().split_ids([]) == []
    assert Manifest.new().split_ids([1]) == []
