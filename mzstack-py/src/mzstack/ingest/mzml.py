"""Streaming mzML reader, and conversion to a native run.

``lxml.etree.iterparse`` driven: each ``<spectrum>`` is handed over as it
closes and then cleared along with its consumed siblings, so peak memory
tracks the largest single spectrum rather than the file.

Retention time is read with its unit accession (``MS:1000595`` /
``UO:0000010`` for seconds) rather than inferred from its magnitude, and is
always emitted in seconds.
"""

from __future__ import annotations

import base64
import gzip
import zlib
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from ..format import layout
from .native import NativeWriter

__all__ = ["iter_spectra", "read_spectra", "mzml_to_mzstack", "MZML_EXTENSIONS"]

_MZML_NS = "http://psi.hupo.org/ms/mzml"

#: File extensions this reader accepts, case-insensitively.
MZML_EXTENSIONS = (".mzml", ".mzml.gz", ".mzml.gzip")


def _q(tag: str) -> str:
    return f"{{{_MZML_NS}}}{tag}"


# -----------------------------------------------------------------------------
# Controlled vocabulary
# -----------------------------------------------------------------------------

_ARRAY_ACC = {
    "MS:1000514": "mz",
    "MS:1000515": "intensity",
}
_COMPRESS_ACC = {
    "MS:1000576": False,  # no compression
    "MS:1000574": True,  # zlib
    "MS:1000284": True,  # zlib (older accession)
}
_DTYPE_ACC = {
    "MS:1000521": np.dtype("float32"),
    "MS:1000523": np.dtype("float64"),
    "MS:1000519": np.dtype("int32"),
    "MS:1000522": np.dtype("uint32"),
}
#: Units that mean the scan start time is in seconds.
_RT_SECONDS_ACC = frozenset({"MS:1000595", "UO:0000010"})
_RT_MINUTES_ACC = frozenset({"UO:0000031"})

_DISSOCIATION_ACC = {
    "MS:1000133": "CID",
    "MS:1000422": "HCD",
    "MS:1000598": "ETD",
    "MS:1000599": "PQD",
}


def _decode_binary(text: str, compressed: bool, dtype: np.dtype) -> np.ndarray:
    raw = base64.b64decode(text)
    if compressed:
        raw = zlib.decompress(raw)
    return np.frombuffer(raw, dtype=dtype).copy()


def _float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _acquisition_num(spectrum_id: str | None) -> int | None:
    """The instrument scan number embedded in an mzML spectrum id.

    mzML ids are ``key=value`` pairs, e.g.
    ``controllerType=0 controllerNumber=1 scan=42``.
    """
    if not spectrum_id:
        return None
    for token in spectrum_id.split():
        key, _, value = token.partition("=")
        if key == "scan":
            return _int(value)
    return None


# -----------------------------------------------------------------------------
# Parsing
# -----------------------------------------------------------------------------

def _parse_scan_list(elem) -> dict[str, Any]:
    out: dict[str, Any] = {}
    sl = elem.find(_q("scanList"))
    if sl is None:
        return out
    scan = sl.find(_q("scan"))
    if scan is None:
        return out

    for cv in scan.iterfind(_q("cvParam")):
        acc = cv.get("accession", "")
        if acc == "MS:1000016":  # scan start time
            value = _float(cv.get("value"))
            if value is None:
                continue
            unit = cv.get("unitAccession", "")
            if unit in _RT_SECONDS_ACC:
                out["rtime"] = value
            elif unit in _RT_MINUTES_ACC:
                out["rtime"] = value * 60.0
            else:
                # No unit given; mzML's default for scan start time is minutes.
                out["rtime"] = value * 60.0
        elif acc == "MS:1000927":  # ion injection time
            out["injectionTime"] = _float(cv.get("value"))
        elif acc == "MS:1000512":  # filter string
            out["filterString"] = cv.get("value")

    window = scan.find(_q("scanWindowList"))
    if window is not None:
        sw = window.find(_q("scanWindow"))
        if sw is not None:
            for cv in sw.iterfind(_q("cvParam")):
                acc = cv.get("accession", "")
                if acc == "MS:1000501":
                    out["scanWindowLowerLimit"] = _float(cv.get("value"))
                elif acc == "MS:1000500":
                    out["scanWindowUpperLimit"] = _float(cv.get("value"))
    return out


