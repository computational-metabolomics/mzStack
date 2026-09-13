"""Translating between mzPeak column names and canonical spectra variables.

Both dataset kinds keep mzPeak's names on disk: an mzPeak-backed dataset in its
derived index, and a natively converted dataset through
`encode_spectra_record`. One SQL view serves both, and either exports back to
an archive mechanically.

Every unit and encoding difference lives here. Chiefly: mzPeak ``time`` is in
**minutes** and ``rtime`` is in **seconds**.

MassQL uses a third convention -- minutes again, and polarity 1/2/0 rather
than 1/0. That translation is applied on top of this view, in
:mod:`mzstack.query.massql`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "Mapping",
    "MZPEAK_TO_SPECTRA",
    "SPECTRA_TO_MZPEAK",
    "MZPEAK_CONSUMED",
    "INDEX_PASSTHROUGH",
    "RESERVED_COLUMNS",
    "encode_spectra_record",
    "encode_var_names",
    "spectra_variables",
    "view_select_sql",
    "quote_ident",
    "quote_string",
]


def quote_ident(name: str) -> str:
    """Quote a SQL identifier."""
    return '"' + name.replace('"', '""') + '"'


def quote_string(value: str | None) -> str:
    """Quote a SQL string literal, or render ``NULL``."""
    if value is None:
        return "NULL"
    return "'" + value.replace("'", "''") + "'"


@dataclass(frozen=True)
class Mapping:
    """One canonical variable and the SQL that produces it.

    Attributes:
        spectra: the name the caller sees.
        expr: SQL over the index columns.
        requires: index columns ``expr`` needs; if any is absent the view
            emits a typed ``NULL`` instead.
        type: SQL type of that ``NULL``.
    """

    spectra: str
    expr: str
    requires: tuple[str, ...]
    type: str


def _m(spectra: str, expr: str, requires: str | Sequence[str], type: str) -> Mapping:
    if isinstance(requires, str):
        requires = (requires,)
    return Mapping(spectra, expr, tuple(requires), type)


#: Canonical variable -> the SQL producing it. A variable may have several
#: candidates; the first whose inputs are all present wins, and one with no
#: usable candidate still appears, as a typed ``NULL``.
MZPEAK_TO_SPECTRA: tuple[Mapping, ...] = (
    _m("msLevel", 'CAST("ms_level" AS INTEGER)', "ms_level", "INTEGER"),
    # Minutes -> seconds.
    _m("rtime", '"time" * 60.0', "time", "DOUBLE"),
    # mzPeak: 1 positive, -1 negative. Spectra follows mzR: 1 positive,
    # 0 negative, NULL unknown.
    _m(
        "polarity",
        'CASE "scan_polarity" WHEN 1 THEN 1 WHEN -1 THEN 0 END',
        "scan_polarity",
        "INTEGER",
    ),
    # A CURIE, not a flag: MS:1000127 centroid, MS:1000128 profile.
    _m(
        "centroided",
        'CASE "spectrum_representation" '
        "WHEN 'MS:1000127' THEN TRUE "
        "WHEN 'MS:1000128' THEN FALSE END",
        "spectrum_representation",
        "BOOLEAN",
    ),
    _m("spectrumId", 'CAST("id" AS VARCHAR)', "id", "VARCHAR"),
    # mzPeak has no column for the instrument scan number; a native dataset
    # keeps it in `acquisition_num_`, and an mzPeak archive falls back to its
    # 0-based `spectrum_index`.
    _m(
        "acquisitionNum",
        'CAST("acquisition_num_" AS INTEGER)',
        "acquisition_num_",
        "INTEGER",
    ),
    _m(
        "acquisitionNum",
        'CAST("spectrum_index" AS INTEGER)',
        "spectrum_index",
        "INTEGER",
    ),
    # `spectrum_index` is the dataset's unique 0-based key, so it answers
    # `scanIndex` only for a run that is a single source file -- which an
    # mzPeak archive always is. A native dataset spanning several files keeps
    # each spectrum's position within its own file in `scan_index`.
    _m("scanIndex", 'CAST("scan_index" AS INTEGER)', "scan_index", "INTEGER"),
    _m("scanIndex", 'CAST("spectrum_index" AS INTEGER)', "spectrum_index", "INTEGER"),
    _m(
        "precScanNum",
        'CAST("precursor_index" AS INTEGER)',
        "precursor_index",
        "INTEGER",
    ),
    _m("precursorMz", '"selected_ion_mz"', "selected_ion_mz", "DOUBLE"),
    _m("precursorCharge", 'CAST("charge_state" AS INTEGER)', "charge_state", "INTEGER"),
    # mzPeak's selected-ion intensity is `peak_intensity` (MS:1000042).
    _m("precursorIntensity", '"peak_intensity"', "peak_intensity", "DOUBLE"),
    _m("collisionEnergy", '"collision_energy"', "collision_energy", "DOUBLE"),
    # mzPeak stores the window as target plus offsets; these expose absolute
    # bounds.
    _m(
        "isolationWindowTargetMz",
        '"isolation_window_target"',
        "isolation_window_target",
        "DOUBLE",
    ),
    _m(
        "isolationWindowLowerMz",
        '"isolation_window_target" - "isolation_window_lower_offset"',
        ("isolation_window_target", "isolation_window_lower_offset"),
        "DOUBLE",
    ),
    _m(
        "isolationWindowUpperMz",
        '"isolation_window_target" + "isolation_window_upper_offset"',
        ("isolation_window_target", "isolation_window_upper_offset"),
        "DOUBLE",
    ),
    # Not core Spectra variables. Other backends surface them, and downstream
    # code including MassQL reads them.
    _m("totIonCurrent", '"total_ion_current"', "total_ion_current", "DOUBLE"),
    _m("basePeakMz", '"base_peak_mz"', "base_peak_mz", "DOUBLE"),
    _m("basePeakIntensity", '"base_peak_intensity"', "base_peak_intensity", "DOUBLE"),
    _m("lowMz", '"lowest_observed_mz"', "lowest_observed_mz", "DOUBLE"),
    _m("highMz", '"highest_observed_mz"', "highest_observed_mz", "DOUBLE"),
    # Whichever representation this spectrum actually stores. An archive
    # holding only profile data has no `number_of_peaks` column at all, so the
    # three candidates are tried in order and the first whose inputs exist
    # wins.
    _m(
        "peaksCount",
        'CAST(COALESCE("number_of_peaks", "number_of_data_points") AS INTEGER)',
        ("number_of_peaks", "number_of_data_points"),
        "INTEGER",
    ),
    _m(
        "peaksCount", 'CAST("number_of_peaks" AS INTEGER)', "number_of_peaks", "INTEGER"
    ),
    _m(
        "peaksCount",
        'CAST("number_of_data_points" AS INTEGER)',
        "number_of_data_points",
        "INTEGER",
    ),
    _m("injectionTime", '"ion_injection_time"', "ion_injection_time", "DOUBLE"),
    _m("filterString", 'CAST("filter_string" AS VARCHAR)', "filter_string", "VARCHAR"),
    _m(
        "scanWindowLowerLimit",
        '"scan_window_lower_limit"',
        "scan_window_lower_limit",
        "DOUBLE",
    ),
    _m(
        "scanWindowUpperLimit",
        '"scan_window_upper_limit"',
        "scan_window_upper_limit",
        "DOUBLE",
    ),
    _m(
        "instrumentConfigurationId",
        'CAST("instrument_configuration_id" AS INTEGER)',
        "instrument_configuration_id",
        "INTEGER",
    ),
    _m("spectrumType", 'CAST("spectrum_type" AS VARCHAR)', "spectrum_type", "VARCHAR"),
)

#: Columns carried through untouched. ``spectrum_id_`` is the key rows are
#: addressed by; the ``n_*`` counts say how many scans, precursors or selected
#: ions the spectrum had, the flattened index showing only the first of each.
INDEX_PASSTHROUGH: tuple[str, ...] = (
    "spectrum_id_",
    "run_id",
    "spectrum_index",
    "n_scans",
    "n_selected_ions",
    "n_precursors",
)

#: Columns mzstack reserves; mzPeak has no equivalent for them. The row key,
#: the instrument scan number, the per-source-file position, and the ``n_*``
#: counts.
RESERVED_COLUMNS: tuple[str, ...] = (
    "spectrum_id_",
    "acquisition_num_",
    "scan_index",
    "n_scans",
    "n_selected_ions",
    "n_precursors",
)


@dataclass(frozen=True)
class Encoding:
    """One canonical variable and the on-disk column it is written as.

    Attributes:
        spectra: the incoming canonical variable.
        mzpeak: the on-disk column name.
        encode: value transform, or ``None`` for a pure rename.
    """

    spectra: str
    mzpeak: str
    encode: Callable[[Any], Any] | None = None


def _curie_representation(centroided: Any) -> str | None:
    """``centroided`` as the CURIE mzPeak stores it."""
    if centroided is None:
        return None
    return "MS:1000127" if centroided else "MS:1000128"


def _scan_polarity(polarity: Any) -> int | None:
    """Polarity 1 positive / 0 negative as mzPeak's 1 / -1."""
    if polarity == 1:
        return 1
    if polarity == 0:
        return -1
    return None


