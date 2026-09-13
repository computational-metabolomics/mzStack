"""What MetaboLights ingest must do.

The study is built on disk by `isatab_builder` and read with `local_only`, so
every test below exercises the real ISA-Tab parser and the real converter
without touching the network. Only the last test reaches the repository, and
it is marked accordingly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from isatab_builder import AssaySpec, write_study
from mzml_builder import SIMPLE_SPECTRA, make_spectrum, write_mzml
from mzstack.errors import StudyError

metabolights = pytest.importorskip(
    "mzstack.ingest.metabolights",
    reason="needs metabolights-utils: pip install 'mzstack[metabolights]'",
)
pytest.importorskip("metabolights_utils")

pytestmark = pytest.mark.requires_metabolights

STUDY = "MTBLSTEST"


def _write_mzml(path: Path) -> Path:
    spectra = [
        make_spectrum(index=i, scan=i + 1, ms_level=level, rt=rt, mz=mz, intensity=it)
        for i, (level, rt, mz, it) in enumerate(SIMPLE_SPECTRA)
    ]
    return write_mzml(path, spectra)


@pytest.fixture
def study(tmp_path: Path) -> Path:
    """A cache root holding a two-assay study with real mzML in `FILES/`.

    Positive assay: S1, S2. Negative assay: S1, S3. mzML is referenced from
    `Derived Spectral Data File`, which is where MetaboLights studies most
    often keep it.
    """
    root = tmp_path / "cache"
    directory = write_study(
        root,
        [
            AssaySpec(
                "a_MTBLSTEST_POS.txt",
                ["S1", "S2"],
                ["S1_pos.mzML", "S2_pos.mzML"],
                column="derived",
                polarity="positive",
            ),
            AssaySpec(
                "a_MTBLSTEST_NEG.txt",
                ["S1", "S3"],
                ["S1_neg.mzML", "QC_blank_neg.mzML"],
                column="derived",
                polarity="negative",
            ),
        ],
        study_id=STUDY,
        treatments={"S1": "drought", "S2": "control", "S3": "control"},
    )
    for name in ("S1_pos", "S2_pos", "S1_neg", "QC_blank_neg"):
        _write_mzml(directory / "FILES" / f"{name}.mzML")
    return root


def _files(root: Path):
    return metabolights.list_study_files(STUDY, cache_dir=root, local_only=True)


# --- finding the study's mzML ------------------------------------------------


def test_mzml_referenced_as_a_derived_file_is_found(study: Path):
    """The common real case: `Raw Spectral Data File` is empty and the mzML
    path sits in `Derived Spectral Data File`."""
    files = _files(study)

    assert len(files) == 4
    assert {f.remote_path for f in files} == {
        "FILES/S1_pos.mzML",
        "FILES/S2_pos.mzML",
        "FILES/S1_neg.mzML",
        "FILES/QC_blank_neg.mzML",
    }
    assert {f.column for f in files} == {"derived"}


def test_mzml_referenced_as_a_raw_file_is_found(tmp_path: Path):
    """Studies that acquired mzML directly reference it from the raw column."""
    root = tmp_path / "cache"
    write_study(
        root,
        [AssaySpec("a_MTBLSTEST_POS.txt", ["S1"], ["S1.mzML"], column="raw")],
        study_id=STUDY,
    )

    files = _files(root)

    assert [f.remote_path for f in files] == ["FILES/S1.mzML"]
    assert files[0].column == "raw"


def test_each_file_carries_the_sample_it_came_from(study: Path):
    """Selection by sample and the metadata join both need the assay row's
    identity, not just the file name."""
    by_path = {f.remote_path: f for f in _files(study)}

    assert by_path["FILES/S1_pos.mzML"].sample_name == "S1"
    assert by_path["FILES/S1_pos.mzML"].assay_name == "S1_pos"
    assert by_path["FILES/S1_pos.mzML"].assay == "a_MTBLSTEST_POS.txt"
    assert by_path["FILES/QC_blank_neg.mzML"].sample_name == "S3"


def test_a_study_without_mzml_is_refused(tmp_path: Path):
    """The whole point of the format check: vendor formats cannot be read, so
    the study is refused up front rather than half-ingested."""
    root = tmp_path / "cache"
    write_study(
        root,
        [
            AssaySpec("a_MTBLSTEST_POS.txt", ["S1"], ["S1.raw"], column="raw"),
            AssaySpec("a_MTBLSTEST_NEG.txt", ["S2"], ["S2.d.zip"], column="raw"),
        ],
        study_id=STUDY,
    )

    with pytest.raises(StudyError) as excinfo:
        _files(root)

    message = str(excinfo.value)
    assert "no mzML" in message
    # The message must say what the study does hold, or the user cannot tell
    # whether converting it themselves is worth the trouble.
    assert ".raw" in message
    assert ".zip" in message


def test_non_mzml_files_in_a_study_that_has_mzml_are_ignored(tmp_path: Path):
    """A mixed study is ingestible; only its mzML is taken."""
    root = tmp_path / "cache"
    write_study(
        root,
        [
            AssaySpec("a_MTBLSTEST_POS.txt", ["S1"], ["S1.raw"], column="raw"),
            AssaySpec("a_MTBLSTEST_NEG.txt", ["S2"], ["S2.mzML"], column="derived"),
        ],
        study_id=STUDY,
    )

    assert [f.remote_path for f in _files(root)] == ["FILES/S2.mzML"]


def test_mzml_extension_is_matched_case_insensitively(tmp_path: Path):
    root = tmp_path / "cache"
    write_study(
        root,
        [AssaySpec("a_MTBLSTEST_POS.txt", ["S1"], ["S1.MZML"], column="derived")],
        study_id=STUDY,
    )

    assert [f.remote_path for f in _files(root)] == ["FILES/S1.MZML"]


# --- choosing a subset -------------------------------------------------------


def test_no_filters_selects_everything(study: Path):
    files = _files(study)

    assert len(metabolights.select_files(files)) == 4


def test_selection_by_assay(study: Path):
    selected = metabolights.select_files(_files(study), assays=["a_MTBLSTEST_NEG.txt"])

    assert {f.name for f in selected} == {"S1_neg.mzML", "QC_blank_neg.mzML"}


def test_selection_by_sample_name(study: Path):
    """S1 appears in both assays, so selecting it crosses assay boundaries."""
    selected = metabolights.select_files(_files(study), samples=["S1"])

    assert {f.name for f in selected} == {"S1_pos.mzML", "S1_neg.mzML"}


def test_selection_by_ms_assay_name(study: Path):
    """`--sample` matches the MS Assay Name too, which is what distinguishes
    one acquisition of a sample from another."""
    selected = metabolights.select_files(_files(study), samples=["S1_neg"])

    assert [f.name for f in selected] == ["S1_neg.mzML"]


def test_selection_by_filename_glob(study: Path):
    selected = metabolights.select_files(_files(study), include=["*_pos.mzML"])

    assert {f.name for f in selected} == {"S1_pos.mzML", "S2_pos.mzML"}


def test_exclude_is_applied_last_and_wins(study: Path):
    selected = metabolights.select_files(
        _files(study), include=["*_neg.mzML"], exclude=["QC*"]
    )

    assert [f.name for f in selected] == ["S1_neg.mzML"]


def test_filters_combine(study: Path):
    selected = metabolights.select_files(
        _files(study), assays=["a_MTBLSTEST_POS.txt"], samples=["S2"]
    )

    assert [f.name for f in selected] == ["S2_pos.mzML"]


def test_an_assay_glob_is_accepted(study: Path):
    selected = metabolights.select_files(_files(study), assays=["*_NEG.txt"])

    assert len(selected) == 2


def test_a_selection_matching_nothing_raises(study: Path):
    """A mistyped assay name must not quietly fall back to the whole study --
    that is the difference between no download and a very large one."""
    with pytest.raises(StudyError) as excinfo:
        metabolights.select_files(_files(study), assays=["a_MTBLSTEST_TYPO.txt"])

    message = str(excinfo.value)
    assert "a_MTBLSTEST_POS.txt" in message
    assert "a_MTBLSTEST_NEG.txt" in message


# --- conversion --------------------------------------------------------------


def test_only_the_selected_files_are_converted(study: Path, tmp_path: Path):
    from mzstack.store import MzStack

    dataset = metabolights.metabolights_to_mzstack(
        STUDY,
        tmp_path / "ds",
        cache_dir=study,
        assays=["a_MTBLSTEST_POS.txt"],
        local_only=True,
        verbose=False,
    )

    # Four spectra per file, two files selected out of four available.
    assert MzStack(dataset).manifest.n_spectra == 8


def test_sample_metadata_describes_each_converted_file(study: Path, tmp_path: Path):
    from mzstack.samples import read_sample_metadata

    dataset = metabolights.metabolights_to_mzstack(
        STUDY,
        tmp_path / "ds",
        cache_dir=study,
        include=["S1_*.mzML"],
        local_only=True,
        verbose=False,
    )
    table = read_sample_metadata(dataset)

    assert len(table) == 2
    assert set(table["study_id"]) == {STUDY}
    assert set(table["sample_name"]) == {"S1"}
    # The study's own annotation, from both sheets, under their prefixes.
    assert set(table["sample.Factor Value[Treatment]"]) == {"drought"}
    assert set(table["assay.Parameter Value[Scan polarity]"]) == {
        "positive",
        "negative",
    }


def test_sample_metadata_joins_to_the_spectra(study: Path, tmp_path: Path):
    """`dataOrigin` is the join key, so it must equal what the converter wrote
    for the same file -- a resolved absolute path."""
    from mzstack.samples import read_sample_metadata
    from mzstack.store import MzStack

    dataset = metabolights.metabolights_to_mzstack(
        STUDY,
        tmp_path / "ds",
        cache_dir=study,
        assays=["a_MTBLSTEST_POS.txt"],
        local_only=True,
        verbose=False,
    )

    origins = set(MzStack(dataset).spectra_data(columns=["dataOrigin"])["dataOrigin"])
    assert set(read_sample_metadata(dataset)["dataOrigin"]) == origins


def test_the_cache_is_reused_and_never_emptied(study: Path, tmp_path: Path):
    """Converting twice must not disturb the downloaded study."""
    before = sorted(p.name for p in (study / STUDY / "FILES").iterdir())

    for name in ("ds1", "ds2"):
        metabolights.metabolights_to_mzstack(
            STUDY,
            tmp_path / name,
            cache_dir=study,
            assays=["a_MTBLSTEST_POS.txt"],
            local_only=True,
            verbose=False,
        )

    assert sorted(p.name for p in (study / STUDY / "FILES").iterdir()) == before


def test_a_file_missing_from_the_cache_is_reported(study: Path, tmp_path: Path):
    (study / STUDY / "FILES" / "S1_pos.mzML").unlink()

    with pytest.raises(StudyError, match="not in the cache"):
        metabolights.metabolights_to_mzstack(
            STUDY,
            tmp_path / "ds",
            cache_dir=study,
            assays=["a_MTBLSTEST_POS.txt"],
            local_only=True,
            verbose=False,
        )


def test_partitioning_is_passed_through(study: Path, tmp_path: Path):
    dataset = metabolights.metabolights_to_mzstack(
        STUDY,
        tmp_path / "ds",
        cache_dir=study,
        assays=["a_MTBLSTEST_POS.txt"],
        partitioning=("dataOrigin",),
        local_only=True,
        verbose=False,
    )

    partitions = list((dataset / "spectra").glob("data_origin=*"))
    assert len(partitions) == 2


# --- the command line --------------------------------------------------------


def test_cli_list_prints_the_selection_without_converting(
    study: Path, tmp_path: Path, capsys
):
    from mzstack.cli import main

    code = main(
        [
            "metabolights",
            STUDY,
            "--cache-dir",
            str(study),
            "--local-only",
            "--list",
            "--assay",
            "a_MTBLSTEST_NEG.txt",
        ]
    )
    out = capsys.readouterr().out

    assert code == 0
    assert "S1_neg.mzML" in out
    assert "S1_pos.mzML" not in out
    assert "2 file(s)" in out


def test_cli_converts_a_subset(study: Path, tmp_path: Path):
    from mzstack.cli import main
    from mzstack.store import MzStack

    dataset = tmp_path / "ds"
    code = main(
        [
            "metabolights",
            STUDY,
            str(dataset),
            "--cache-dir",
            str(study),
            "--local-only",
            "--include",
            "S1_pos.mzML",
            "-q",
        ]
    )

    assert code == 0
    assert MzStack(dataset).manifest.n_spectra == 4


def test_cli_reports_a_study_without_mzml(tmp_path: Path, capsys):
    """`cli.main` turns the refusal into a message and exit code 1, not a
    traceback."""
    from mzstack.cli import main

    root = tmp_path / "cache"
    write_study(
        root,
        [AssaySpec("a_MTBLSTEST_POS.txt", ["S1"], ["S1.raw"], column="raw")],
        study_id=STUDY,
    )

    code = main(
        ["metabolights", STUDY, "--cache-dir", str(root), "--local-only", "--list"]
    )

    assert code == 1
    assert "no mzML" in capsys.readouterr().err


def test_cli_requires_an_output_unless_listing(study: Path, capsys):
    from mzstack.cli import main

    code = main(["metabolights", STUDY, "--cache-dir", str(study), "--local-only"])

    assert code == 1
    assert "No output dataset" in capsys.readouterr().err


# --- against the real repository ---------------------------------------------


@pytest.mark.network
def test_a_real_study_resolves_to_its_mzml(tmp_path: Path):
    """MTBLS341 keeps mzML in `Derived Spectral Data File`; MTBLS733 has only
    vendor `.raw`. Both behaviours are what the offline tests stand in for."""
    files = metabolights.list_study_files("MTBLS341", cache_dir=tmp_path)
    assert files
    assert all(f.name.lower().endswith(".mzml") for f in files)

    exudate = metabolights.select_files(
        files, assays=["a_MTBLS341_exudate_NEG_LCMS.txt"]
    )
    assert 0 < len(exudate) < len(files)

    with pytest.raises(StudyError, match="no mzML"):
        metabolights.list_study_files("MTBLS733", cache_dir=tmp_path)