def _parse_precursor(elem) -> dict[str, Any]:
    out: dict[str, Any] = {}
    pl = elem.find(_q("precursorList"))
    if pl is None:
        return out
    prec = pl.find(_q("precursor"))
    if prec is None:
        return out

    sel_list = prec.find(_q("selectedIonList"))
    if sel_list is not None:
        sel = sel_list.find(_q("selectedIon"))
        if sel is not None:
            for cv in sel.iterfind(_q("cvParam")):
                acc = cv.get("accession", "")
                if acc in ("MS:1000744", "MS:1000040"):
                    out["precursorMz"] = _float(cv.get("value"))
                elif acc == "MS:1000041":
                    out["precursorCharge"] = _int(cv.get("value"))
                elif acc == "MS:1000042":
                    out["precursorIntensity"] = _float(cv.get("value"))

    iso = prec.find(_q("isolationWindow"))
    if iso is not None:
        target = lower = upper = None
        for cv in iso.iterfind(_q("cvParam")):
            acc = cv.get("accession", "")
            if acc == "MS:1000827":
                target = _float(cv.get("value"))
            elif acc == "MS:1000828":
                lower = _float(cv.get("value"))
            elif acc == "MS:1000829":
                upper = _float(cv.get("value"))
        if target is not None:
            out["isolationWindowTargetMz"] = target
            # mzML stores the window as a target plus two offsets.
            if lower is not None:
                out["isolationWindowLowerMz"] = target - lower
            if upper is not None:
                out["isolationWindowUpperMz"] = target + upper

    act = prec.find(_q("activation"))
    if act is not None:
        for cv in act.iterfind(_q("cvParam")):
            acc = cv.get("accession", "")
            if acc == "MS:1000045":
                out["collisionEnergy"] = _float(cv.get("value"))
            elif acc in _DISSOCIATION_ACC and "activationType" not in out:
                out["activationType"] = _DISSOCIATION_ACC[acc]

    ref = prec.get("spectrumRef")
    if ref:
        out["_precursor_ref"] = ref
    return out


def _parse_arrays(elem) -> dict[str, np.ndarray]:
    arrays: dict[str, np.ndarray] = {}
    bdal = elem.find(_q("binaryDataArrayList"))
    if bdal is None:
        return arrays

    for bda in bdal.iterfind(_q("binaryDataArray")):
        kind: str | None = None
        compressed = False
        dtype = np.dtype("float64")
        for cv in bda.iterfind(_q("cvParam")):
            acc = cv.get("accession", "")
            if acc in _ARRAY_ACC:
                kind = _ARRAY_ACC[acc]
            elif acc in _COMPRESS_ACC:
                compressed = _COMPRESS_ACC[acc]
            elif acc in _DTYPE_ACC:
                dtype = _DTYPE_ACC[acc]
        if kind is None:
            # Some other array (ion mobility, wavelength, ...). Not signal
            # this reader carries.
            continue

        binary = bda.find(_q("binary"))
        if binary is None or not binary.text:
            arrays[kind] = np.empty(0, dtype=np.float64)
            continue
        try:
            arrays[kind] = _decode_binary(
                binary.text.strip(), compressed, dtype
            ).astype(np.float64, copy=False)
        except (ValueError, zlib.error, base64.binascii.Error):
            arrays[kind] = np.empty(0, dtype=np.float64)
    return arrays


def _parse_spectrum(elem) -> dict[str, Any]:
    """One ``<spectrum>`` element as a canonical record plus its arrays."""
    rec: dict[str, Any] = {
        "spectrumId": elem.get("id"),
        "scanIndex": _int(elem.get("index")),
        "msLevel": 1,
    }
    rec["acquisitionNum"] = _acquisition_num(rec["spectrumId"])

    is_spectrum = True
    for cv in elem.iterfind(_q("cvParam")):
        acc = cv.get("accession", "")
        if acc == "MS:1000511":
            rec["msLevel"] = _int(cv.get("value")) or 1
        elif acc == "MS:1000130":
            rec["polarity"] = 1
        elif acc == "MS:1000129":
            rec["polarity"] = 0
        elif acc == "MS:1000127":
            rec["centroided"] = True
        elif acc == "MS:1000128":
            rec["centroided"] = False
        elif acc == "MS:1000285":
            rec["totIonCurrent"] = _float(cv.get("value"))
        elif acc == "MS:1000504":
            rec["basePeakMz"] = _float(cv.get("value"))
        elif acc == "MS:1000505":
            rec["basePeakIntensity"] = _float(cv.get("value"))
        elif acc == "MS:1000528":
            rec["lowMz"] = _float(cv.get("value"))
        elif acc == "MS:1000527":
            rec["highMz"] = _float(cv.get("value"))
        elif acc == "MS:1000294":
            # A UV/Vis or other non-mass spectrum: no m/z axis to store.
            is_spectrum = False

    rec["_is_spectrum"] = is_spectrum
    rec.update(_parse_scan_list(elem))
    rec.update(_parse_precursor(elem))

    arrays = _parse_arrays(elem)
    rec["mz"] = arrays.get("mz", np.empty(0, dtype=np.float64))
    rec["intensity"] = arrays.get("intensity", np.empty(0, dtype=np.float64))
    return rec


