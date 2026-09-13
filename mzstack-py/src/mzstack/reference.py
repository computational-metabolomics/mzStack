"""Reference spectral libraries, stored as Parquet.

Two files -- ``entries.parquet``, one row per reference spectrum, and
``peaks.parquet``, one row per peak -- so that reading a large library is a
column read rather than an MSP text parse.

Libraries are not mzStack datasets: they describe no acquisition, so they have
no runs, retention times or provenance to record.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from .errors import MzStackError

__all__ = [
    "ReferenceLibrary",
    "msp_to_reference",
    "SpectrumSet",
    "store_as_spectrum_set",
]

ENTRIES_NAME = "entries.parquet"
PEAKS_NAME = "peaks.parquet"

ENTRIES_SCHEMA = pa.schema(
    [
        pa.field("ref_id", pa.int64(), nullable=False),
        pa.field("name", pa.string()),
        pa.field("inchikey", pa.string()),
        pa.field("smiles", pa.string()),
        pa.field("formula", pa.string()),
        pa.field("adduct", pa.string()),
        pa.field("precursor_mz", pa.float64()),
        pa.field("charge", pa.int32()),
        pa.field("polarity", pa.int32()),
        pa.field("n_peaks", pa.int32()),
    ]
)

PEAKS_SCHEMA = pa.schema(
    [
        pa.field("ref_id", pa.int64(), nullable=False),
        pa.field("mz", pa.float64(), nullable=False),
        pa.field("intensity", pa.float64(), nullable=False),
    ]
)


@dataclass(frozen=True)
class SpectrumSet:
    """Spectra in the shape BLINK takes, with ids to join results back.

    Attributes:
        peaks: one ``(2, n)`` array per spectrum -- row 0 m/z, row 1 intensity.
        precursor_mz: one precursor m/z per spectrum; NaN where unknown.
        ids: identifier per spectrum -- ``spectrum_id_`` for a store,
            ``ref_id`` for a library.
    """

    peaks: list[np.ndarray]
    precursor_mz: list[float]
    ids: np.ndarray

    def __len__(self) -> int:
        return len(self.peaks)


def store_as_spectrum_set(store) -> SpectrumSet:
    """Read an `MzStack` selection into BLINK's input shape."""
    ids = store.spectrum_ids
    peaks = store.peaks_data()
    precursors = store.spectra_data(columns=["precursorMz"])["precursorMz"].to_numpy()

    arrays = [
        np.vstack([np.asarray(mz, dtype=np.float64), np.asarray(i, dtype=np.float64)])
        for mz, i in peaks
    ]
    return SpectrumSet(
        peaks=arrays,
        precursor_mz=[float(v) for v in precursors],
        ids=np.asarray(ids, dtype=np.int64),
    )


# --- MSP parsing -------------------------------------------------------------

_PEAK_LINE = re.compile(
    r"^\s*([0-9]*\.?[0-9]+(?:[eE][-+]?\d+)?)[\s,:]+"
    r"([0-9]*\.?[0-9]+(?:[eE][-+]?\d+)?)"
)

#: MSP field names vary between exporters; map them onto one set of names.
_FIELD_ALIASES = {
    "name": "name",
    "title": "name",
    "inchikey": "inchikey",
    "inchi_key": "inchikey",
    "smiles": "smiles",
    "formula": "formula",
    "molecular_formula": "formula",
    "precursormz": "precursor_mz",
    "precursor_mz": "precursor_mz",
    "precursor_m/z": "precursor_mz",
    "exactmass": "precursor_mz",
    "precursortype": "adduct",
    "adduct": "adduct",
    "adduct_type": "adduct",
    "charge": "charge",
    "ionmode": "polarity",
    "ion_mode": "polarity",
    "polarity": "polarity",
}


def _polarity(value: str) -> int | None:
    v = value.strip().lower()
    if v.startswith("p") or v == "+":
        return 1
    if v.startswith("n") or v == "-":
        return 0
    return None


def _finish(fields: dict, mz: list[float], intensity: list[float]) -> dict | None:
    if not mz:
        return None
    entry = {
        k: fields.get(k) for k in ("name", "inchikey", "smiles", "formula", "adduct")
    }
    try:
        entry["precursor_mz"] = float(fields["precursor_mz"])
    except (KeyError, TypeError, ValueError):
        entry["precursor_mz"] = None
    try:
        entry["charge"] = int(float(fields["charge"]))
    except (KeyError, TypeError, ValueError):
        entry["charge"] = None
    entry["polarity"] = _polarity(fields["polarity"]) if "polarity" in fields else None
    entry["mz"] = mz
    entry["intensity"] = intensity
    entry["n_peaks"] = len(mz)
    return entry


