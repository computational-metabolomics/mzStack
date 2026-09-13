"""A minimal mzML writer, so tests run against real files.

Adapted from ``mzstack-py/tests/mzml_builder.py``, cut down to what the API
tests need. Fixtures build real mzML and convert it with the real ingest
rather than mocking `mzstack`, so the tests exercise the same code path a
user does -- including the unit conversions, which are where the API is most
likely to go wrong.
"""

from __future__ import annotations

import base64
from pathlib import Path

import numpy as np

_HEADER = """<?xml version="1.0" encoding="utf-8"?>
<indexedmzML xmlns="http://psi.hupo.org/ms/mzml">
 <mzML xmlns="http://psi.hupo.org/ms/mzml" version="1.1.0">
  <run id="run1">
   <spectrumList count="{count}">
"""

_FOOTER = """   </spectrumList>
  </run>
 </mzML>
</indexedmzML>
"""


def _array(values, accession: str) -> str:
    payload = base64.b64encode(
        np.asarray(values, dtype=np.float64).tobytes()
    ).decode("ascii")
    return f"""     <binaryDataArray encodedLength="{len(payload)}">
      <cvParam cvRef="MS" accession="MS:1000523" name="64-bit float"/>
      <cvParam cvRef="MS" accession="MS:1000576" name="no compression"/>
      <cvParam cvRef="MS" accession="{accession}" name="array"/>
      <binary>{payload}</binary>
     </binaryDataArray>
"""


def make_spectrum(
    index: int,
    ms_level: int,
    rt: float,
    mz,
    intensity,
    precursor_mz: float | None = None,
    polarity: str = "positive",
) -> str:
    """One ``<spectrum>`` element, retention time in seconds."""
    polarity_cv = (
        '<cvParam cvRef="MS" accession="MS:1000130" name="positive scan"/>'
        if polarity == "positive"
        else '<cvParam cvRef="MS" accession="MS:1000129" name="negative scan"/>'
    )
    precursor = ""
    if precursor_mz is not None:
        precursor = f"""    <precursorList count="1">
     <precursor>
      <isolationWindow>
       <cvParam cvRef="MS" accession="MS:1000827" name="target m/z" \
value="{precursor_mz}"/>
      </isolationWindow>
      <selectedIonList count="1">
       <selectedIon>
        <cvParam cvRef="MS" accession="MS:1000744" name="selected ion m/z" \
value="{precursor_mz}"/>
       </selectedIon>
      </selectedIonList>
      <activation>
       <cvParam cvRef="MS" accession="MS:1000133" \
name="collision-induced dissociation"/>
      </activation>
     </precursor>
    </precursorList>
"""
    arrays = _array(mz, "MS:1000514") + _array(intensity, "MS:1000515")
    return f"""    <spectrum index="{index}" id="scan={index + 1}" \
defaultArrayLength="{len(mz)}">
     <cvParam cvRef="MS" accession="MS:1000511" name="ms level" \
value="{ms_level}"/>
     <cvParam cvRef="MS" accession="MS:1000127" name="centroid spectrum"/>
     {polarity_cv}
     <scanList count="1">
      <scan>
       <cvParam cvRef="MS" accession="MS:1000016" name="scan start time" \
value="{rt}" unitAccession="UO:0000010" unitName="second"/>
      </scan>
     </scanList>
{precursor}     <binaryDataArrayList count="2">
{arrays}     </binaryDataArrayList>
    </spectrum>
"""


def write_mzml(path: Path, spectra: list[str]) -> Path:
    path.write_text(
        _HEADER.format(count=len(spectra)) + "".join(spectra) + _FOOTER,
        encoding="utf-8",
    )
    return path


#: (ms_level, rt_seconds, mz, intensity). Two MS1 scans bracketing two MS2
#: scans, with 226.18 present in exactly one MS1 -- enough to tell a working
#: m/z filter from one that matches everything.
SPECTRA = [
    (1, 60.0, [100.0, 200.0, 300.0], [10.0, 40.0, 50.0]),
    (2, 66.0, [50.0, 75.0], [30.0, 70.0]),
    (1, 72.0, [100.0, 226.18, 300.5], [20.0, 30.0, 50.0]),
    (2, 78.0, [60.0, 90.0, 226.18], [10.0, 20.0, 70.0]),
]


def write_simple(path: Path) -> Path:
    """The four-spectrum file the fixtures use."""
    spectra = [
        make_spectrum(
            index=i,
            ms_level=level,
            rt=rt,
            mz=mz,
            intensity=intensity,
            precursor_mz=195.0877 if level == 2 else None,
        )
        for i, (level, rt, mz, intensity) in enumerate(SPECTRA)
    ]
    return write_mzml(path, spectra)
