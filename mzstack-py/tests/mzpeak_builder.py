"""Builders for synthetic mzPeak archives.

Signal columns sit under a top-level ``point`` group; the metadata table is
keyed on ``index`` and side tables reference it through ``source_index``;
``time`` is in minutes; ``spectrum_representation`` is a CURIE; and the signal
file carries a ``spectrum_array_index`` in its Parquet footer naming the m/z
and intensity columns by CV term.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

MZ_ARRAY = "MS:1000514"
INTENSITY_ARRAY = "MS:1000515"

CENTROID = "MS:1000127"
PROFILE = "MS:1000128"


def _array_index(prefix: str = "point") -> bytes:
    return json.dumps(
        {
            "prefix": prefix,
            "entries": [
                {
                    "context": "spectrum",
                    "path": f"{prefix}.mz",
                    "data_type": "f64",
                    "array_type": MZ_ARRAY,
                    "array_name": "m/z array",
                    "unit": "MS:1000040",
                    "buffer_format": "point",
                    "buffer_priority": "primary",
                },
                {
                    "context": "spectrum",
                    "path": f"{prefix}.intensity",
                    "data_type": "f32",
                    "array_type": INTENSITY_ARRAY,
                    "array_name": "intensity array",
                    "unit": "MS:1000131",
                    "buffer_format": "point",
                    "buffer_priority": "primary",
                },
            ],
        }
    ).encode()


def write_signal(path: Path, spectra: list[dict], prefix: str = "point") -> Path:
    """A point-layout signal file: one row per data point, under ``prefix``."""
    index, mz, intensity = [], [], []
    for spec in spectra:
        for m, i in zip(spec["mz"], spec["intensity"], strict=True):
            index.append(spec["index"])
            mz.append(float(m))
            intensity.append(float(i))

    struct = pa.StructArray.from_arrays(
        [
            pa.array(index, pa.int64()),
            pa.array(mz, pa.float64()),
            pa.array(intensity, pa.float64()),
        ],
        names=["spectrum_index", "mz", "intensity"],
    )
    table = pa.table({prefix: struct})
    table = table.replace_schema_metadata(
        {b"spectrum_array_index": _array_index(prefix)}
    )
    pq.write_table(table, path)
    return path


def write_chunked_signal(path: Path, spectra: list[dict]) -> Path:
    """A *chunked*-layout signal file, which readers are expected to reject."""
    struct = pa.StructArray.from_arrays(
        [
            pa.array([s["index"] for s in spectra], pa.int64()),
            pa.array(
                [list(map(float, s["mz"])) for s in spectra], pa.list_(pa.float64())
            ),
        ],
        names=["spectrum_index", "mz_chunk_values"],
    )
    table = pa.table({"chunk": struct})
    table = table.replace_schema_metadata(
        {
            b"spectrum_array_index": json.dumps(
                {"prefix": "chunk", "entries": []}
            ).encode()
        }
    )
    pq.write_table(table, path)
    return path


def make_archive(
    directory: Path,
    spectra: list[dict] | None = None,
    run_id: str = "QC01",
    with_centroid: bool = True,
    with_profile: bool = False,
    omit: tuple[str, ...] = (),
    data_kind_spelling: str = "data_arrays",
    chunked: bool = False,
) -> Path:
    """Write a complete, conformant mzPeak archive.

    Args:
        directory: where to write it.
        spectra: the spectra; a small MS1/MS2/MS1/MS2 run by default.
        run_id: the run identifier the index declares.
        with_centroid: write ``spectra_peaks.parquet``.
        with_profile: write ``spectra_data.parquet``.
        omit: metadata columns to leave out, so a reader can be tested
            against archives that promoted different parameters.
        data_kind_spelling: how the index spells the signal kind; the
            specification uses ``data arrays`` and ``data_arrays``
            interchangeably.
        chunked: write the signal in the chunked layout instead.
    """
    directory.mkdir(parents=True, exist_ok=True)
    if spectra is None:
        spectra = default_spectra()

    files = []

    # --- metadata table, keyed on `index`
    columns: dict[str, pa.Array] = {
        "index": pa.array([s["index"] for s in spectra], pa.int64()),
        "id": pa.array([f"scan={s['index'] + 1}" for s in spectra], pa.string()),
        "ms_level": pa.array([s["ms_level"] for s in spectra], pa.int32()),
        # MINUTES, as the specification requires.
        "time": pa.array([s["time"] for s in spectra], pa.float64()),
        "scan_polarity": pa.array([s.get("polarity", 1) for s in spectra], pa.int32()),
        "spectrum_representation": pa.array(
            [CENTROID if with_centroid else PROFILE] * len(spectra), pa.string()
        ),
        "spectrum_type": pa.array(["MS:1000580"] * len(spectra), pa.string()),
        "lowest_observed_mz": pa.array([min(s["mz"]) for s in spectra], pa.float64()),
        "highest_observed_mz": pa.array([max(s["mz"]) for s in spectra], pa.float64()),
        "base_peak_mz": pa.array(
            [s["mz"][s["intensity"].index(max(s["intensity"]))] for s in spectra],
            pa.float64(),
        ),
        "base_peak_intensity": pa.array(
            [float(max(s["intensity"])) for s in spectra], pa.float64()
        ),
        "total_ion_current": pa.array(
            [float(sum(s["intensity"])) for s in spectra], pa.float64()
        ),
        "number_of_peaks": pa.array([len(s["mz"]) for s in spectra], pa.int32()),
    }
    for name in omit:
        columns.pop(name, None)
    pq.write_table(pa.table(columns), directory / "spectra_metadata.parquet")
    files.append(
        {
            "name": "spectra_metadata.parquet",
            "entity_type": "spectrum",
            "data_kind": "metadata",
            "column_mapping": [
                {
                    "path": "time",
                    "name": "scan start time",
                    "accession": "MS:1000016",
                    "unit": "UO:0000031",
                },
                {
                    "path": "ms_level",
                    "name": "ms level",
                    "accession": "MS:1000511",
                    "unit": None,
                },
            ],
        }
    )

    # --- scans side table, with a nested scan_windows group
    scan_windows = pa.array(
        [
            [{"scan_window_lower_limit": 50.0, "scan_window_upper_limit": 1000.0}]
            for _ in spectra
        ],
        pa.list_(
            pa.struct(
                [
                    ("scan_window_lower_limit", pa.float64()),
                    ("scan_window_upper_limit", pa.float64()),
                ]
            )
        ),
    )
    pq.write_table(
        pa.table(
            {
                "source_index": pa.array([s["index"] for s in spectra], pa.int64()),
                "scan_start_time": pa.array([s["time"] for s in spectra], pa.float64()),
                "filter_string": pa.array(["FTMS + p"] * len(spectra), pa.string()),
                "ion_injection_time": pa.array([7.5] * len(spectra), pa.float64()),
                "scan_windows": scan_windows,
            }
        ),
        directory / "spectra_metadata_scans.parquet",
    )
    files.append(
        {
            "name": "spectra_metadata_scans.parquet",
            "entity_type": "spectrum",
            "data_kind": "scans",
        }
    )

    # --- selected ions and precursors, for the MS2 spectra only
    msn = [s for s in spectra if s["ms_level"] > 1]
    if msn:
        pq.write_table(
            pa.table(
                {
                    "source_index": pa.array([s["index"] for s in msn], pa.int64()),
                    "precursor_index": pa.array(
                        [s.get("precursor_index") for s in msn], pa.int64()
                    ),
                    "selected_ion_mz": pa.array(
                        [s.get("precursor_mz") for s in msn], pa.float64()
                    ),
                    "charge_state": pa.array(
                        [s.get("charge") for s in msn], pa.int32()
                    ),
                    "intensity": pa.array([1.0e5] * len(msn), pa.float64()),
                }
            ),
            directory / "spectra_metadata_selected_ions.parquet",
        )
        files.append(
            {
                "name": "spectra_metadata_selected_ions.parquet",
                "entity_type": "spectrum",
                "data_kind": "selected_ions",
            }
        )

        isolation = pa.array(
            [
                {
                    "isolation_window_target": s.get("precursor_mz"),
                    "isolation_window_lower_offset": 0.5,
                    "isolation_window_upper_offset": 1.5,
                }
                for s in msn
            ],
            pa.struct(
                [
                    ("isolation_window_target", pa.float64()),
                    ("isolation_window_lower_offset", pa.float64()),
                    ("isolation_window_upper_offset", pa.float64()),
                ]
            ),
        )
        activation = pa.array(
            [{"collision_energy": 35.0} for _ in msn],
            pa.struct([("collision_energy", pa.float64())]),
        )
        pq.write_table(
            pa.table(
                {
                    "source_index": pa.array([s["index"] for s in msn], pa.int64()),
                    "precursor_index": pa.array(
                        [s.get("precursor_index") for s in msn], pa.int64()
                    ),
                    "isolation_window": isolation,
                    "activation": activation,
                }
            ),
            directory / "spectra_metadata_precursors.parquet",
        )
        files.append(
            {
                "name": "spectra_metadata_precursors.parquet",
                "entity_type": "spectrum",
                "data_kind": "precursors",
            }
        )

    # --- signal
    writer = write_chunked_signal if chunked else write_signal
    if with_centroid:
        writer(directory / "spectra_peaks.parquet", spectra)
        files.append(
            {
                "name": "spectra_peaks.parquet",
                "entity_type": "spectrum",
                "data_kind": "peaks",
            }
        )
    if with_profile:
        writer(directory / "spectra_data.parquet", spectra)
        files.append(
            {
                "name": "spectra_data.parquet",
                "entity_type": "spectrum",
                "data_kind": data_kind_spelling,
            }
        )

    (directory / "mzpeak_index.json").write_text(
        json.dumps(
            {
                "files": files,
                "metadata": {
                    "version": "0.9.0",
                    "cv_list": [
                        {"id": "MS", "version": "4.1.249"},
                        {"id": "UO", "version": "2026-01-16"},
                    ],
                    "run": {"id": run_id, "default_instrument_id": 1},
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return directory


def zip_archive(directory: Path, dest: Path) -> Path:
    """Pack an unpacked archive into a ``.mzpeak`` ZIP."""
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_STORED) as zf:
        for f in sorted(directory.iterdir()):
            if f.is_file():
                zf.write(f, f.name)
    return dest


def default_spectra() -> list[dict]:
    """An MS1/MS2/MS1/MS2 run, retention times in minutes."""
    return [
        {
            "index": 0,
            "ms_level": 1,
            "time": 1.0,
            "mz": [100.0, 200.0, 300.0],
            "intensity": [10.0, 40.0, 50.0],
        },
        {
            "index": 1,
            "ms_level": 2,
            "time": 1.1,
            "precursor_index": 0,
            "precursor_mz": 195.0877,
            "charge": 1,
            "mz": [50.0, 75.0],
            "intensity": [30.0, 70.0],
        },
        {
            "index": 2,
            "ms_level": 1,
            "time": 1.2,
            "mz": [100.0, 250.0, 300.5],
            "intensity": [20.0, 30.0, 50.0],
        },
        {
            "index": 3,
            "ms_level": 2,
            "time": 1.3,
            "precursor_index": 2,
            "precursor_mz": 195.0877,
            "charge": 1,
            "mz": [60.0, 90.0, 110.0],
            "intensity": [10.0, 20.0, 70.0],
        },
    ]