def iter_msp(path: str | Path) -> Iterator[dict]:
    """Stream entries from a NIST/MassBank-format MSP file."""
    fields: dict[str, str] = {}
    mz: list[float] = []
    intensity: list[float] = []
    in_peaks = False

    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\r\n")
            if not line.strip():
                # A blank line ends the entry.
                entry = _finish(fields, mz, intensity)
                if entry is not None:
                    yield entry
                fields, mz, intensity, in_peaks = {}, [], [], False
                continue

            if not in_peaks and ":" in line:
                key, _, value = line.partition(":")
                key = key.strip().lower()
                if key in ("num peaks", "num_peaks", "numpeaks"):
                    in_peaks = True
                    continue
                mapped = _FIELD_ALIASES.get(key)
                if mapped is not None:
                    fields[mapped] = value.strip()
                continue

            match = _PEAK_LINE.match(line)
            if match:
                in_peaks = True
                mz.append(float(match.group(1)))
                intensity.append(float(match.group(2)))

    entry = _finish(fields, mz, intensity)
    if entry is not None:
        yield entry


def msp_to_reference(
    msp_path: str | Path,
    out_dir: str | Path,
    chunk_size: int = 5000,
    verbose: bool = False,
) -> Path:
    """Convert an MSP file into a Parquet reference library."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    entries_writer = pq.ParquetWriter(
        out / ENTRIES_NAME, ENTRIES_SCHEMA, compression="zstd"
    )
    peaks_writer = pq.ParquetWriter(out / PEAKS_NAME, PEAKS_SCHEMA, compression="zstd")

    entry_rows: list[dict] = []
    peak_ids: list[int] = []
    peak_mz: list[float] = []
    peak_i: list[float] = []
    ref_id = 0

    def flush() -> None:
        if entry_rows:
            entries_writer.write_table(
                pa.Table.from_pylist(entry_rows, schema=ENTRIES_SCHEMA)
            )
            entry_rows.clear()
        if peak_ids:
            peaks_writer.write_table(
                pa.table(
                    {"ref_id": peak_ids, "mz": peak_mz, "intensity": peak_i},
                    schema=PEAKS_SCHEMA,
                )
            )
            peak_ids.clear()
            peak_mz.clear()
            peak_i.clear()

    try:
        for entry in iter_msp(msp_path):
            mz = entry.pop("mz")
            intensity = entry.pop("intensity")
            entry_rows.append({"ref_id": ref_id, **entry})
            peak_ids.extend([ref_id] * len(mz))
            peak_mz.extend(mz)
            peak_i.extend(intensity)
            ref_id += 1
            if len(entry_rows) >= chunk_size:
                flush()
        flush()
    finally:
        entries_writer.close()
        peaks_writer.close()

    if verbose:
        print(f"{out}: {ref_id} reference spectra")
    return out


class ReferenceLibrary:
    """A Parquet reference library."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        entries = self.path / ENTRIES_NAME
        peaks = self.path / PEAKS_NAME
        if not entries.is_file() or not peaks.is_file():
            raise MzStackError(
                f"'{path}' is not a reference library: expected "
                f"{ENTRIES_NAME} and {PEAKS_NAME}. Build one with "
                "msp_to_reference()."
            )
        self._entries_path = entries
        self._peaks_path = peaks

    @classmethod
    def from_msp(cls, msp_path: str | Path, out_dir: str | Path) -> ReferenceLibrary:
        return cls(msp_to_reference(msp_path, out_dir))

    def __len__(self) -> int:
        return pq.ParquetFile(self._entries_path).metadata.num_rows

    def entries(self, columns: Sequence[str] | None = None):
        """Reference metadata, one row per spectrum."""
        # pyarrow accepts a list, not any sequence.
        wanted = list(columns) if columns is not None else None
        return pq.read_table(self._entries_path, columns=wanted).to_pandas()

    def spectrum_set(self, ref_ids: Sequence[int] | None = None) -> SpectrumSet:
        """Read the library into BLINK's input shape."""
        entries = pq.read_table(
            self._entries_path, columns=["ref_id", "precursor_mz"]
        ).to_pandas()
        if ref_ids is not None:
            entries = entries[entries["ref_id"].isin(list(ref_ids))]
        wanted = entries["ref_id"].to_numpy()

        peaks = pq.read_table(self._peaks_path).to_pandas()
        if ref_ids is not None:
            peaks = peaks[peaks["ref_id"].isin(set(wanted.tolist()))]
        peaks = peaks.sort_values(["ref_id", "mz"], kind="stable")

        sid = peaks["ref_id"].to_numpy()
        mz = peaks["mz"].to_numpy()
        intensity = peaks["intensity"].to_numpy()
        starts = np.searchsorted(sid, wanted, side="left")
        stops = np.searchsorted(sid, wanted, side="right")

        arrays = [
            np.vstack([mz[a:b], intensity[a:b]])
            for a, b in zip(starts, stops, strict=True)
        ]
        return SpectrumSet(
            peaks=arrays,
            precursor_mz=[float(v) for v in entries["precursor_mz"].to_numpy()],
            ids=wanted.astype(np.int64),
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"ReferenceLibrary({str(self.path)!r}, n={len(self)})"
