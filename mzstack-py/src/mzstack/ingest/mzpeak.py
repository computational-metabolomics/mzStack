"""Building the derived index that registers mzPeak archives as a dataset.

Reads each archive's spectrum metadata and its scan / precursor / selected-ion
side tables, flattens them to one row per spectrum, and writes a small index.
No signal is read, and the archives are neither modified nor copied.

The index keeps mzPeak's own column names (``ms_level``, ``time``,
``selected_ion_mz``); translation to canonical names happens in a SQL view at
read time.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterable, Sequence
from pathlib import Path

from .. import duck
from ..errors import ArchiveError
from ..format import layout
from ..format.columnmap import quote_ident, quote_string
from ..format.manifest import Manifest
from ..mzpeak.archive import (
    Archive,
    column_mapping,
    is_archive,
    leaf_paths,
    read_archive,
    unpack_archive,
)

__all__ = ["create_mzpeak_dataset", "add_mzpeak_archives"]

#: Spectra per row group in the derived index.
INDEX_ROW_GROUP_SIZE = layout.INDEX_ROW_GROUP_SIZE

#: Columns taken from the archive's metadata table, under their mzPeak names.
METADATA_COLUMNS = (
    "spectrum_index",
    "id",
    "ms_level",
    "time",
    "scan_polarity",
    "spectrum_representation",
    "spectrum_type",
    "lowest_observed_mz",
    "highest_observed_mz",
    "base_peak_mz",
    "base_peak_intensity",
    "total_ion_current",
    "number_of_data_points",
    "number_of_peaks",
)

#: Columns taken from each side table, with the logical paths to look for.
#: A writer may nest these or keep them at the top level; both are found.
SCAN_COLUMNS = {
    "scan_start_time": ("scan_start_time",),
    "filter_string": ("filter_string",),
    "ion_injection_time": ("ion_injection_time",),
    "instrument_configuration_id": ("instrument_configuration_id",),
    "scan_window_lower_limit": (
        "scan_windows.scan_window_lower_limit",
        "scan_window_lower_limit",
    ),
    "scan_window_upper_limit": (
        "scan_windows.scan_window_upper_limit",
        "scan_window_upper_limit",
    ),
}

SELECTED_ION_COLUMNS = {
    "selected_ion_mz": ("selected_ion_mz",),
    "charge_state": ("charge_state",),
    # The spec calls this `peak_intensity` (MS:1000042); older archives wrote
    # a bare `intensity`.
    "peak_intensity": ("peak_intensity", "intensity"),
}

PRECURSOR_COLUMNS = {
    "precursor_index": ("precursor_index",),
    "isolation_window_target": (
        "isolation_window.isolation_window_target",
        "isolation_window_target",
    ),
    "isolation_window_lower_offset": (
        "isolation_window.isolation_window_lower_offset",
        "isolation_window_lower_offset",
    ),
    "isolation_window_upper_offset": (
        "isolation_window.isolation_window_upper_offset",
        "isolation_window_upper_offset",
    ),
    "collision_energy": ("activation.collision_energy", "collision_energy"),
}


def _resolve(leaves: dict[str, str], candidates: Sequence[str]) -> str | None:
    """The first candidate path present in a file, as a SQL expression."""
    for candidate in candidates:
        if candidate in leaves:
            return leaves[candidate]
    return None


def _facet_subquery(
    file: Path,
    columns: dict[str, tuple[str, ...]],
    count_alias: str,
    order_by: str | None = None,
) -> tuple[str, list[str]] | None:
    """One row per spectrum from a side table, plus how many rows there were.

    Side tables link back to the spectrum by ``source_index``; the first row
    of each group is kept and the count recorded, so a caller can tell that a
    spectrum had more detail than the flattened row shows.

    Args:
        order_by: column deciding which row of a group is "first"; defaults
            to ``source_index``, which makes the choice arbitrary within a
            group.
    """
    leaves = leaf_paths(file)
    if "source_index" not in leaves:
        return None
    order_expr = leaves.get(order_by) if order_by else None
    if order_expr is None:
        order_expr = leaves["source_index"]

    selected = []
    names = []
    for alias, candidates in columns.items():
        expr = _resolve(leaves, candidates)
        if expr is not None:
            selected.append(f"{expr} AS {quote_ident(alias)}")
            names.append(alias)
    if not selected:
        return None

    names.append(count_alias)
    sql = (
        "SELECT source_index, "
        + ", ".join(selected)
        + f", count(*) OVER (PARTITION BY source_index) AS {quote_ident(count_alias)} "
        + f"FROM read_parquet({quote_string(str(file))}) "
        "QUALIFY row_number() OVER (PARTITION BY source_index "
        f"ORDER BY {order_expr}) = 1"
    )
    return sql, names


def _write_index(dataset: Path, archive: Archive, run_id: str, uid_base: int) -> int:
    """Write one run's derived index; return the number of spectra."""
    con = duck.connection()
    meta_leaves = leaf_paths(archive.metadata)

    index_expr = meta_leaves.get("index")
    if index_expr is None:
        raise ArchiveError(
            f"'{archive.metadata}' has no 'index' column; the spectrum "
            "metadata table's primary key is required."
        )

    # INTEGER rather than BIGINT: half the index size, at a cap of ~2.1e9
    # spectra per dataset.
    selected = [
        f"CAST({uid_base} + {index_expr} AS INTEGER) AS {quote_ident('spectrum_id_')}",
        # The archive path, so a spectrum can be traced back to its run.
        f"{quote_string(str(archive.directory))} AS {quote_ident('data_origin')}",
    ]
    for name in METADATA_COLUMNS:
        expr = meta_leaves.get(name)
        if expr is not None:
            selected.append(f"{expr} AS {quote_ident(name)}")

    joins = []
    facets = (
        # A spectrum may have several scans; `scan_index` orders them, so the
        # row kept is the first rather than an arbitrary one.
        (archive.scans, SCAN_COLUMNS, "n_scans", "scan_index"),
        (archive.selected_ions, SELECTED_ION_COLUMNS, "n_selected_ions", None),
        (archive.precursors, PRECURSOR_COLUMNS, "n_precursors", None),
    )
    for i, (file, columns, count_alias, order_by) in enumerate(facets):
        if file is None:
            continue
        built = _facet_subquery(file, columns, count_alias, order_by)
        if built is None:
            continue
        sql, names = built
        alias = f"f{i}"
        joins.append(f" LEFT JOIN ({sql}) AS {alias} ON {alias}.source_index = m.idx_")
        selected.extend(f"{alias}.{quote_ident(n)} AS {quote_ident(n)}" for n in names)

    # `run_id` is NOT written into the file: DuckDB derives it from the
    # `run_id=<id>` directory name, and so can skip a run unopened.
    dest_dir = layout.index_spectra_path(dataset) / f"run_id={run_id}"
    shutil.rmtree(dest_dir, ignore_errors=True)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "part-0.parquet"

    # Materialised in a subquery: `JOIN ... ON` cannot see the outer select
    # list, and the key may be a nested expression.
    metadata_source = (
        f"(SELECT *, {index_expr} AS idx_ FROM read_parquet("
        + quote_string(str(archive.metadata))
        + ")) AS m"
    )

    con.execute(
        "COPY (SELECT "
        + ", ".join(selected)
        + f" FROM {metadata_source}"
        + "".join(joins)
        + " ORDER BY m.idx_) TO "
        + quote_string(str(dest))
        + f" (FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE {INDEX_ROW_GROUP_SIZE})"
    )

    return int(
        con.execute(
            f"SELECT count(*) FROM read_parquet({quote_string(str(dest))})"
        ).fetchone()[0]
    )


