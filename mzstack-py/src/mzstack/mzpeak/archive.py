"""Resolving the members and signal columns of an mzPeak archive.

An archive is one MS run: Parquet files plus an ``mzpeak_index.json`` naming
them. Names are not the contract, so two rules from the specification
drive the module:

* members are resolved through the index by ``entity_type`` and
  ``data_kind``, never by file name;
* signal columns are resolved through the *array index* -- a JSON map in the
  Parquet footer identifying the m/z and intensity columns by CV term.
  Column names are the fallback for files carrying no array index.

Only the point layout is read; the chunked layout is recognised so that it can
be rejected rather than misread.
"""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .. import duck
from ..errors import ArchiveError
from ..format.columnmap import quote_ident, quote_string

__all__ = [
    "Archive",
    "SignalColumns",
    "read_archive",
    "unpack_archive",
    "MZPEAK_INDEX_FILE",
    "LAYOUT_POINT",
    "LAYOUT_CHUNK",
]

MZPEAK_INDEX_FILE = "mzpeak_index.json"

LAYOUT_POINT = "point"
LAYOUT_CHUNK = "chunk"
LAYOUTS = (LAYOUT_POINT, LAYOUT_CHUNK)

#: The CV terms naming the two arrays every spectrum has.
MS_MZ_ARRAY = "MS:1000514"
MS_INTENSITY_ARRAY = "MS:1000515"

#: Tokens Parquet inserts to express repetition. They are part of the
#: physical path but not the logical one; mzPeak's own ``path`` convention
#: omits them.
PARQUET_STRUCTURAL = frozenset({"list", "element", "item"})

_TOKEN_RE = re.compile(r"[^a-z0-9]+")


def token(value: object) -> str:
    """Normalise an ``entity_type`` / ``data_kind`` token.

    The specification writes these inconsistently -- ``data arrays`` and
    ``data_arrays`` both occur -- so they are compared in canonical form.
    """
    return _TOKEN_RE.sub("_", str(value).lower())


# --- the index file ----------------------------------------------------------