#: Canonical variable -> on-disk column, the inverse of the read map for the
#: columns a native dataset can carry. The isolation window needs two inputs at
#: once and is handled in `encode_spectra_record`.
SPECTRA_TO_MZPEAK: tuple[Encoding, ...] = (
    Encoding("msLevel", "ms_level"),
    Encoding("rtime", "time", lambda x: None if x is None else x / 60.0),
    Encoding("polarity", "scan_polarity", _scan_polarity),
    Encoding("centroided", "spectrum_representation", _curie_representation),
    Encoding("spectrumId", "id"),
    Encoding("scanIndex", "scan_index"),
    Encoding("acquisitionNum", "acquisition_num_"),
    Encoding("precScanNum", "precursor_index"),
    Encoding("precursorMz", "selected_ion_mz"),
    Encoding("precursorCharge", "charge_state"),
    Encoding("precursorIntensity", "peak_intensity"),
    Encoding("collisionEnergy", "collision_energy"),
    Encoding("isolationWindowTargetMz", "isolation_window_target"),
    Encoding("totIonCurrent", "total_ion_current"),
    Encoding("basePeakMz", "base_peak_mz"),
    Encoding("basePeakIntensity", "base_peak_intensity"),
    Encoding("lowMz", "lowest_observed_mz"),
    Encoding("highMz", "highest_observed_mz"),
    Encoding("peaksCount", "number_of_data_points"),
    Encoding("injectionTime", "ion_injection_time"),
    Encoding("filterString", "filter_string"),
    Encoding("scanWindowLowerLimit", "scan_window_lower_limit"),
    Encoding("scanWindowUpperLimit", "scan_window_upper_limit"),
    Encoding("instrumentConfigurationId", "instrument_configuration_id"),
    Encoding("spectrumType", "spectrum_type"),
    Encoding("dataOrigin", "data_origin"),
)


