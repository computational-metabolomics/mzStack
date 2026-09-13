"""Cross-implementation conformance, checked by shelling out to ``Rscript``.

Datasets written by mzstack must be readable by the R implementation of
mzStack and vice versa. Skipped when R or the package is absent.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from mzml_builder import SIMPLE_SPECTRA, make_spectrum, write_mzml
from mzstack import duck
from mzstack.format import layout
from mzstack.format.manifest import Manifest
from mzstack.ingest import mzml_to_mzstack
from mzstack.projections import build_projection
from mzstack.store import MzStack

pytestmark = pytest.mark.requires_r


def _r_probe(expr: str) -> bool:
    if shutil.which("Rscript") is None:
        return False
    probe = subprocess.run(["Rscript", "-e", expr], capture_output=True, text=True)
    return probe.returncode == 0 and "ok" in probe.stdout


def _have_r() -> bool:
    return _r_probe('library(MsBackendParquet); cat("ok")')


def _r_speaks_mzpeak_vocabulary() -> bool:
    """Does the installed R build write mzPeak names for a native dataset?

    A build without the shared vocabulary stores canonical names and reads
    nothing but nulls from a dataset written here.
    """
    return _r_probe(
        "library(MsBackendParquet); "
        'if (exists(".spectra_df_to_mzpeak", envir = asNamespace("MsBackendParquet")))'
        ' cat("ok")'
    )


pytest.importorskip("pyarrow")
if not _have_r():  # pragma: no cover - environment dependent
    pytest.skip(
        "needs Rscript with MsBackendParquet installed", allow_module_level=True
    )
if not _r_speaks_mzpeak_vocabulary():  # pragma: no cover - environment dependent
    pytest.skip(
        "the installed MsBackendParquet predates the shared mzPeak column "
        "vocabulary; reinstall it to run the conformance tests",
        allow_module_level=True,
    )


def r_script(body: str, *args: str) -> str:
    """Run an R snippet, returning stdout. Raises with stderr on failure."""
    result = subprocess.run(
        ["Rscript", "-e", body, *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"Rscript failed ({result.returncode}):\n{result.stderr}\n{result.stdout}"
        )
    return result.stdout


_READ_SPECTRA = """
suppressMessages({library(Spectra); library(MsBackendParquet); library(jsonlite)})
p <- commandArgs(TRUE)[1]
sps <- Spectra(backendInitialize(MsBackendParquet(), path = p))
pk <- peaksData(sps)
cat(toJSON(list(
    n = length(sps),
    msLevel = msLevel(sps),
    rtime = rtime(sps),
    precursorMz = precursorMz(sps),
    polarity = polarity(sps),
    mz = lapply(pk, function(m) as.numeric(m[, "mz"])),
    intensity = lapply(pk, function(m) as.numeric(m[, "intensity"]))
), auto_unbox = TRUE, na = "null", digits = 12))
"""


def read_with_r(path: Path) -> dict:
    """What R sees in the dataset at ``path``."""
    return json.loads(r_script(_READ_SPECTRA, str(path)))


# --- Python writes, R reads --------------------------------------------------


def test_r_reads_a_python_written_dataset(simple_store):
    got = read_with_r(simple_store.path)

    assert got["n"] == len(SIMPLE_SPECTRA)
    assert got["msLevel"] == [s[0] for s in SIMPLE_SPECTRA]
    # Seconds on both sides: the factor-of-60 check, across implementations.
    assert got["rtime"] == pytest.approx([s[1] for s in SIMPLE_SPECTRA])

    for arrays, (_, _, mz, intensity) in zip(
        zip(got["mz"], got["intensity"], strict=True), SIMPLE_SPECTRA, strict=True
    ):
        np.testing.assert_allclose(arrays[0], mz)
        np.testing.assert_allclose(arrays[1], intensity)


def test_r_and_python_agree_on_precursor_and_polarity(simple_store):
    got = read_with_r(simple_store.path)
    mine = simple_store.spectra_data(columns=["precursorMz", "polarity"])

    theirs = [None if v is None else float(v) for v in got["precursorMz"]]
    ours = [None if np.isnan(v) else float(v) for v in mine["precursorMz"]]
    assert theirs == pytest.approx(ours, nan_ok=True)
    assert got["polarity"] == list(mine["polarity"])


def test_r_reads_a_partitioned_python_dataset(simple_mzml, tmp_path):
    path = tmp_path / "partitioned"
    mzml_to_mzstack(simple_mzml, path, partitioning=("dataOrigin",))
    assert read_with_r(path)["n"] == len(SIMPLE_SPECTRA)


def test_r_reads_a_multi_file_python_dataset(tmp_path):
    a = write_mzml(
        tmp_path / "a.mzML",
        [make_spectrum(i, i + 1, 1, float(i), [100.0 + i], [1.0]) for i in range(3)],
    )
    b = write_mzml(
        tmp_path / "b.mzML",
        [make_spectrum(i, i + 1, 1, float(i), [200.0 + i], [2.0]) for i in range(2)],
    )
    path = tmp_path / "store"
    mzml_to_mzstack([a, b], path)

    got = read_with_r(path)
    assert got["n"] == 5
    assert len(got["mz"]) == 5


# --- R writes, Python reads --------------------------------------------------


_WRITE_DATASET = """
suppressMessages({library(Spectra); library(MsBackendParquet); library(S4Vectors)})
out <- commandArgs(TRUE)[1]
df <- DataFrame(msLevel = c(1L, 2L, 1L),
                rtime = c(10.5, 11.5, 12.5),
                precursorMz = c(NA_real_, 300.25, NA_real_),
                polarity = c(1L, 1L, 0L))