def read_index(directory: Path) -> dict:
    """Parse an archive's ``mzpeak_index.json``."""
    fl = directory / MZPEAK_INDEX_FILE
    if not fl.is_file():
        raise ArchiveError(
            f"'{directory}' is not an mzPeak archive: no {MZPEAK_INDEX_FILE}."
        )
    try:
        index = json.loads(fl.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ArchiveError(f"Could not parse '{fl}': {e}") from e

    files = index.get("files") if isinstance(index, dict) else None
    if not isinstance(files, list):
        raise ArchiveError(f"'{fl}' has no 'files' member.")
    for entry in files:
        if not all(k in entry for k in ("name", "entity_type", "data_kind")):
            raise ArchiveError(
                f"'{fl}' entries must have 'name', 'entity_type' and 'data_kind'."
            )
    return index


def member_names(index: dict, entity_type: str, data_kind: str) -> list[str]:
    """Members holding a given kind of data, by content rather than name.

    Empty when the archive has no such member, which is legal: an archive may
    omit chromatograms, centroids, or any other facet.
    """
    want = (token(entity_type), token(data_kind))
    return [
        str(f["name"])
        for f in index["files"]
        if (token(f["entity_type"]), token(f["data_kind"])) == want
    ]


def member_path(
    directory: Path, index: dict, entity_type: str, data_kind: str
) -> Path | None:
    """Absolute path of one archive member, or ``None``.

    Raises:
        ArchiveError: if the index names a member that is not on disk.
    """
    names = member_names(index, entity_type, data_kind)
    if not names:
        return None
    path = directory / names[0]
    if not path.exists():
        raise ArchiveError(
            f"'{MZPEAK_INDEX_FILE}' of '{directory}' lists '{names[0]}' "
            "but the file does not exist."
        )
    return path


def run_id(index: dict) -> str | None:
    """The archive's own run identifier, when it declares one."""
    metadata = index.get("metadata")
    if isinstance(metadata, dict):
        run = metadata.get("run")
        if isinstance(run, dict) and run.get("id"):
            return str(run["id"])
    return None


def column_mapping(index: dict) -> list[dict]:
    """Every CV column mapping the archive declares, flattened."""
    out: list[dict] = []
    for entry in index.get("files", []):
        mapping = entry.get("column_mapping")
        if not isinstance(mapping, list):
            continue
        for row in mapping:
            if isinstance(row, dict):
                out.append({"file": entry.get("name"), **row})
    return out


# --- Parquet introspection ---------------------------------------------------


def _kv_value(file: Path, key: str) -> str | None:
    """One Parquet footer metadata value, as text.

    ``decode()`` rather than a VARCHAR cast: casting a BLOB yields an escaped
    rendering that looks plausible and parses as nothing. Only the requested
    key is decoded, since footer values need not be text at all.
    """
    con = duck.connection()
    try:
        rows = con.execute(
            "SELECT decode(value) AS v FROM parquet_kv_metadata("
            + quote_string(str(file))
            + ") WHERE decode(key) = "
            + quote_string(key)
        ).fetchall()
    except Exception:  # noqa: BLE001 -- a missing/odd footer is not an error
        return None
    return str(rows[0][0]) if rows else None


def array_index(file: Path, entity_type: str = "spectrum") -> list[dict]:
    """mzPeak's description of what each signal column is: which holds m/z,
    which intensity, in what unit and encoding."""
    raw = _kv_value(file, f"{token(entity_type)}_array_index")
    if raw is None:
        return []
    try:
        parsed = json.loads(raw)
    except ValueError:
        return []
    entries = parsed.get("entries") if isinstance(parsed, dict) else None
    return [e for e in entries or [] if isinstance(e, dict)]


def _array_index_prefix(file: Path, entity_type: str) -> str | None:
    raw = _kv_value(file, f"{token(entity_type)}_array_index")
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    prefix = parsed.get("prefix") if isinstance(parsed, dict) else None
    return token(prefix) if prefix else None


def leaf_paths(file: Path) -> dict[str, str]:
    """Every leaf column of a Parquet file: logical path -> SQL expression.

    Finds a column whether or not the writer nested it:
    ``isolation_window_target`` may sit at the top level or inside an
    ``isolation_window`` group, and both appear under their logical path.
    """
    con = duck.connection()
    rows = con.execute(
        "SELECT DISTINCT path_in_schema FROM parquet_metadata("
        + quote_string(str(file))
        + ")"
    ).fetchall()

    out: dict[str, str] = {}
    for (raw,) in rows:
        if raw is None:
            continue
        tokens = [t.strip() for t in str(raw).split(",")]
        logical = [t for t in tokens if t not in PARQUET_STRUCTURAL]
        if not logical:
            continue
        out[".".join(logical)] = _path_expr(tokens)
    return out


def _path_expr(tokens: list[str]) -> str:
    """Turn a Parquet leaf path into a DuckDB expression.

    A run of structural tokens becomes ``[1]``, taking the first entry of a
    repeated group. Uses ``struct_extract()``, not dotted syntax: a top-level
    group named ``point`` is ambiguous with a table alias.
    """
    expr: str | None = None
    i = 0
    while i < len(tokens):
        if tokens[i] in PARQUET_STRUCTURAL:
            while i < len(tokens) and tokens[i] in PARQUET_STRUCTURAL:
                i += 1
            expr = f"{expr}[1]"
            continue
        if expr is None:
            expr = quote_ident(tokens[i])
        else:
            expr = f"struct_extract({expr}, '{tokens[i]}')"
        i += 1
    return expr or "NULL"


def layout_of(file: Path, entity_type: str = "spectrum") -> str | None:
    """Signal layout of a data/peaks file: ``point``, ``chunk`` or ``None``.

    From the array index's ``prefix``, falling back to the top-level group
    name in the schema.
    """
    prefix = _array_index_prefix(file, entity_type)
    if prefix in LAYOUTS:
        return prefix

    columns = {token(p.split(".")[0]) for p in leaf_paths(file)}
    if LAYOUT_CHUNK in columns or any("_chunk_" in c for c in columns):
        return LAYOUT_CHUNK
    if LAYOUT_POINT in columns or "mz" in columns:
        return LAYOUT_POINT
    return None


@dataclass(frozen=True)
class SignalColumns:
    """SQL expressions for the three columns a point-layout file must have."""

    index: str
    mz: str
    intensity: str


def signal_columns(file: Path, entity_type: str = "spectrum") -> SignalColumns:
    """Locate the index, m/z and intensity columns of a signal file.

    m/z and intensity come from the array index by CV term, preferring the
    entry marked ``primary``. The index column is not in the array index; its
    name is fixed and it is the first column of the layout group.

    Raises:
        ArchiveError: if any of the three cannot be resolved.
    """
    leaves = leaf_paths(file)
    entries = array_index(file, entity_type)

    def by_term(term: str) -> str | None:
        hits = [e for e in entries if e.get("array_type") == term]
        if not hits:
            return None
        # Several arrays may share a type (differing units or precisions);
        # the writer marks the everyday one `primary`.
        primary = [e for e in hits if e.get("buffer_priority") == "primary"]
        path = (primary or hits)[0].get("path")
        return leaves.get(str(path)) if path else None

    # The layout group, if the writer used one.
    prefix = None
    for path in leaves:
        top = path.split(".")[0]
        if token(top) in LAYOUTS:
            prefix = top
            break

    def qualified(name: str) -> str | None:
        candidates = [f"{prefix}.{name}", name] if prefix else [name]
        for candidate in candidates:
            if candidate in leaves:
                return leaves[candidate]
        return None

    index_name = f"{token(entity_type)}_index"
    index_expr = qualified(index_name)
    if index_expr is None:
        raise ArchiveError(
            f"'{file}' has no '{index_name}' column; a point-layout signal "
            "file must carry one as the first column of its layout group."
        )

    # The array index is authoritative; names are the fallback for files that
    # carry none.
    mz = by_term(MS_MZ_ARRAY) or qualified("mz")
    intensity = by_term(MS_INTENSITY_ARRAY) or qualified("intensity")
    if mz is None or intensity is None:
        raise ArchiveError(
            f"Could not resolve the m/z and intensity columns of '{file}'. "
            "Expected an array index naming "
            f"{MS_MZ_ARRAY} and {MS_INTENSITY_ARRAY}, or columns named "
            "'mz' and 'intensity'."
        )
    return SignalColumns(index=index_expr, mz=mz, intensity=intensity)


# --- archives ----------------------------------------------------------------


@dataclass(frozen=True)
class Archive:
    """A validated mzPeak archive, with its members resolved."""

    directory: Path
    index: dict
    metadata: Path
    layout: str
    scans: Path | None = None
    precursors: Path | None = None
    selected_ions: Path | None = None
    profile: Path | None = None
    centroid: Path | None = None

    @property
    def run_id(self) -> str | None:
        return run_id(self.index)

    def signal_file(self, representation: str = "auto") -> Path:
        """The signal file to read for ``representation``.

        ``auto`` prefers centroids, falling back to profile data.
        """
        if representation == "profile":
            chosen = self.profile
        elif representation == "centroid":
            chosen = self.centroid
        else:
            chosen = self.centroid or self.profile
        if chosen is None:
            raise ArchiveError(
                f"Archive '{self.directory}' has no {representation} signal data."
            )
        return chosen


def read_archive(directory: str | Path) -> Archive:
    """Validate an archive and resolve its members.

    Raises:
        ArchiveError: if required members are missing, or signal is stored in
            the chunked layout.
    """
    path = Path(directory).resolve()
    index = read_index(path)

    metadata = member_path(path, index, "spectrum", "metadata")
    if metadata is None:
        raise ArchiveError(f"Archive '{path}' has no spectrum metadata table.")

    profile = member_path(path, index, "spectrum", "data_arrays")
    centroid = member_path(path, index, "spectrum", "peaks")
    if profile is None and centroid is None:
        raise ArchiveError(
            f"Archive '{path}' has neither spectrum signal data nor peak data."
        )

    layout = None
    for signal in (profile, centroid):
        if signal is None:
            continue
        found = layout_of(signal)
        if found == LAYOUT_CHUNK:
            raise ArchiveError(
                f"Archive '{path}' stores signal in the chunked layout, which "
                "mzstack cannot read yet. Only the point layout is supported."
            )
        if layout is None:
            layout = found
    if layout is None:
        raise ArchiveError(
            f"Could not determine the signal layout of archive '{path}'."
        )

    return Archive(
        directory=path,
        index=index,
        metadata=metadata,
        layout=layout,
        scans=member_path(path, index, "spectrum", "scans"),
        precursors=member_path(path, index, "spectrum", "precursors"),
        selected_ions=member_path(path, index, "spectrum", "selected_ions"),
        profile=profile,
        centroid=centroid,
    )


def unpack_archive(source: str | Path, dest: str | Path) -> Path:
    """Unpack a ``.mzpeak`` ZIP so its members can be read as files."""
    target = Path(dest)
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(source) as zf:
        zf.extractall(target)

    # A ZIP may wrap its members in a single top-level directory.
    if not (target / MZPEAK_INDEX_FILE).is_file():
        subdirs = [p for p in target.iterdir() if p.is_dir()]
        for sub in subdirs:
            if (sub / MZPEAK_INDEX_FILE).is_file():
                return sub
    return target


def is_archive(path: str | Path) -> bool:
    """Does ``path`` look like an mzPeak archive, packed or unpacked?"""
    p = Path(path)
    if p.is_dir():
        return (p / MZPEAK_INDEX_FILE).is_file()
    return p.is_file() and zipfile.is_zipfile(p)