def encode_spectra_record(record: dict[str, Any]) -> dict[str, Any]:
    """Encode one canonical spectrum record into mzPeak's on-disk vocabulary.

    ``mz``, ``intensity``, ``spectrum_id_`` and any key the map does not
    mention -- a user-defined spectra variable -- pass through untouched.
    ``dataStorage`` is dropped: the read view supplies it as the dataset path.

    Args:
        record: canonical spectrum metadata, already carrying
            ``spectrum_id_``.

    Returns:
        A new dict keyed by on-disk column names.
    """
    out = dict(record)

    # The isolation window while the target still has its canonical name:
    # canonical carries absolute bounds, mzPeak the target plus offsets.
    target = out.get("isolationWindowTargetMz")
    lower = out.pop("isolationWindowLowerMz", None)
    upper = out.pop("isolationWindowUpperMz", None)
    if target is not None:
        if lower is not None:
            out["isolation_window_lower_offset"] = target - lower
        if upper is not None:
            out["isolation_window_upper_offset"] = upper - target

    # Supplied by the read view.
    out.pop("dataStorage", None)

    for e in SPECTRA_TO_MZPEAK:
        if e.spectra not in out:
            continue
        value = out.pop(e.spectra)
        out[e.mzpeak] = e.encode(value) if e.encode is not None else value

    # mzPeak's `spectrum_index` is the run's unique, monotonic 0-based key. A
    # native dataset is one run, so that is `spectrum_id_ - 1` -- never the
    # per-file `scanIndex`, which restarts for every source file.
    sid = out.get("spectrum_id_")
    if sid is not None:
        out["spectrum_index"] = int(sid) - 1

    return out