def iter_spectra(path: str | Path) -> Iterator[dict[str, Any]]:
    """Stream spectra from an mzML file, plain or gzip-compressed.

    Each record uses canonical variable names, ``rtime`` in seconds,
    ``polarity`` as 1/0, and float64 ``mz`` / ``intensity`` arrays.
    """
    from lxml import etree

    p = str(path)
    opener = gzip.open if p.lower().endswith((".gz", ".gzip")) else open
    with opener(p, "rb") as fh:
        context = etree.iterparse(fh, events=("end",), tag=_q("spectrum"), recover=True)
        for _, elem in context:
            yield _parse_spectrum(elem)
            # Without this the parser retains the whole document.
            elem.clear()
            while elem.getprevious() is not None:
                del elem.getparent()[0]


def read_spectra(path: str | Path) -> Iterator[dict[str, Any]]:
    """`iter_spectra`, with non-spectra dropped and summaries filled in.

    TIC, base peak, m/z bounds and peak count are computed from the arrays only
    where the file stated none, so its own header values are preserved.
    """
    for rec in iter_spectra(path):
        if not rec.pop("_is_spectrum", True):
            continue

        mz = rec["mz"]
        intensity = rec["intensity"]
        rec["peaksCount"] = int(mz.size)

        if mz.size:
            if rec.get("totIonCurrent") is None:
                rec["totIonCurrent"] = float(intensity.sum())
            if rec.get("basePeakMz") is None and intensity.size:
                top = int(intensity.argmax())
                rec["basePeakMz"] = float(mz[top])
                rec["basePeakIntensity"] = float(intensity[top])
            if rec.get("lowMz") is None:
                rec["lowMz"] = float(mz.min())
            if rec.get("highMz") is None:
                rec["highMz"] = float(mz.max())
        elif rec.get("totIonCurrent") is None:
            rec["totIonCurrent"] = 0.0

        yield rec


# -----------------------------------------------------------------------------
# Conversion
# -----------------------------------------------------------------------------

def mzml_to_mzstack(
    files: str | Path | Iterable[str | Path],
    path: str | Path,
    partitioning: Sequence[str] = (),
    compression: str = layout.DEFAULT_COMPRESSION,
    row_group_size: int = layout.DEFAULT_ROW_GROUP_SIZE,
    chunk_size: int = 2000,
    verbose: bool = False,
) -> Path:
    """Convert mzML files into a native mzStack dataset.

    Args:
        files: one path or an iterable of them.
        path: the dataset directory to create.
        partitioning: Hive partition columns, e.g. ``("dataOrigin",)``.
        compression: Parquet codec.
        row_group_size: spectra per row group.
        chunk_size: spectra buffered before a part file is written.
        verbose: report progress per file.

    Returns:
        The dataset path.
    """
    if isinstance(files, str | Path):
        files = [files]
    paths = [Path(f) for f in files]
    if not paths:
        raise ValueError("No input files given.")

    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        raise FileNotFoundError("No such file(s): " + ", ".join(missing))

    dataset = Path(path)

    with NativeWriter(
        dataset,
        partitioning=partitioning,
        compression=compression,
        row_group_size=row_group_size,
        chunk_size=chunk_size,
    ) as writer:
        for source in paths:
            origin = str(source.resolve())
            # A `spectrumRef` always points at an earlier spectrum of the
            # same file, so the map can be built as we go.
            id_by_spectrum_id: dict[str, int] = {}
            n_before = writer.n_written

            for rec in read_spectra(source):
                # `dataStorage` is not stored: the read view supplies it as
                # the dataset path.
                rec["dataOrigin"] = origin

                ref = rec.pop("_precursor_ref", None)
                if ref is not None:
                    resolved = id_by_spectrum_id.get(ref)
                    if resolved is not None:
                        rec["precScanNum"] = resolved

                spectrum_id = rec.get("spectrumId")
                sid = writer.add(rec)
                if spectrum_id is not None:
                    id_by_spectrum_id[spectrum_id] = sid

            if verbose:
                print(f"  {source.name}: {writer.n_written - n_before} spectra")

        total = writer.n_written

    if verbose:
        print(f"{dataset}: {total} spectra")
    return dataset
