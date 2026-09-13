"""Command-line interface.

``mzstack convert | query | match | project | list | info``
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .errors import MzStackError
from .format import layout
from .format.manifest import Manifest
from .store import MzStack

__all__ = ["main", "build_parser"]


# --- output ------------------------------------------------------------------


def _write(df: Any, output: str | None) -> None:
    """Write a frame to ``output``, choosing the format from its extension."""
    if output is None:
        if df.empty:
            print("(no rows)")
        else:
            print(df.to_string(index=False))
        return

    path = Path(output)
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        df.to_parquet(path, index=False)
    elif suffix == ".json":
        df.to_json(path, orient="records", indent=2)
    elif suffix == ".csv":
        df.to_csv(path, index=False)
    else:
        df.to_csv(path, sep="\t", index=False)
    print(f"{len(df)} row(s) -> {path}", file=sys.stderr)


def _read_metadata_table(path: Path):
    """A sample-metadata sheet: CSV, TSV or XLSX with an `mzml_path` column."""
    import pandas as pd

    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xls"):
        try:
            table = pd.read_excel(path)
        except ImportError as e:
            raise MzStackError(
                "Reading .xlsx needs openpyxl: pip install 'mzstack[excel]'"
            ) from e
    elif suffix == ".csv":
        table = pd.read_csv(path)
    else:
        table = pd.read_csv(path, sep="\t")

    if "mzml_path" not in table.columns:
        raise MzStackError(
            f"'{path}' has no 'mzml_path' column; found: "
            + ", ".join(map(str, table.columns))
        )
    return table


# --- commands ----------------------------------------------------------------


def cmd_convert(args: argparse.Namespace) -> int:
    from .ingest import mzml_to_mzstack

    files = [Path(f) for f in args.files]
    metadata = None
    if args.metadata_file:
        table = _read_metadata_table(Path(args.metadata_file))
        base = Path(args.metadata_file).parent
        files = [
            p if (p := Path(row)).is_absolute() else base / p
            for row in table["mzml_path"]
        ]
        metadata = table

    if not files:
        raise MzStackError("No input files given.")

    mzml_to_mzstack(
        files,
        args.out,
        partitioning=("dataOrigin",) if args.partition else (),
        compression=args.compression,
        row_group_size=args.row_group_size,
        verbose=not args.quiet,
    )

    if metadata is not None:
        from .samples import write_sample_metadata

        write_sample_metadata(args.out, metadata, files)
        if not args.quiet:
            print(f"  sample metadata: {len(metadata)} row(s)")
    return 0


def cmd_metabolights(args: argparse.Namespace) -> int:
    from .ingest import metabolights as mtbls

    selection = {
        "assays": args.assay or (),
        "samples": args.sample or (),
        "include": args.include or (),
        "exclude": args.exclude or (),
    }

    if args.list:
        # Listing is the cheap half of the job: only ISA-Tab is transferred,
        # so a caller can size a download before committing to it.
        files = mtbls.list_study_files(
            args.study_id,
            cache_dir=args.cache_dir,
            local_only=args.local_only,
            refresh=args.refresh,
        )
        print(mtbls.describe_files(mtbls.select_files(files, **selection)))
        return 0

    if not args.out:
        raise MzStackError("No output dataset given (or use --list).")

    mtbls.metabolights_to_mzstack(
        args.study_id,
        args.out,
        cache_dir=args.cache_dir,
        partitioning=("dataOrigin",) if args.partition else (),
        compression=args.compression,
        row_group_size=args.row_group_size,
        local_only=args.local_only,
        refresh=args.refresh,
        verbose=not args.quiet,
        **selection,
    )
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    from .ingest.mzpeak import add_mzpeak_archives, create_mzpeak_dataset

    action = add_mzpeak_archives if args.add else create_mzpeak_dataset
    if args.add:
        action(args.out, args.archives, link=args.link, verbose=not args.quiet)
    else:
        action(args.archives, args.out, link=args.link, verbose=not args.quiet)
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    store = MzStack(args.store)
    if args.ms_level:
        store = store.filter_ms_level(args.ms_level)
    if args.rt:
        store = store.filter_rt(tuple(args.rt))

    result = store.massql(args.query, engine=args.engine, pushdown=not args.no_pushdown)
    _write(result, args.output)
    return 0


def cmd_match(args: argparse.Namespace) -> int:
    from .match import annotate_hits, blink_match
    from .reference import ReferenceLibrary

    store = MzStack(args.store).filter_ms_level(args.ms_level)
    if args.reference:
        reference: Any = ReferenceLibrary(args.reference)
    else:
        # No library given: match the selection against itself, which is the
        # molecular-networking shape.
        reference = store

    hits = blink_match(
        store,
        reference,
        tolerance=args.tolerance,
        bin_width=args.bin_width,
        intensity_power=args.intensity_power,
        min_score=args.min_score,
        min_matches=args.min_matches,
        remove_self_connections=args.reference is None,
        top_n=args.top_n,
    )
    hits = annotate_hits(hits, reference)
    _write(hits, args.output)
    return 0


def cmd_project(args: argparse.Namespace) -> int:
    from .projections import build_projection, drop_projection

    action = drop_projection if args.drop else build_projection
    action(args.store, args.type, verbose=not args.quiet)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    manifest = Manifest.read(args.store)
    print(f"{args.store}  ({manifest.format} {manifest.version})")
    print(f"  generation: {manifest.generation}")
    print(f"  runs:       {len(manifest.runs)}")
    print(f"  spectra:    {manifest.n_spectra}")
    for run in manifest.runs:
        ids = f"{run.uid_base}..{run.uid_base + run.n_spectra - 1}"
        projections = ", ".join(sorted(run.projections)) or "-"
        print(
            f"    {run.run_id:<24} {run.kind:<7} "
            f"{run.n_spectra:>8} spectra  ids {ids:<15} "
            f"projections: {projections}"
        )
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    store = MzStack(args.store)
    manifest = store.manifest

    print(f"{store.path}")
    print(f"  format:   {manifest.format} {manifest.version}")
    print(f"  kind:     {store.kind}")
    print(f"  spectra:  {manifest.n_spectra}")

    df = store.spectra_data(columns=["msLevel", "rtime", "polarity"])
    if not df.empty:
        levels = df["msLevel"].value_counts().sort_index()
        print("  ms levels: " + ", ".join(f"{k}: {v}" for k, v in levels.items()))
        print(f"  rtime:     {df['rtime'].min():.2f} .. {df['rtime'].max():.2f} s")
        polarities = df["polarity"].dropna().unique().tolist()
        print(f"  polarity:  {sorted(polarities)}  (1 positive, 0 negative)")

    print("  variables: " + ", ".join(store.spectra_variables()))
    return 0


# --- parser ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mzstack",
        description="Storage, querying and spectral matching for MS data.",
    )
    parser.add_argument("--version", action="version", version=f"mzstack {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    # convert
    p = sub.add_parser("convert", help="convert mzML files into a dataset")
    p.add_argument("files", nargs="*", help="mzML files (or use --metadata-file)")
    p.add_argument("out", help="dataset directory to create")
    p.add_argument(
        "--metadata-file",
        help="CSV/TSV/XLSX with an 'mzml_path' column; other columns become "
        "per-sample metadata",
    )
    p.add_argument(
        "--partition",
        action="store_true",
        help="Hive-partition the dataset by dataOrigin (one directory per file)",
    )
    p.add_argument(
        "--compression",
        default=layout.DEFAULT_COMPRESSION,
        choices=["snappy", "zstd", "gzip", "lz4", "none"],
    )
    p.add_argument("--row-group-size", type=int, default=layout.DEFAULT_ROW_GROUP_SIZE)
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(func=cmd_convert)

    # metabolights
    p = sub.add_parser(
        "metabolights",
        help="convert a public MetaboLights study, or a subset of it",
        description="Fetch the mzML files of a MetaboLights study and convert "
        "them. Studies whose assays reference no mzML are refused. Selection "
        "options combine, and are resolved from the study's ISA-Tab metadata "
        "before anything large is downloaded.",
    )
    p.add_argument("study_id", help="a study accession, e.g. MTBLS341")
    p.add_argument("out", nargs="?", help="dataset directory to create")
    p.add_argument(
        "--assay",
        action="append",
        metavar="NAME",
        help="assay file to take mzML from, e.g. a_MTBLS341_root_NEG_LCMS.txt; "
        "a glob is allowed. Repeatable; default is every assay",
    )
    p.add_argument(
        "--sample",
        action="append",
        metavar="NAME",
        help="keep files whose Sample Name or MS Assay Name matches. Repeatable",
    )
    p.add_argument(
        "--include",
        action="append",
        metavar="PATTERN",
        help="keep files whose name matches this glob. Repeatable",
    )
    p.add_argument(
        "--exclude",
        action="append",
        metavar="PATTERN",
        help="drop files whose name matches this glob, applied last. Repeatable",
    )
    p.add_argument(
        "--list",
        action="store_true",
        help="print the selected files and exit, downloading nothing",
    )
    p.add_argument(
        "--cache-dir",
        metavar="DIR",
        help=f"where downloads are kept (default {layout.metabolights_cache_dir()}). "
        "Nothing in it is ever deleted",
    )
    p.add_argument(
        "--local-only",
        action="store_true",
        help="use an already-downloaded study and do not contact the repository",
    )
    p.add_argument(
        "--refresh",
        action="store_true",
        help="re-fetch metadata and data files even when they are cached",
    )
    p.add_argument(
        "--partition",
        action="store_true",
        help="Hive-partition the dataset by dataOrigin (one directory per file)",
    )
    p.add_argument(
        "--compression",
        default=layout.DEFAULT_COMPRESSION,
        choices=["snappy", "zstd", "gzip", "lz4", "none"],
    )
    p.add_argument("--row-group-size", type=int, default=layout.DEFAULT_ROW_GROUP_SIZE)
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(func=cmd_metabolights)

    # index
    p = sub.add_parser(
        "index",
        help="index mzPeak archives as a dataset (the archives are not modified)",
    )
    p.add_argument("archives", nargs="+", help="archive directories or .mzpeak ZIPs")
    p.add_argument("out", help="dataset directory")
    p.add_argument(
        "--add",
        action="store_true",
        help="add to an existing dataset instead of creating one",
    )
    p.add_argument(
        "--link",
        choices=["reference", "copy"],
        default="reference",
        help="reference reads the archives where they are (default)",
    )
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(func=cmd_index)

    # query
    p = sub.add_parser("query", help="run a MassQL query")
    p.add_argument("store")
    p.add_argument("query", help="the MassQL query")
    p.add_argument("--ms-level", type=int, action="append")
    p.add_argument("--rt", type=float, nargs=2, metavar=("MIN", "MAX"), help="seconds")
    p.add_argument(
        "--engine",
        choices=["engine", "sql"],
        default="sql",
        help="sql compiles the query where it can and falls back otherwise; "
        "engine always uses massql's evaluator",
    )
    p.add_argument("--no-pushdown", action="store_true")
    p.add_argument("-o", "--output", help=".tsv/.csv/.json/.parquet")
    p.set_defaults(func=cmd_query)

    # match
    p = sub.add_parser("match", help="BLINK spectral matching")
    p.add_argument("store")
    p.add_argument(
        "--reference",
        help="reference library directory; omit to match the store against itself",
    )
    p.add_argument("--ms-level", type=int, default=2)
    p.add_argument("--tolerance", type=float, default=0.01)
    p.add_argument("--bin-width", type=float, default=0.001)
    p.add_argument("--intensity-power", type=float, default=0.5)
    p.add_argument("--min-score", type=float, default=0.5)
    p.add_argument("--min-matches", type=int, default=5)
    p.add_argument("--top-n", type=int, default=5)
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_match)

    # project
    p = sub.add_parser("project", help="build or drop a signal projection")
    p.add_argument("store")
    p.add_argument(
        "--type",
        default=layout.PROJECTION_SCANSORTED,
        choices=[layout.PROJECTION_SCANSORTED, layout.PROJECTION_MZSORTED],
    )
    p.add_argument("--drop", action="store_true")
    p.add_argument("-q", "--quiet", action="store_true")
    p.set_defaults(func=cmd_project)

    # list / info
    p = sub.add_parser("list", help="list a dataset's runs")
    p.add_argument("store")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("info", help="summarise a dataset")
    p.add_argument("store")
    p.set_defaults(func=cmd_info)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except MzStackError as e:
        print(f"mzstack: {e}", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f"mzstack: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