df$mz <- list(c(100.0, 200.0), c(50.5, 60.5, 70.5), c(300.0))
df$intensity <- list(c(1.0, 2.0), c(3.0, 4.0, 5.0), c(6.0))
invisible(createMsBackendParquetDataset(path = out, data = df))
cat("done")
"""

R_WRITTEN_MZ = [[100.0, 200.0], [50.5, 60.5, 70.5], [300.0]]
R_WRITTEN_INTENSITY = [[1.0, 2.0], [3.0, 4.0, 5.0], [6.0]]


@pytest.fixture
def r_store(tmp_path) -> MzStack:
    path = tmp_path / "r-store"
    r_script(_WRITE_DATASET, str(path))
    return MzStack(path)


def test_python_reads_an_r_written_dataset(r_store):
    assert r_store.kind == layout.KIND_NATIVE
    assert len(r_store) == 3

    df = r_store.spectra_data(columns=["msLevel", "rtime", "precursorMz", "polarity"])
    assert list(df["msLevel"]) == [1, 2, 1]
    assert list(df["rtime"]) == pytest.approx([10.5, 11.5, 12.5])
    assert list(df["polarity"]) == [1, 1, 0]
    assert df["precursorMz"][1] == pytest.approx(300.25)


def test_python_reads_r_written_peaks(r_store):
    peaks = r_store.peaks_data()
    assert len(peaks) == 3
    for (mz, intensity), want_mz, want_i in zip(
        peaks, R_WRITTEN_MZ, R_WRITTEN_INTENSITY, strict=True
    ):
        np.testing.assert_allclose(mz, want_mz)
        np.testing.assert_allclose(intensity, want_i)


def test_python_filters_an_r_written_dataset(r_store):
    assert list(r_store.filter_ms_level(2).spectrum_ids) == [2]
    assert list(r_store.filter_rt((10.0, 11.0)).spectrum_ids) == [1]
    assert list(r_store.filter_contains_mz(60.5, tolerance=0.01).spectrum_ids) == [2]


def test_python_runs_massql_over_an_r_written_dataset(r_store):
    pytest.importorskip("massql")
    result = r_store.massql("QUERY scannum(MS2DATA) WHERE MS2PROD=60.5")
    assert sorted(result["scan"]) == [2]


# --- the manifest itself -----------------------------------------------------


def test_both_implementations_write_the_same_manifest_shape(r_store, simple_store):
    """Keys, nesting and JSON types must match, not merely parse."""
    mine = json.loads(layout.manifest_path(simple_store.path).read_text())
    theirs = json.loads(layout.manifest_path(r_store.path).read_text())

    assert mine.keys() == theirs.keys()
    assert mine["format"] == theirs["format"] == "mzStack"
    assert mine["version"] == theirs["version"]

    (my_run,), (their_run,) = mine["runs"], theirs["runs"]
    assert my_run.keys() == their_run.keys()
    assert my_run["signal"].keys() == their_run["signal"].keys()

    for key in ("n_spectra", "uid_base", "ingested_at"):
        assert isinstance(their_run[key], int)
        assert isinstance(my_run[key], int)

    # `{}`, not `[]`: an empty JSON array reads back as a list, not a map.
    assert my_run["projections"] == their_run["projections"] == {}
    assert their_run["signal"]["profile"] is None
    assert my_run["signal"]["profile"] is None


def test_r_still_reads_a_dataset_carrying_an_mzstack_extension(simple_store):
    """`scansorted` is an mzstack extension; R must ignore it."""
    build_projection(simple_store.path, layout.PROJECTION_SCANSORTED, verbose=False)
    duck.invalidate(simple_store.path)

    got = read_with_r(simple_store.path)
    assert got["n"] == len(SIMPLE_SPECTRA)
    np.testing.assert_allclose(got["mz"][0], SIMPLE_SPECTRA[0][2])


def test_an_unknown_projection_survives_r_rewriting_the_manifest(simple_store):
    """R rewrites the whole manifest, so our entry must survive it."""
    build_projection(simple_store.path, layout.PROJECTION_SCANSORTED, verbose=False)
    before = Manifest.read(simple_store.path)
    assert before.has_projection(layout.PROJECTION_SCANSORTED) == [layout.NATIVE_RUN_ID]

    # Any R call that writes the manifest back will do; dropProjection on a
    # type that is not there rewrites it without otherwise changing it.
    r_script(
        """
        suppressMessages(library(MsBackendParquet))
        p <- commandArgs(TRUE)[1]
        m <- MsBackendParquet:::.manifest_read(p)
        MsBackendParquet:::.manifest_write(p, m)
        cat("done")
        """,
        str(simple_store.path),
    )

    after = Manifest.read(simple_store.path)
    assert after.has_projection(layout.PROJECTION_SCANSORTED) == [layout.NATIVE_RUN_ID]


def test_r_refuses_a_dataset_with_no_manifest(simple_store, tmp_path):
    """A missing manifest is fatal on both sides."""
    broken = tmp_path / "broken"
    shutil.copytree(simple_store.path, broken)
    layout.manifest_path(broken).unlink()

    with pytest.raises(AssertionError, match="not an mzStack dataset|no mzStack.json"):
        read_with_r(broken)


# --- mzPeak-indexed datasets -------------------------------------------------


def test_r_reads_a_python_indexed_mzpeak_dataset(tmp_path):
    """Both index the same archives and read the same peaks."""
    from mzpeak_builder import default_spectra, make_archive

    from mzstack.ingest.mzpeak import create_mzpeak_dataset

    archive = make_archive(tmp_path / "QC01.mzpeak", run_id="QC01")
    path = tmp_path / "store"
    create_mzpeak_dataset([archive], path, verbose=False)

    got = read_with_r(path)
    assert got["n"] == 4
    assert got["msLevel"] == [1, 2, 1, 2]
    # The archive stores `time` in minutes; both sides report seconds.
    assert got["rtime"] == pytest.approx([60.0, 66.0, 72.0, 78.0])

    for arrays, spec in zip(
        zip(got["mz"], got["intensity"], strict=True), default_spectra(), strict=True
    ):
        np.testing.assert_allclose(arrays[0], spec["mz"])
        np.testing.assert_allclose(arrays[1], spec["intensity"])


def test_r_reading_an_mzpeak_dataset_does_not_touch_the_archive(tmp_path):
    import hashlib

    from mzpeak_builder import make_archive

    from mzstack.ingest.mzpeak import create_mzpeak_dataset

    archive = make_archive(tmp_path / "QC01.mzpeak", run_id="QC01")
    path = tmp_path / "store"
    create_mzpeak_dataset([archive], path, verbose=False)

    def sums():
        return {
            f.name: hashlib.md5(f.read_bytes()).hexdigest()
            for f in sorted(archive.iterdir())
            if f.is_file()
        }

    before = sums()
    read_with_r(path)
    assert sums() == before


# --- the shared on-disk vocabulary -------------------------------------------


_NATIVE_COLUMNS = """
suppressMessages(library(MsBackendParquet))
out <- commandArgs(TRUE)[1]
cat(paste(sort(names(arrow::open_dataset(file.path(out, "spectra")))),
          collapse = ","))