def encode_var_names(names: Iterable[str]) -> list[str]:
    """Translate canonical variable names to their on-disk column names.

    For callers passing column names rather than values, such as Hive
    partitioning keys. Unmapped names pass through, so this is idempotent.
    """
    to = {e.spectra: e.mzpeak for e in SPECTRA_TO_MZPEAK}
    return [to.get(n, n) for n in names]


#: On-disk columns consumed by a read expression. The view's generic
#: pass-through skips them, so the view carries ``rtime`` and not also
#: ``time``. ``data_origin`` becomes ``dataOrigin`` and is handled explicitly.
MZPEAK_CONSUMED: frozenset[str] = frozenset(
    [r for m in MZPEAK_TO_SPECTRA for r in m.requires] + ["data_origin"]
)


def _ordered_variables() -> list[str]:
    """Canonical variable names, in declaration order, without duplicates."""
    seen: list[str] = []
    for m in MZPEAK_TO_SPECTRA:
        if m.spectra not in seen:
            seen.append(m.spectra)
    return seen


def _resolve(name: str, available: frozenset[str]) -> Mapping | None:
    """First candidate for ``name`` whose inputs are all present."""
    for m in MZPEAK_TO_SPECTRA:
        if m.spectra == name and available.issuperset(m.requires):
            return m
    return None


def spectra_variables(available: Iterable[str]) -> list[str]:
    """Variables the view exposes, given the columns available on disk.

    A variable whose source is missing is still exposed, as a typed ``NULL``,
    so the variable set does not depend on which optional columns a dataset
    happens to carry. Columns the map neither consumes nor emits are passed
    through under their own names.
    """
    have = list(dict.fromkeys(available))
    emitted = [c for c in INDEX_PASSTHROUGH if c in have]
    emitted.extend(_ordered_variables())
    emitted.extend(("dataOrigin", "dataStorage"))

    seen = set(emitted) | MZPEAK_CONSUMED
    return emitted + [c for c in have if c not in seen]


def view_select_sql(
    available: Iterable[str],
    source: str,
    data_storage: str,
) -> str:
    """SQL for the view that presents the index using canonical names.

    Args:
        available: column names present in the index.
        source: SQL expression naming the index relation.
        data_storage: dataset path, reported as the ``dataStorage`` variable.
    """
    have = list(dict.fromkeys(available))
    present = frozenset(have)
    emitted: list[str] = [c for c in INDEX_PASSTHROUGH if c in present]
    sel: list[str] = [quote_ident(c) for c in emitted]

    for name in _ordered_variables():
        m = _resolve(name, present)
        if m is not None:
            expr = m.expr
        else:
            # Typed, so an absent source is a NULL column not a missing one.
            fallback = next(c for c in MZPEAK_TO_SPECTRA if c.spectra == name)
            expr = f"CAST(NULL AS {fallback.type})"
        sel.append(f"{expr} AS {quote_ident(name)}")
        emitted.append(name)

    # `dataOrigin` identifies the run a spectrum came from and `dataStorage`
    # the dataset holding it -- the same contract as every other backend.
    origin = '"data_origin"' if "data_origin" in present else "CAST(NULL AS VARCHAR)"
    sel.append(f"{origin} AS {quote_ident('dataOrigin')}")
    sel.append(f"{quote_string(data_storage)} AS {quote_ident('dataStorage')}")
    emitted += ["dataOrigin", "dataStorage"]

    # Anything the dataset carries that the map neither consumes as a source
    # nor already emits passes through unchanged: a native dataset's peak
    # columns (`mz`, `intensity`), the reserved `acquisition_num_`, and any
    # user-defined spectra variable.
    seen = set(emitted) | MZPEAK_CONSUMED
    sel.extend(quote_ident(c) for c in have if c not in seen)

    return "SELECT " + ", ".join(sel) + " FROM " + source
