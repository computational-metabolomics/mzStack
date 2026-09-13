"""The SQL both MassQL paths are built on.

`massql.massql_frames` hands whole frames to massql's evaluator;
`massql_sql.run_compiled` answers in SQL. Both start from the same two
fragments, which live here: per-spectrum metadata in MassQL's units, and the
running "previous MS1 scan" link.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .. import duck
from ..format.columnmap import quote_ident
from ..predicates import pred_and, pred_in

if TYPE_CHECKING:  # pragma: no cover
    from ..store import MzStack

__all__ = ["id_restriction", "metadata_cte", "ms1_link_sql"]

#: Above this many ids, a registered table is used instead of inlining them.
#: Mirrors `store.MAX_INLINE_IDS`.
MAX_INLINE_IDS = 1024


def id_restriction(store: MzStack) -> str | None:
    """A predicate for a store carrying a materialised id set.

    `MzStack.filter_contains_mz` and `select_ids` narrow by *ids* rather than
    by a predicate, so the selection is not visible in ``_predicate``. This
    turns the id set back into SQL, which both query paths apply.
    """
    ids = store._ids
    if ids is None:
        return None
    if not ids:
        return "FALSE"

    column = quote_ident("spectrum_id_")
    ordered = sorted(ids)
    if ordered[-1] - ordered[0] == len(ordered) - 1:
        # Contiguous: a range prunes row groups where a membership test cannot.
        return f"{column} BETWEEN {ordered[0]} AND {ordered[-1]}"
    if len(ordered) <= MAX_INLINE_IDS:
        return pred_in("spectrum_id_", ordered)

    name = f"cte_ids_{abs(hash((str(store.path), ids))) & 0xFFFFFFFF:08x}"
    duck.register_ids(ordered, name)
    return f"{column} IN (SELECT {column} FROM {quote_ident(name)})"


def metadata_cte(store: MzStack, extra: str | None = None) -> str:
    """Per-spectrum metadata, translated to MassQL's conventions.

    Retention time becomes minutes and polarity becomes MassQL's 1/2/0; the
    store holds seconds and 1/0.
    """
    view = quote_ident(duck.dataset_view(store.path))
    where = pred_and(store._predicate, id_restriction(store), extra)
    return f"""
        SELECT
            {quote_ident("spectrum_id_")} AS scan,
            "msLevel"                     AS ms_level,
            "rtime" / 60.0                AS rt,
            CASE "polarity"
                WHEN 1 THEN 1
                WHEN 0 THEN 2
                ELSE 0
            END                           AS polarity,
            "precursorMz"                 AS precmz,
            COALESCE("precursorCharge", 0) AS charge,
            "dataOrigin"                  AS data_origin
        FROM {view}
        {f"WHERE {where}" if where else ""}
    """


def ms1_link_sql(source: str = "meta") -> str:
    """Attach each spectrum to the MS1 preceding it in its own file.

    Reproduces massql's own running "previous MS1 scan" rather than using the
    precursor reference, which is often absent.

    This must run over **every** MS level. Restricting ``source`` to one level
    first leaves the window with no MS1 rows to find, and ``ms1scan`` silently
    comes back NULL for every row.
    """
    return f"""
        SELECT
            *,
            last_value(CASE WHEN ms_level = 1 THEN scan END IGNORE NULLS)
                OVER (
                    PARTITION BY data_origin ORDER BY scan
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS ms1scan
        FROM {source}
    """