"""


def test_both_implementations_write_the_same_native_columns(r_store, tmp_path):
    """Not merely mutually readable: the same column names on disk."""
    from mzstack.ingest.native import NativeWriter

    # The same three spectra the R fixture writes. `dataOrigin` is explicit:
    # R's DataFrame entry point defaults it to "<mzStack>" when absent, while
    # every mzstack ingest path sets it from the source file.
    origin = "<mzStack>"
    records = [
        {
            "msLevel": 1,
            "rtime": 10.5,
            "polarity": 1,
            "dataOrigin": origin,
            "mz": [100.0, 200.0],
            "intensity": [1.0, 2.0],
        },
        {
            "msLevel": 2,
            "rtime": 11.5,
            "polarity": 1,
            "precursorMz": 300.25,
            "dataOrigin": origin,
            "mz": [50.5, 60.5, 70.5],
            "intensity": [3.0, 4.0, 5.0],
        },
        {
            "msLevel": 1,
            "rtime": 12.5,
            "polarity": 0,
            "dataOrigin": origin,
            "mz": [300.0],
            "intensity": [6.0],
        },
    ]
    mine_path = tmp_path / "py-store"
    with NativeWriter(mine_path) as writer:
        for record in records:
            writer.add(dict(record))

    theirs = set(r_script(_NATIVE_COLUMNS, str(r_store.path)).strip().split(","))
    mine = set(r_script(_NATIVE_COLUMNS, str(mine_path)).strip().split(","))
    assert mine == theirs


def test_r_reads_python_written_encodings(simple_store):
    """The values R decodes must match what mzstack encoded."""
    got = read_with_r(simple_store.path)
    assert got["rtime"] == pytest.approx([s[1] for s in SIMPLE_SPECTRA])
    assert got["polarity"] == [1, 1, 1, 1]
