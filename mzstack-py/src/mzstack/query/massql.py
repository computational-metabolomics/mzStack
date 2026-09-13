"""Building the frames MassQL evaluates against, and running queries.

``msql_engine.process_query`` takes ``ms1_df`` / ``ms2_df`` directly, so
queries run off Parquet without massql opening any file. The frames are long,
one row per peak: ``i, i_norm, i_tic_norm, mz, scan, rt, polarity``, plus
``precmz, ms1scan, charge`` for MS2.

Three values are translated here: ``rt`` is in minutes (the store holds
seconds), ``polarity`` is 1 positive / 2 negative / 0 unknown (the store uses
1/0), and ``i_tic_norm`` divides by the sum of the intensities in the array
rather than by the header's total ion current -- the two differ for centroided
or vendor-supplied values.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from .. import duck
from ..errors import MzStackError
from ..format import layout
from ..format.columnmap import quote_ident
from ..predicates import pred_and, pred_in, pred_range
from ..signal import point_source
from ._cte import metadata_cte, ms1_link_sql

if TYPE_CHECKING:  # pragma: no cover
    import pandas as pd

    from ..store import MzStack

__all__ = ["massql_frames", "run_query", "MS1_COLUMNS", "MS2_COLUMNS"]

#: The frame contracts, as massql's own loaders build them.
MS1_COLUMNS = ("i", "i_norm", "i_tic_norm", "mz", "scan", "rt", "polarity")
MS2_COLUMNS = (*MS1_COLUMNS, "precmz", "ms1scan", "charge")


def _require_massql():
    try:
        from massql import msql_engine, msql_parser
    except ImportError as e:  # pragma: no cover - exercised by the extra
        raise MzStackError(
            "The MassQL query layer needs the `massql` package. "
            "Install it with: pip install 'mzstack[massql]'"
        ) from e
    return msql_engine, msql_parser


def _meta_prefilter(
    rt_range: tuple[float, float] | None,
    precmz_range: tuple[float, float] | None,
    polarity: int | None,
) -> str | None:
    """The per-spectrum prefilter, in MassQL's units."""
    return pred_and(
        pred_range("rt", *(rt_range or (None, None))),
        # An MS1 row has no precursor, so the NULL check keeps MS1 rows in
        # ms1_df.
        (
            f"(precmz IS NULL OR {pred_range('precmz', *precmz_range)})"
            if precmz_range
            else None
        ),
        f"polarity = {int(polarity)}" if polarity is not None else None,
    )


def _ms1_summary(
    store: MzStack,
    rt_range: tuple[float, float] | None = None,
    precmz_range: tuple[float, float] | None = None,
    polarity: int | None = None,
) -> pd.DataFrame:
    """One row per MS1 spectrum, standing in for its peaks.

    massql reduces the MS1 frame to ``max(i_norm)`` per scan when collating
    `scaninfo(MS2DATA)`, and that maximum is always 1.0. This frame carries
    exactly that.

    The peak columns are NaN: `required_levels` asks for real MS1 peaks
    whenever a condition touches them, so nothing that reads them reaches this
    frame, and NaN fails loudly if that stops being true.
    """
    where = _meta_prefilter(rt_range, precmz_range, polarity)
    sql = f"""
        WITH meta AS ({metadata_cte(store)}),
        linked AS ({ms1_link_sql()})
        SELECT scan, rt, polarity
        FROM linked
        WHERE ms_level = 1 {f"AND {where}" if where else ""}
    """
    df = duck.connection().execute(sql).to_arrow_table().to_pandas()
    df["i_norm"] = 1.0
    for column in ("i", "i_tic_norm", "mz"):
        df[column] = float("nan")
    return df[list(MS1_COLUMNS)]


