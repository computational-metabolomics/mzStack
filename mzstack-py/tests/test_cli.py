"""Tests for the command-line interface and per-sample metadata."""

from __future__ import annotations

import csv

import pytest

from mzml_builder import make_spectrum, write_mzml
from mzstack.cli import main
from mzstack.format import layout
from mzstack.samples import (
    has_sample_metadata,
    read_sample_metadata,
    write_sample_metadata,
)


@pytest.fixture
def two_files(tmp_path):
    paths = []
    for name in ("A", "B"):
        spectra = [
            make_spectrum(
                i,
                i + 1,
                1 if i % 2 == 0 else 2,
                float(i) * 10,
                [100.0, 226.18],
                [10.0, 60.0],
                precursor_mz=400.0 if i % 2 else None,
            )
            for i in range(4)
        ]
        paths.append(write_mzml(tmp_path / f"{name}.mzML", spectra))
    return paths


@pytest.fixture
def metadata_csv(tmp_path, two_files):
    path = tmp_path / "meta.csv"
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["mzml_path", "sample_id", "sample_class", "injection_order"])
        writer.writerow(["A.mzML", "S1", "QC", 1])
        writer.writerow(["B.mzML", "S2", "sample", 2])
    return path


# --- convert -----------------------------------------------------------------


def test_convert_creates_a_dataset(tmp_path, two_files):
    out = tmp_path / "store"
    assert main(["convert", *map(str, two_files), str(out), "--quiet"]) == 0
    assert layout.is_mzstack_dataset(out)


def test_convert_from_a_metadata_sheet(tmp_path, metadata_csv, capsys):
    out = tmp_path / "store"
    assert main(["convert", str(out), "--metadata-file", str(metadata_csv)]) == 0

    assert layout.is_mzstack_dataset(out)
    assert has_sample_metadata(out)
    meta = read_sample_metadata(out)
    assert list(meta["sample_id"]) == ["S1", "S2"]
    # The sheet's own path column is not carried; dataOrigin is the join key.
    assert "mzml_path" not in meta.columns
    assert layout.SAMPLE_METADATA_NAME in {p.name for p in out.iterdir()}


def test_sample_metadata_joins_to_the_spectra(tmp_path, metadata_csv):
    from mzstack.store import MzStack

    out = tmp_path / "store"
    main(["convert", str(out), "--metadata-file", str(metadata_csv), "--quiet"])

    origins = set(MzStack(out).spectra_data(columns=["dataOrigin"])["dataOrigin"])
    assert set(read_sample_metadata(out)["dataOrigin"]) == origins


def test_a_metadata_sheet_without_the_path_column_is_rejected(tmp_path, capsys):
    bad = tmp_path / "bad.csv"
    bad.write_text("sample_id\nS1\n")
    assert main(["convert", str(tmp_path / "store"), "--metadata-file", str(bad)]) == 1
    assert "no 'mzml_path' column" in capsys.readouterr().err


def test_convert_with_no_files_reports_an_error(tmp_path, capsys):
    assert main(["convert", str(tmp_path / "store")]) == 1
    assert "No input files" in capsys.readouterr().err


def test_convert_can_partition(tmp_path, two_files):
    out = tmp_path / "store"
    main(["convert", *map(str, two_files), str(out), "--partition", "--quiet"])
    dirs = list(layout.spectra_path(out).iterdir())
    assert all(d.name.startswith("data_origin=") for d in dirs)


# --- the read-side commands --------------------------------------------------


@pytest.fixture
def store_path(tmp_path, two_files):
    out = tmp_path / "store"
    main(["convert", *map(str, two_files), str(out), "--quiet"])
    return out


def test_list_reports_the_runs(store_path, capsys):
    assert main(["list", str(store_path)]) == 0
    out = capsys.readouterr().out
    assert "mzStack 0.1.0" in out
    assert "native" in out
    assert "8" in out  # two files of four spectra


def test_info_summarises_the_dataset(store_path, capsys):
    assert main(["info", str(store_path)]) == 0
    out = capsys.readouterr().out
    assert "kind:     native" in out
    assert "ms levels:" in out
    assert "rtime:" in out


def test_query_prints_results(store_path, capsys):
    pytest.importorskip("massql")
    assert (
        main(["query", str(store_path), "QUERY scannum(MS2DATA) WHERE MS2PROD=226.18"])
        == 0
    )
    assert "scan" in capsys.readouterr().out


def test_query_writes_to_a_file(store_path, tmp_path):
    pytest.importorskip("massql")
    import pandas as pd

    dest = tmp_path / "hits.csv"
    main(
        [
            "query",
            str(store_path),
            "QUERY scannum(MS2DATA) WHERE MS2PROD=226.18",
            "-o",
            str(dest),
        ]
    )
    assert dest.is_file()
    assert "scan" in pd.read_csv(dest).columns


def test_project_builds_and_drops(store_path, capsys):
    from mzstack.format.manifest import Manifest

    assert main(["project", str(store_path), "--type", "scansorted"]) == 0
    assert Manifest.read(store_path).has_projection("scansorted") == ["native"]

    assert main(["project", str(store_path), "--type", "scansorted", "--drop"]) == 0
    assert Manifest.read(store_path).has_projection("scansorted") == []


def test_match_runs(store_path, capsys):
    pytest.importorskip("blink")
    assert (
        main(["match", str(store_path), "--min-score", "0.5", "--min-matches", "2"])
        == 0
    )
    out = capsys.readouterr().out
    assert "query_id" in out or "no rows" in out


def test_a_missing_dataset_is_reported_not_raised(tmp_path, capsys):
    assert main(["info", str(tmp_path / "nope")]) == 1
    assert "not an mzStack dataset" in capsys.readouterr().err


# --- sample metadata directly ------------------------------------------------


def test_write_sample_metadata_requires_a_key_or_files(tmp_path):
    import pandas as pd

    with pytest.raises(ValueError, match="no 'dataOrigin' column"):
        write_sample_metadata(tmp_path, pd.DataFrame({"sample_id": ["S1"]}))


def test_write_sample_metadata_checks_the_row_count(tmp_path):
    import pandas as pd

    with pytest.raises(ValueError, match="must correspond row for row"):
        write_sample_metadata(
            tmp_path, pd.DataFrame({"sample_id": ["S1", "S2"]}), files=["a.mzML"]
        )


def test_read_sample_metadata_reports_absence_clearly(tmp_path):
    with pytest.raises(FileNotFoundError, match="sample_metadata"):
        read_sample_metadata(tmp_path)