def _update_column_map(dataset: Path, archive: Archive, run_id: str) -> None:
    """Merge the archive's CV column mapping into the dataset's."""
    path = layout.column_map_path(dataset)
    existing: list[dict] = []
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            existing = []

    seen = {(r.get("path"), r.get("accession")) for r in existing}
    for row in column_mapping(archive.index):
        key = (row.get("path"), row.get("accession"))
        if key not in seen:
            existing.append({**row, "run_id": run_id})
            seen.add(key)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")


def _prepare_archive(source: Path, dataset: Path, link: str) -> tuple[Archive, str]:
    """Resolve an archive, unpacking or copying it if asked."""
    if not is_archive(source):
        raise ArchiveError(f"'{source}' is not an mzPeak archive.")

    name = layout.sanitise_run_id(source.stem if source.is_file() else source.name)

    if source.is_file():
        # A ZIP must be unpacked before its members can be read as files.
        directory = unpack_archive(source, layout.run_unpack_path(dataset, name))
    elif link == "copy":
        directory = layout.run_unpack_path(dataset, name)
        shutil.rmtree(directory, ignore_errors=True)
        shutil.copytree(source, directory)
    else:
        directory = source

    archive = read_archive(directory)
    return archive, layout.sanitise_run_id(archive.run_id or name)


def add_mzpeak_archives(
    path: str | Path,
    archives: str | Path | Iterable[str | Path],
    link: str = "reference",
    verbose: bool = True,
) -> Path:
    """Add mzPeak archives to an existing dataset.

    Args:
        path: the dataset directory.
        archives: archive directories or ``.mzpeak`` ZIPs.
        link: ``reference`` reads the archives in place; ``copy`` copies them
            into the dataset first.
        verbose: report each archive as it is indexed.
    """
    if isinstance(archives, str | Path):
        archives = [archives]
    sources = [Path(a) for a in archives]
    if not sources:
        raise ValueError("No archives given.")

    dataset = Path(path).resolve()
    manifest = (
        Manifest.read(dataset) if layout.is_mzstack_dataset(dataset) else Manifest.new()
    )
    if manifest.runs and manifest.kind != layout.KIND_MZPEAK:
        raise ArchiveError(
            f"'{dataset}' holds natively converted data; a dataset may not "
            "mix run kinds."
        )

    manifest.bump_generation()
    for source in sources:
        archive, run_id = _prepare_archive(source, dataset, link)
        uid_base = manifest.next_uid_base()

        n = _write_index(dataset, archive, run_id, uid_base)
        _update_column_map(dataset, archive, run_id)

        manifest.add_run(
            run_id=run_id,
            kind=layout.KIND_MZPEAK,
            path=str(archive.directory),
            n_spectra=n,
            layout_=archive.layout,
            profile=archive.profile.name if archive.profile else None,
            centroid=archive.centroid.name if archive.centroid else None,
        )
        if verbose:
            print(f"  {run_id}: {n} spectra indexed")

    manifest.write(dataset)
    duck.invalidate(dataset)
    return dataset


def create_mzpeak_dataset(
    archives: str | Path | Iterable[str | Path],
    path: str | Path,
    link: str = "reference",
    verbose: bool = True,
) -> Path:
    """Register mzPeak archives as a new mzStack dataset.

    Unless ``link="copy"`` the archives are neither modified nor copied: only
    a derived index is written, and peaks are read from them on demand.
    """
    dataset = Path(path)
    if layout.is_mzstack_dataset(dataset):
        raise ArchiveError(
            f"'{dataset}' is already an mzStack dataset; use "
            "add_mzpeak_archives() to add to it."
        )
    return add_mzpeak_archives(dataset, archives, link=link, verbose=verbose)
