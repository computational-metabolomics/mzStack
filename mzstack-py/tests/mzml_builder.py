"""Builders for synthetic mzML files.

Kept out of ``conftest.py`` so that test modules can import them directly
rather than through a fixture.
"""

from __future__ import annotations

import base64
import zlib
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


def _binary_array(values, accession: str, compress: bool) -> str:
    arr = np.asarray(values, dtype=np.float64)
    raw = arr.tobytes()
    if compress:
        raw = zlib.compress(raw)
    payload = base64.b64encode(raw).decode("ascii")
    compression = (
        '<cvParam cvRef="MS" accession="MS:1000574" name="zlib compression"/>'
        if compress
        else '<cvParam cvRef="MS" accession="MS:1000576" name="no compression"/>'
    )
    return f"""     <binaryDataArray encodedLength="{len(payload)}">
      <cvParam cvRef="MS" accession="MS:1000523" name="64-bit float"/>
      {compression}
      <cvParam cvRef="MS" accession="{accession}" name="array"/>
      <binary>{payload}</binary>
     </binaryDataArray>
"""


def make_spectrum(
    index: int,
    scan: int,
    ms_level: int,
    rt: float,
    mz,
    intensity,
    *,
    rt_unit: str = "second",
    polarity: str = "positive",
    centroided: bool = True,
    precursor_mz: float | None = None,
    precursor_charge: int | None = None,
    precursor_ref: str | None = None,
    collision_energy: float | None = None,
    compress: bool = False,
) -> str:
    """One ``<spectrum>`` element, as a real mzML writer would emit it."""
    unit = (
        ('unitAccession="UO:0000010" unitName="second"')
        if rt_unit == "second"
        else ('unitAccession="UO:0000031" unitName="minute"')
    )
    polarity_cv = (
        '<cvParam cvRef="MS" accession="MS:1000130" name="positive scan"/>'
        if polarity == "positive"
        else '<cvParam cvRef="MS" accession="MS:1000129" name="negative scan"/>'
    )
    representation = (
        '<cvParam cvRef="MS" accession="MS:1000127" name="centroid spectrum"/>'
        if centroided
        else '<cvParam cvRef="MS" accession="MS:1000128" name="profile spectrum"/>'
    )

    precursor_block = ""
    if precursor_mz is not None:
        charge = (
            f'<cvParam cvRef="MS" accession="MS:1000041" name="charge state" '
            f'value="{precursor_charge}"/>'
            if precursor_charge is not None
            else ""
        )
        energy = (
            f'<cvParam cvRef="MS" accession="MS:1000045" name="collision energy" '
            f'value="{collision_energy}"/>'
            if collision_energy is not None
            else ""
        )
        ref = f' spectrumRef="{precursor_ref}"' if precursor_ref else ""
        precursor_block = f"""    <precursorList count="1">
     <precursor{ref}>
      <isolationWindow>
       <cvParam cvRef="MS" accession="MS:1000827" name="target m/z" value="{precursor_mz}"/>
       <cvParam cvRef="MS" accession="MS:1000828" name="lower offset" value="0.5"/>
       <cvParam cvRef="MS" accession="MS:1000829" name="upper offset" value="1.5"/>
      </isolationWindow>
      <selectedIonList count="1">
       <selectedIon>
        <cvParam cvRef="MS" accession="MS:1000744" name="selected ion m/z" \
value="{precursor_mz}"/>
        {charge}
       </selectedIon>
      </selectedIonList>
      <activation>
       <cvParam cvRef="MS" accession="MS:1000133" name="collision-induced dissociation"/>
       {energy}
      </activation>
     </precursor>
    </precursorList>
"""

    arrays = _binary_array(mz, "MS:1000514", compress) + _binary_array(
        intensity, "MS:1000515", compress
    )

    return f"""    <spectrum index="{index}" id="controllerType=0 controllerNumber=1 scan={scan}" \
defaultArrayLength="{len(mz)}">
     <cvParam cvRef="MS" accession="MS:1000511" name="ms level" value="{ms_level}"/>
     {representation}
     {polarity_cv}
     <scanList count="1">
      <scan>
       <cvParam cvRef="MS" accession="MS:1000016" name="scan start time" \
value="{rt}" {unit}/>
       <cvParam cvRef="MS" accession="MS:1000927" name="ion injection time" value="7.5"/>
       <scanWindowList count="1">
        <scanWindow>
         <cvParam cvRef="MS" accession="MS:1000501" name="scan window lower limit" \
value="50.0"/>
         <cvParam cvRef="MS" accession="MS:1000500" name="scan window upper limit" \
value="1000.0"/>
        </scanWindow>
       </scanWindowList>
      </scan>
     </scanList>
{precursor_block}     <binaryDataArrayList count="2">
{arrays}     </binaryDataArrayList>
    </spectrum>
"""


def write_mzml(path: Path, spectra: list[str]) -> Path:
    path.write_text(
        _HEADER.format(count=len(spectra)) + "".join(spectra) + _FOOTER,
        encoding="utf-8",
    )
    return path


#: The spectra `simple_mzml` contains, as (ms_level, rt_seconds, mz, intensity).
SIMPLE_SPECTRA = [
    (1, 60.0, [100.0, 200.0, 300.0], [10.0, 40.0, 50.0]),
    (2, 66.0, [50.0, 75.0], [30.0, 70.0]),
    (1, 72.0, [100.0, 250.0, 300.5], [20.0, 30.0, 50.0]),
    (2, 78.0, [60.0, 90.0, 110.0], [10.0, 20.0, 70.0]),
]
