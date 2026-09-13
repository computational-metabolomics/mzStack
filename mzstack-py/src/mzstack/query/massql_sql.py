"""Compiling a MassQL query to a single DuckDB statement.

Pushes peak-level conditions into the scan instead of materialising frames for
every spectrum the prefilter admits. A query that cannot be compiled raises
`NotTranslatable` and is never approximated.

Scan-level questions are answered from metadata: `scaninfo` is per-spectrum
metadata plus one `sum(intensity)` per scan, `scannum` and `scanmz` metadata
alone. On a profile dataset such a query takes well under a second compiled,
against around 40 seconds through the engine path, which builds one row per
peak for the whole selection.

Anything whose semantics are not reproduced exactly here -- `scansum`, bare
data queries, intensity qualifiers, `X` variables -- is declined, and
`run_query` falls back to the engine. Declining costs one parse.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .. import duck
from ..errors import NotTranslatable
from ..format import layout
from ..format.columnmap import quote_ident
from ..predicates import (
    pred_and,
    pred_in,
    pred_or,
    sql_num,
)
from ..signal import point_source
from ._cte import metadata_cte, ms1_link_sql

if TYPE_CHECKING:  # pragma: no cover
    import pandas as pd

    from ..store import MzStack

__all__ = ["run_compiled", "TRANSLATABLE_CONDITIONS", "TRANSLATABLE_FUNCTIONS"]

#: Conditions the compiler is intended to cover. The differential test
#: enumerates this list.
TRANSLATABLE_CONDITIONS = (
    "rtmincondition",
    "rtmaxcondition",
    "polaritycondition",
    "scanmincondition",
    "scanmaxcondition",
    "chargecondition",
    "ms2precursorcondition",
    "ms2productcondition",
    "ms2neutrallosscondition",
    "ms1mzcondition",
)

#: Output functions the compiler is intended to cover.
TRANSLATABLE_FUNCTIONS = (
    None,
    "functionscaninfo",
    "functionscannum",
    "functionscanmz",
    "functionscansum",
    "functionscanmaxint",
)

#: Of those, the ones actually implemented. The rest fall back.
_COMPILED_FUNCTIONS = ("functionscaninfo", "functionscannum", "functionscanmz")

#: Conditions answerable from per-spectrum metadata alone, as
#: ``(column, mode, gates_ms1)``.
#:
#: ``gates_ms1`` says whether massql applies the condition to the MS1 frame
#: *directly*, which is what decides `i_norm_ms1`. The engine filters MS1 rows
#: by retention time, polarity and scan number the same way it filters MS2
#: rows; for a precursor or charge condition it instead keeps whatever MS1
#: scans the surviving MS2 rows point at, which never excludes a surviving
#: row's own MS1 scan. Those conditions therefore do not gate it here.
#:
#: The comparisons match the engine exactly, and it is not consistent: the
#: retention-time bounds are strict while the scan bounds are inclusive
#: (`msql_engine._executeconditions_query`).
_METADATA_CONDITIONS = {
    "rtmincondition": ("rt", "gt", True),
    "rtmaxcondition": ("rt", "lt", True),
    "scanmincondition": ("scan", "ge", True),
    "scanmaxcondition": ("scan", "le", True),
    "polaritycondition": ("polarity", "in", True),
    "chargecondition": ("charge", "in", False),
    "ms2precursorcondition": ("precmz", "window", False),
}

#: Conditions needing a look at the signal: "does this spectrum hold a peak
#: in this window?". Each names the level whose peaks it examines.
_PEAK_CONDITIONS = {
    "ms2productcondition": 2,
    "ms2neutrallosscondition": 2,
    "ms1mzcondition": 1,
}

#: Qualifiers whose meaning is reproduced here. Anything else -- intensity
#: thresholds, cardinality, matching across conditions -- is declined.
_KNOWN_QUALIFIERS = {"qualifiermztolerance", "qualifierppmtolerance"}

#: MassQL's window when a condition names no tolerance
#: (`msql_engine_filters._get_mz_tolerance`).
_DEFAULT_TOLERANCE_DA = 0.1


def _decline(reason: str) -> NotTranslatable:
    return NotTranslatable(f"{reason}; using the engine.")


def _numbers(condition: dict[str, Any]) -> list[float]:
    """The condition's values, or a decline if any is not a number.

    A non-numeric value is an `X` variable, which makes massql enumerate
    candidate masses from the data -- not something to reproduce blind.
    """
    values = condition.get("value") or []
    out = []
    for value in values:
        try:
            out.append(float(value))
        except (TypeError, ValueError) as exc:
            raise _decline(f"{condition.get('type')} takes a variable") from exc
    if not out:
        raise _decline(f"{condition.get('type')} has no value")
    return out


def _tolerance(mz: float, qualifiers: dict | None) -> float:
    """The condition's tolerance, as an absolute m/z delta.

    Reproduces `msql_engine_filters._get_mz_tolerance` exactly, including its
    precedence: ppm wins outright when both are given. This is the final
    answer and has to match. (`pushdown` takes the widest instead, since it
    only has to produce a superset.)
    """
    for key, qualifier in (qualifiers or {}).items():
        if key == "type":
            continue
        if not isinstance(qualifier, dict) or str(
            qualifier.get("name", key)
        ).lower() not in _KNOWN_QUALIFIERS:
            raise _decline(f"unsupported qualifier {key}")

    for name, to_delta in (
        ("qualifierppmtolerance", lambda v: abs(v * mz / 1e6)),
        ("qualifiermztolerance", lambda v: v),
    ):
        if name in (qualifiers or {}):
            try:
                return to_delta(float(qualifiers[name]["value"]))
            except (TypeError, ValueError, KeyError) as exc:
                raise _decline(f"qualifier {name} has no numeric value") from exc
    return _DEFAULT_TOLERANCE_DA


def _metadata_predicate(condition: dict[str, Any]) -> str:
    """One metadata condition as SQL over the `meta` columns."""
    column, mode, _ = _METADATA_CONDITIONS[condition["type"]]
    quoted = quote_ident(column)

    if condition["type"] == "polaritycondition":
        # Spelled as a keyword rather than a number. `meta` already encodes
        # polarity the way MassQL does, so the parsed keyword maps straight on.
        from .pushdown import _parse_polarity

        polarities = [_parse_polarity(v) for v in condition.get("value") or []]
        if not polarities or any(p is None for p in polarities):
            raise _decline("unrecognised polarity")
        return pred_in(column, polarities)

    values = _numbers(condition)

    if mode == "gt":
        return f"{quoted} > {sql_num(values[0])}"
    if mode == "lt":
        return f"{quoted} < {sql_num(values[0])}"
    if mode == "ge":
        return f"{quoted} >= {sql_num(int(values[0]))}"
    if mode == "le":
        return f"{quoted} <= {sql_num(int(values[0]))}"
    if mode == "in":
        # Alternatives inside one condition are OR-ed, as MassQL defines them.
        return pred_in(column, values)

    # A precursor window per alternative, OR-ed together. Strict at both ends,
    # as the engine writes it.
    qualifiers = condition.get("qualifiers")
    windows = []
    for mz in values:
        delta = _tolerance(mz, qualifiers)
        windows.append(
            f"({quoted} > {sql_num(mz - delta)} AND {quoted} < {sql_num(mz + delta)})"
        )
    return pred_or(*windows)


def _peak_predicate(store: MzStack, condition: dict[str, Any]) -> str:
    """One peak condition as an EXISTS over the signal.

    "Holds a peak in any of these windows" -- the same question
    `signal.ids_containing_mz` answers, phrased so DuckDB can prune the scan
    rather than hand every point to pandas.
    """
    src = point_source(store, prefer=layout.PROJECTION_MZSORTED)
    sid = quote_ident("spectrum_id_")
    qualifiers = condition.get("qualifiers")
    values = _numbers(condition)

    # Strict at both ends, as the engine writes it.
    windows = []
    for value in values:
        delta = _tolerance(value, qualifiers)
        if condition["type"] == "ms2neutrallosscondition":
            # A neutral loss is a window relative to the precursor, so the
            # bounds move per row and are written against `w.precmz`.
            lo = f"w.precmz - {sql_num(value + delta)}"
            hi = f"w.precmz - {sql_num(value - delta)}"
        else:
            lo, hi = sql_num(value - delta), sql_num(value + delta)
        windows.append(f'("mz" > {lo} AND "mz" < {hi})')

    scan_ref = "w.scan" if _PEAK_CONDITIONS[condition["type"]] == 2 else "w.ms1scan"
    return f"""EXISTS (
        SELECT 1 FROM {src.sql}
        WHERE {sid} = {scan_ref} AND ({pred_or(*windows)})
    )"""


def _check(parsed: dict[str, Any]) -> tuple[str, int]:
    """The function and MS level to compile, or a decline."""
    querytype = parsed.get("querytype") or {}
    function = querytype.get("function")
    if function not in _COMPILED_FUNCTIONS:
        raise _decline(f"{function or 'a bare data query'} is not compiled")

    datatype = str(querytype.get("datatype", "")).lower()
    if "ms2data" in datatype:
        level = 2
    elif "ms1data" in datatype:
        level = 1
    else:
        raise _decline(f"unknown datatype {datatype!r}")

    if function == "functionscanmz" and level != 2:
        raise _decline("scanmz reads precursors, which only MS2 carries")

    for condition in parsed.get("conditions") or []:
        kind = condition.get("type")
        if condition.get("conditiontype") != "where":
            raise _decline(f"{kind} is a {condition.get('conditiontype')} condition")
        if kind not in _METADATA_CONDITIONS and kind not in _PEAK_CONDITIONS:
            raise _decline(f"{kind} is not compiled")
        if kind == "ms1mzcondition" and level != 1:
            # massql narrows the MS2 rows via their linked MS1 scan; the
            # interaction with `ms1scan` is not reproduced here.
            raise _decline("ms1mz against MS2 output is not compiled")
    return function, level


def run_compiled(store: MzStack, query: str, **kwargs: Any) -> pd.DataFrame:
    """Answer ``query`` with one DuckDB statement.

    Raises:
        NotTranslatable: if the query is outside the compiled subset.
    """
    if kwargs:
        raise _decline(f"unsupported options {sorted(kwargs)}")

    try:
        from massql import msql_parser
    except ImportError as exc:  # pragma: no cover - exercised by the extra
        raise _decline("massql is not installed") from exc

    try:
        parsed = msql_parser.parse_msql(query)
    except Exception as exc:  # noqa: BLE001 - let the engine report it
        raise _decline("the query does not parse") from exc

    function, level = _check(parsed)

    meta_parts, ms1_parts, peak_parts = [], [], []
    for condition in parsed.get("conditions") or []:
        if condition["type"] in _METADATA_CONDITIONS:
            predicate = _metadata_predicate(condition)
            meta_parts.append(predicate)
            if _METADATA_CONDITIONS[condition["type"]][2]:
                ms1_parts.append(predicate)
        else:
            peak_parts.append(_peak_predicate(store, condition))

    sql = _statement(
        store,
        function,
        level,
        pred_and(*meta_parts),
        pred_and(*ms1_parts),
        peak_parts,
    )
    df = duck.connection().execute(sql).to_arrow_table().to_pandas()
    return _match_engine_dtypes(df, function, level)


def _statement(
    store: MzStack,
    function: str,
    level: int,
    meta_where: str | None,
    ms1_where: str | None,
    peak_parts: list[str],
) -> str:
    """The single statement that answers the query."""
    sid = quote_ident("spectrum_id_")
    conds = f"AND {meta_where}" if meta_where else ""
    peaks = "".join(f"\n              AND {part}" for part in peak_parts)

    # `meta` and `linked` stay over every MS level: `ms1scan`'s running
    # "previous MS1 scan" window needs the MS1 rows to be there to find them.
    header = f"""
        WITH meta AS ({metadata_cte(store)}),
        linked AS ({ms1_link_sql()}),
        wanted AS (
            SELECT * FROM linked AS w
            WHERE ms_level = {level} {conds}{peaks}
        )
    """

    if function == "functionscannum":
        return f"{header} SELECT CAST(scan AS BIGINT) AS scan FROM wanted ORDER BY scan"

    if function == "functionscanmz":
        # Ordered, unlike the engine, which builds this from a Python set and
        # so returns an arbitrary row order.
        return f"""
            {header}
            SELECT DISTINCT precmz FROM wanted
            WHERE precmz IS NOT NULL ORDER BY precmz
        """

    # scaninfo. `i` is the only column needing the signal, and it is an
    # aggregate: DuckDB sums each spectrum instead of handing every point to
    # pandas. It is not the stored `totIonCurrent`, which comes from the file
    # header and disagrees with the arrays.
    src = point_source(store, prefer=layout.PROJECTION_SCANSORTED)
    totals = f"""
        totals AS (
            SELECT {sid} AS scan, sum("intensity") AS i
            FROM {src.sql}
            WHERE {sid} IN (SELECT scan FROM wanted)
            GROUP BY {sid}
        )
    """

    if level == 1:
        return f"""
            {header}, {totals}
            SELECT
                CAST(w.scan AS BIGINT) AS scan,
                w.rt,
                CAST(1 AS BIGINT) AS mslevel,
                t.i,
                CAST(1.0 AS DOUBLE) AS i_norm
            FROM wanted AS w JOIN totals AS t USING (scan)
            ORDER BY w.scan
        """

    # `i_norm_ms1` is massql's per-scan max of the MS1 frame's `i_norm`, which
    # is 1.0 by construction -- but NULL where the linked MS1 scan was itself
    # excluded by a condition, which `ms1_ok` reproduces.
    return f"""
        {header}, {totals},
        ms1_ok AS (
            SELECT scan AS ms1scan FROM linked AS w
            WHERE ms_level = 1 {f"AND {ms1_where}" if ms1_where else ""}
        )
        SELECT
            CAST(w.scan AS BIGINT) AS scan,
            w.precmz,
            w.ms1scan,
            w.rt,
            w.charge,
            t.i,
            CAST(1.0 AS DOUBLE) AS i_norm,
            CAST(2 AS BIGINT) AS mslevel,
            CASE WHEN m.ms1scan IS NULL THEN NULL ELSE CAST(1.0 AS DOUBLE) END
                AS i_norm_ms1
        FROM wanted AS w
        JOIN totals AS t USING (scan)
        LEFT JOIN ms1_ok AS m USING (ms1scan)
        ORDER BY w.scan
    """


def _match_engine_dtypes(df: pd.DataFrame, function: str, level: int) -> pd.DataFrame:
    """Land on the same dtypes the engine produces.

    The engine's frames come from pandas groupbys, so its integer columns are
    whatever pandas settled on. Matching them keeps the two paths
    interchangeable for callers that check dtypes, and for the differential
    test, which compares frames rather than values.
    """
    if function == "functionscanmz":
        return df
    if df.empty:
        # massql returns a bare empty frame rather than an empty typed one.
        import pandas as pd

        return pd.DataFrame()
    if function == "functionscaninfo" and level == 2:
        for column in ("ms1scan", "charge"):
            # Null only where the store's own filter removed the MS1 spectra,
            # in which case pandas holds the engine's column as float too.
            if column in df and df[column].notna().all():
                df[column] = df[column].astype("int32")
    return df