def massql_frames(
    store: MzStack,
    rt_range: tuple[float, float] | None = None,
    precmz_range: tuple[float, float] | None = None,
    mz_range: tuple[float, float] | None = None,
    polarity: int | None = None,
    levels: Sequence[int] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build the ``(ms1_df, ms2_df)`` frames MassQL evaluates against.

    The per-spectrum ranges are prefilters applied in SQL, in MassQL's units
    (minutes for ``rt_range``). massql re-applies the full predicate
    afterwards.

    Args:
        mz_range: unused. Unlike the others this drops individual *peaks*, and
            ``i_norm`` and ``i_tic_norm`` are normalised over whatever peaks
            survive, so a window here rescales them and changes the summed
            spectrum ``scansum`` returns. `massql_sql` tests an m/z window
            without removing the peaks around it.
        levels: MS levels whose **peaks** are needed. The frames are one row
            per peak, and on a profile dataset the MS1 half is routinely 99%
            of the rows. Omitted means both.
    """
    import pandas as pd

    src = point_source(store, prefer=layout.PROJECTION_SCANSORTED)

    meta_where = _meta_prefilter(rt_range, precmz_range, polarity)
    peak_where = pred_range("mz", *(mz_range or (None, None)))

    # The level restriction applies on the *points* side. In `meta` it would
    # leave `linked`'s running-MS1 window with no MS1 rows to find, and
    # `ms1scan` would come back NULL for every row.
    wanted_levels = None
    if levels is not None:
        wanted_levels = pred_in("ms_level", sorted({int(v) for v in levels}))

    sql = f"""
        WITH meta AS ({metadata_cte(store)}),
        linked AS ({ms1_link_sql()}),
        filtered AS (
            SELECT * FROM linked {f"WHERE {meta_where}" if meta_where else ""}
        ),
        wanted AS (
            SELECT scan FROM filtered
            {f"WHERE {wanted_levels}" if wanted_levels else ""}
        ),
        points AS (
            SELECT
                {quote_ident("spectrum_id_")} AS scan,
                "mz",
                "intensity" AS i,
                "intensity" / max("intensity") OVER (
                    PARTITION BY {quote_ident("spectrum_id_")}
                ) AS i_norm,
                "intensity" / sum("intensity") OVER (
                    PARTITION BY {quote_ident("spectrum_id_")}
                ) AS i_tic_norm
            FROM {src.sql}
            WHERE {quote_ident("spectrum_id_")} IN (SELECT scan FROM wanted)
            {f"AND {peak_where}" if peak_where else ""}
        )
        SELECT
            f.ms_level,
            p.i, p.i_norm, p.i_tic_norm, p.mz,
            p.scan, f.rt, f.polarity,
            f.precmz, f.ms1scan, f.charge
        FROM points AS p
        JOIN filtered AS f USING (scan)
        ORDER BY p.scan, p.mz
    """
    # The ORDER BY holds the frame contract: peaks ascending in m/z within a
    # scan, as everywhere else in the library.

    con = duck.connection()
    # Arrow rather than `.df()`: measurably faster on frames this wide.
    df = con.execute(sql).to_arrow_table().to_pandas()

    ms1 = df.loc[df["ms_level"] == 1, list(MS1_COLUMNS)].reset_index(drop=True)
    ms2 = df.loc[df["ms_level"] == 2, list(MS2_COLUMNS)].reset_index(drop=True)

    # massql indexes these columns unconditionally in places, so an empty
    # frame still needs them.
    if ms1.empty:
        ms1 = pd.DataFrame(columns=list(MS1_COLUMNS))
    if ms2.empty:
        ms2 = pd.DataFrame(columns=list(MS2_COLUMNS))
    return ms1, ms2


def run_query(
    store: MzStack,
    query: str,
    engine: str = "sql",
    pushdown: bool = True,
    **kwargs: Any,
) -> pd.DataFrame:
    """Run a MassQL query over ``store``.

    Args:
        store: the spectra to query.
        query: the MassQL query.
        engine: ``sql`` compiles to one DuckDB statement where it can and
            falls back to the evaluator otherwise; ``engine`` always hands the
            frames to massql's evaluator. Both give the same answers; an
            uncompilable query runs through the evaluator either way.
        pushdown: derive prefilter bounds from the query and apply them in SQL
            before massql sees the frames.
    """
    if engine not in ("engine", "sql"):
        raise ValueError(f"engine must be 'engine' or 'sql', not {engine!r}")

    if engine == "sql":
        from ..errors import NotTranslatable
        from .massql_sql import run_compiled

        try:
            return run_compiled(store, query, **kwargs)
        except NotTranslatable:
            pass  # Fall through to the evaluator.

    msql_engine, _ = _require_massql()

    hints = None
    levels = None
    if pushdown:
        from .pushdown import extract_hints, required_levels

        hints = extract_hints(query)
        levels = required_levels(query)

    ms1_df, ms2_df = massql_frames(
        store,
        rt_range=hints.rt_range if hints else None,
        precmz_range=hints.precmz_range if hints else None,
        polarity=hints.polarity if hints else None,
        levels=levels,
    )

    if levels is not None and 1 not in levels:
        # `scaninfo(MS2DATA)` reports `i_norm_ms1`, which massql derives as the
        # per-scan max of the MS1 frame's `i_norm` -- always 1.0, since
        # `i_norm` is `i / max(i)` per scan. One summary row per MS1 spectrum
        # reproduces that groupby without reading any MS1 peak.
        ms1_df = _ms1_summary(
            store,
            rt_range=hints.rt_range if hints else None,
            precmz_range=hints.precmz_range if hints else None,
            polarity=hints.polarity if hints else None,
        )

    return msql_engine.process_query(
        query,
        str(store.path),
        ms1_df=ms1_df,
        ms2_df=ms2_df,
        **kwargs,
    )
