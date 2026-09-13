"""Ingesting a public MetaboLights study, or a subset of one.

A study is described by ISA-Tab: one investigation file, one sample sheet, and
an assay file per platform/polarity combination. Each assay row names the
spectral data file for one acquisition, in either of two columns:

``Raw Spectral Data File``
    the instrument's own output, which for most studies is a vendor format
    (``.raw``, ``.d``, ``.cdf``) that mzstack cannot read;
``Derived Spectral Data File``
    what the submitter converted it to, which is where mzML most often lives.

Both are read, and only mzML is kept. A study whose assays reference no mzML at
all is refused rather than half-ingested: see :class:`~mzstack.errors.StudyError`.

Selection happens before anything large is transferred, so a subset costs a
subset's bandwidth. Downloads land in a cache directory keyed by study id and
are never deleted, which also keeps ``dataOrigin`` stable across runs.
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from ..errors import StudyError
from ..format import layout
from .mzml import MZML_EXTENSIONS, mzml_to_mzstack

if TYPE_CHECKING:  # pragma: no cover
    import pandas as pd

__all__ = [
    "StudyFile",
    "list_study_files",
    "select_files",
    "describe_files",
    "metabolights_to_mzstack",
    "DEFAULT_CACHE_DIR",
]

#: Assay columns naming a spectral data file, and the ``StudyFile.column``
#: value recorded for each. mzML is found under either, depending on whether
#: the submitter acquired it or converted to it.
_FILE_COLUMNS = {
    "Raw Spectral Data File": "raw",
    "Derived Spectral Data File": "derived",
}

#: Assay columns identifying the acquisition a data file belongs to.
_SAMPLE_NAME = "Sample Name"
_ASSAY_NAME = "MS Assay Name"

#: Where downloaded study files are kept. One sub-directory per study, holding
#: the ISA-Tab metadata and a ``FILES/`` tree mirroring the repository.
DEFAULT_CACHE_DIR = layout.metabolights_cache_dir()

_INSTALL_HINT = (
    "MetaboLights ingest needs metabolights-utils: "
    "pip install 'mzstack[metabolights]'"
)


# --- the study's mzML files --------------------------------------------------


@dataclass(frozen=True)
class StudyFile:
    """One mzML file referenced by a study's assay table."""

    #: The study accession, upper case, e.g. ``MTBLS341``.
    study_id: str
    #: File name of the assay that references it.
    assay: str
    #: Path within the study, POSIX, e.g. ``FILES/neg_Exp2_K01.mzML``.
    remote_path: str
    #: The assay row's ``Sample Name``.
    sample_name: str
    #: The assay row's ``MS Assay Name``; may be empty.
    assay_name: str
    #: Which assay column referenced it: ``raw`` or ``derived``.
    column: str
    #: Its row's position in the assay table, so the row can be recovered.
    row: int

    @property
    def name(self) -> str:
        """The file's base name, without its directory."""
        return PurePosixPath(self.remote_path).name


def _is_mzml(name: str) -> bool:
    lowered = name.lower()
    return any(lowered.endswith(ext) for ext in MZML_EXTENSIONS)


def _repository(
    cache_dir: Path,
    ftp_server_url: str | None = None,
    remote_root: str | None = None,
) -> Any:
    """A repository client rooted at ``cache_dir``.

    Both storage paths are set explicitly; the library's own default is
    ``~/metabolights_data``.
    """
    try:
        from metabolights_utils.provider.ftp_repository import (
            MetabolightsFtpRepository,
        )
    except ImportError as e:  # pragma: no cover - exercised by hand
        raise StudyError(_INSTALL_HINT) from e

    return MetabolightsFtpRepository(
        local_storage_root_path=str(cache_dir),
        local_storage_cache_path=str(cache_dir / ".model-cache"),
        ftp_server_url=ftp_server_url,
        remote_repository_root_directory=remote_root,
    )


def _load_model(
    repo: Any,
    study_id: str,
    local_only: bool = False,
    refresh: bool = False,
) -> Any:
    """The study's ISA-Tab model. Metadata only; no data files are fetched."""
    model, messages = repo.load_study_model(
        study_id,
        use_only_local_path=local_only,
        override_local_files=refresh,
        load_folder_metadata=False,
        # The library's JSON model cache can return a stale study; this
        # re-parses the ISA-Tab every time.
        use_study_model_cache=False,
    )
    if model is None:
        detail = "; ".join(m.short for m in messages) or "no such study"
        raise StudyError(f"{study_id}: could not read the study ({detail}).")
    return model


def _files_from_model(model: Any, study_id: str) -> list[StudyFile]:
    """Every mzML the model's assays reference, in assay then row order."""
    found: list[StudyFile] = []
    for assay_name, assay in model.assays.items():
        data = assay.table.data
        samples = data.get(_SAMPLE_NAME, [])
        assay_names = data.get(_ASSAY_NAME, [])

        for column, kind in _FILE_COLUMNS.items():
            for row, value in enumerate(data.get(column, [])):
                value = (value or "").strip()
                if not value or not _is_mzml(value):
                    continue
                found.append(
                    StudyFile(
                        study_id=study_id,
                        assay=assay_name,
                        remote_path=value.replace("\\", "/").lstrip("/"),
                        sample_name=_at(samples, row),
                        assay_name=_at(assay_names, row),
                        column=kind,
                        row=row,
                    )
                )

    if not found:
        raise StudyError(_no_mzml_message(model, study_id))

    # An assay may reference the same file from both columns; keep the first.
    seen: set[str] = set()
    unique = []
    for file in found:
        if file.remote_path not in seen:
            seen.add(file.remote_path)
            unique.append(file)
    return unique


def _at(values: Sequence[str], row: int) -> str:
    """``values[row]`` when the column is present and long enough, else ``""``."""
    if row < len(values):
        return (values[row] or "").strip()
    return ""


def _no_mzml_message(model: Any, study_id: str) -> str:
    """Why a study was refused, naming the formats it does hold."""
    extensions: set[str] = set()
    for assay in model.assays.values():
        extensions.update(assay.referenced_raw_file_extensions)
        extensions.update(assay.referenced_derived_file_extensions)
    extensions.discard("")

    if extensions:
        held = "found " + ", ".join(sorted(extensions))
    else:
        held = "its assays reference no data files at all"
    return (
        f"{study_id} references no mzML files ({held}). mzstack ingests mzML "
        "only; convert the study's raw data first, e.g. with ThermoRawFileParser "
        "or msconvert."
    )


def list_study_files(
    study_id: str,
    cache_dir: str | Path | None = None,
    local_only: bool = False,
    refresh: bool = False,
    ftp_server_url: str | None = None,
    remote_root: str | None = None,
) -> list[StudyFile]:
    """Every mzML file a MetaboLights study references.

    Only the study's ISA-Tab metadata is transferred, so this is cheap enough
    to call before deciding what to download.

    Args:
        study_id: a study accession, e.g. ``MTBLS341``.
        cache_dir: where study files are kept; defaults to
            :data:`DEFAULT_CACHE_DIR`.
        local_only: read an already-downloaded study from the cache and do not
            contact the repository.
        refresh: re-download the ISA-Tab metadata even if it is cached.
        ftp_server_url: override the repository host.
        remote_root: override the repository's root directory.

    Raises:
        StudyError: if the study cannot be read, or references no mzML.
    """
    study_id = _normalise(study_id)
    cache = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR
    repo = _repository(cache, ftp_server_url, remote_root)
    model = _load_model(repo, study_id, local_only=local_only, refresh=refresh)
    return _files_from_model(model, study_id)


def _normalise(study_id: str) -> str:
    study_id = (study_id or "").strip().strip("/").upper()
    if not study_id:
        raise StudyError("No study id given.")
    return study_id


# --- choosing a subset -------------------------------------------------------


def _matches(value: str, patterns: Sequence[str]) -> bool:
    """Does ``value`` match any pattern, as an exact string or a glob?"""
    return any(value == p or fnmatch(value, p) for p in patterns)


def select_files(
    files: Sequence[StudyFile],
    assays: Sequence[str] = (),
    samples: Sequence[str] = (),
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
) -> list[StudyFile]:
    """The subset of ``files`` a caller asked for.

    The three positive filters are ANDed and each is a no-op when empty;
    ``exclude`` is applied last and wins. Every pattern may be an exact string
    or an :mod:`fnmatch` glob.

    Args:
        assays: assay file names, e.g. ``a_MTBLS341_exudate_NEG_LCMS.txt``.
        samples: matched against a file's ``Sample Name`` or ``MS Assay Name``.
        include: matched against the file's base name or its path in the study.
        exclude: the same, for files to drop.

    Raises:
        StudyError: if the selection matches nothing. A mistyped assay name
            must not quietly download the whole study instead.
    """
    selected = []
    for file in files:
        if assays and not _matches(file.assay, assays):
            continue
        if samples and not (
            _matches(file.sample_name, samples) or _matches(file.assay_name, samples)
        ):
            continue
        paths = (file.name, file.remote_path)
        if include and not any(_matches(p, include) for p in paths):
            continue
        if exclude and any(_matches(p, exclude) for p in paths):
            continue
        selected.append(file)

    if not selected:
        raise StudyError(_no_selection_message(files))
    return selected


def _no_selection_message(files: Sequence[StudyFile]) -> str:
    """Why nothing was selected, and what there was to choose from."""
    assays = sorted({f.assay for f in files})
    return (
        f"No mzML file matched the selection. {len(files)} file(s) are "
        "available, across these assays:\n  " + "\n  ".join(assays)
    )


# --- downloading and converting ----------------------------------------------


def _remote_sizes(
    repo: Any, study_id: str, files: Sequence[StudyFile]
) -> dict[str, int]:
    """Size of each selected file on the repository, by path within the study.

    Listed one parent directory at a time -- almost always just ``FILES`` --
    so this costs one round trip per directory, not one per file. A failed
    listing is skipped; the sizes only check downloads.
    """
    wanted = {f.remote_path for f in files}
    sizes: dict[str, int] = {}
    # `repo` inherits `list_directory` but never initialises the connection
    # state it needs; the composed client is the one that works.
    client = getattr(repo, "ftp_client", repo)
    for parent in sorted({posixpath.dirname(f.remote_path) for f in files}):
        try:
            listing = client.list_directory(
                posixpath.join(study_id, parent).rstrip("/")
            )
        except Exception:  # noqa: BLE001 - a failed size check is skipped
            continue
        if not listing.success:
            continue
        for entry in listing.descriptors:
            if entry.is_directory:
                continue
            path = posixpath.join(parent, entry.base_name).lstrip("/")
            if path in wanted:
                sizes[path] = entry.size_in_bytes
    return sizes


def _download(
    repo: Any,
    study_id: str,
    cache: Path,
    files: Sequence[StudyFile],
    refresh: bool,
    verbose: bool,
) -> list[Path]:
    """Fetch the selected files into the cache; return their local paths.

    A file already present at its full size is not fetched again. One that is
    present but the wrong size is a truncated earlier download, and is fetched
    again before it can be converted into a silently short dataset.
    """
    sizes = _remote_sizes(repo, study_id, files)
    paths = [cache / study_id / PurePosixPath(f.remote_path) for f in files]

    def _wrong_size(path: Path, remote: str) -> bool:
        """Present, but not the length the repository reports."""
        expected = sizes.get(remote)
        if expected is None or not path.is_file():
            return False
        return path.stat().st_size != expected

    pairs = list(zip(files, paths, strict=True))
    absent = [f.remote_path for f, p in pairs if not p.is_file()]
    truncated = [f.remote_path for f, p in pairs if _wrong_size(p, f.remote_path)]

    if verbose:
        fetching = set(absent) | set(truncated)
        total = sum(sizes.get(path, 0) for path in fetching)
        cached = len(files) - len(fetching)
        print(
            f"  {len(fetching)} file(s) to download"
            + (f" ({_human(total)})" if total else "")
            + (f", {cached} already cached" if cached else "")
        )

    def _fetch(selected: Sequence[str], override: bool) -> None:
        if not selected:
            return
        result = repo.download_study_data_files(
            study_id,
            selected_data_files=list(selected),
            # `local_path` is what the study id is resolved against, so files
            # land in `<cache>/<study>/FILES/...`.
            local_path=str(cache),
            override_local_files=override,
            delete_unlisted_local_files=False,
        )
        if not result.success:
            raise StudyError(
                f"{study_id}: download failed ({result.message or result.code})."
            )

    # Everything, without overwriting: the library skips files already there,
    # so a re-run fetches only what is missing.
    _fetch([f.remote_path for f in files], override=refresh)
    # Then the ones a previous run left half-written. Without this pass they
    # are skipped as present and converted into a short dataset.
    if not refresh:
        _fetch(truncated, override=True)

    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        raise StudyError(
            f"{study_id}: the repository did not deliver "
            + ", ".join(missing[:3])
            + ("..." if len(missing) > 3 else "")
        )
    still_short = [f.remote_path for f, p in pairs if _wrong_size(p, f.remote_path)]
    if still_short:
        raise StudyError(
            f"{study_id}: incomplete download of "
            + ", ".join(still_short[:3])
            + ("..." if len(still_short) > 3 else "")
            + ". Re-run with refresh=True to fetch them again."
        )
    return paths


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"  # pragma: no cover


def _metadata_frame(
    model: Any,
    files: Sequence[StudyFile],
    study_id: str,
) -> pd.DataFrame:
    """One row per selected file, describing the sample it came from.

    The assay row and the sample-sheet row it points at are both carried over
    whole, under ``assay.``/``sample.`` prefixes: ISA-Tab reuses header names
    (``Protocol REF``, ``Term Source REF``) across both sheets, and prefixing
    is what keeps them apart without dropping anything.
    """
    import pandas as pd

    sample_rows = _sample_rows(model)
    records = []
    for file in files:
        record: dict[str, Any] = {
            "study_id": study_id,
            "assay_file": file.assay,
            "mzml_file": file.remote_path,
            "spectral_data_column": file.column,
            "sample_name": file.sample_name,
            "assay_name": file.assay_name,
        }
        assay = model.assays[file.assay].table.data
        for column, values in assay.items():
            record[f"assay.{column}"] = _at(values, file.row)
        for column, value in sample_rows.get(file.sample_name, {}).items():
            record[f"sample.{column}"] = value
        records.append(record)

    return pd.DataFrame.from_records(records)


def _sample_rows(model: Any) -> dict[str, dict[str, str]]:
    """The sample sheet, keyed by ``Sample Name``.

    A name appearing twice keeps its first row; the sheet is meant to be one
    row per sample, and picking arbitrarily beats failing on a study that is
    merely untidy.
    """
    rows: dict[str, dict[str, str]] = {}
    for sheet in model.samples.values():
        data = sheet.table.data
        names = data.get(_SAMPLE_NAME, [])
        for row, name in enumerate(names):
            name = (name or "").strip()
            if name and name not in rows:
                rows[name] = {c: _at(v, row) for c, v in data.items()}
    return rows


def metabolights_to_mzstack(
    study_id: str,
    path: str | Path,
    assays: Sequence[str] = (),
    samples: Sequence[str] = (),
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
    cache_dir: str | Path | None = None,
    partitioning: Sequence[str] = (),
    compression: str = layout.DEFAULT_COMPRESSION,
    row_group_size: int = layout.DEFAULT_ROW_GROUP_SIZE,
    local_only: bool = False,
    refresh: bool = False,
    ftp_server_url: str | None = None,
    remote_root: str | None = None,
    verbose: bool = True,
) -> Path:
    """Convert a MetaboLights study, or a subset of it, into a dataset.

    Resolves the study's ISA-Tab metadata, selects mzML files from it,
    downloads only those, converts them with :func:`~mzstack.ingest.mzml_to_mzstack`,
    and writes the study's own sample and assay annotation as the dataset's
    per-sample metadata.

    Args:
        study_id: a study accession, e.g. ``MTBLS341``.
        path: the dataset directory to create.
        assays: assay files to take mzML from; all of them when empty.
        samples: sample or MS assay names to keep.
        include: file-name patterns to keep.
        exclude: file-name patterns to drop, applied last.
        cache_dir: where downloads are kept; defaults to
            :data:`DEFAULT_CACHE_DIR`. Nothing in it is ever deleted.
        partitioning: Hive partition columns, e.g. ``("dataOrigin",)``.
        compression: Parquet codec.
        row_group_size: spectra per row group.
        local_only: use an already-downloaded study and do not contact the
            repository.
        refresh: re-fetch metadata and data files even when cached.
        ftp_server_url: override the repository host.
        remote_root: override the repository's root directory.
        verbose: report progress.

    Returns:
        The dataset path.

    Raises:
        StudyError: if the study holds no mzML, if the selection matches
            nothing, or if the download does not complete.
    """
    study_id = _normalise(study_id)
    cache = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR
    repo = _repository(cache, ftp_server_url, remote_root)

    model = _load_model(repo, study_id, local_only=local_only, refresh=refresh)
    available = _files_from_model(model, study_id)
    selected = select_files(
        available, assays=assays, samples=samples, include=include, exclude=exclude
    )
    if verbose:
        print(f"{study_id}: {len(selected)} of {len(available)} mzML file(s) selected")

    if local_only:
        paths = [cache / study_id / PurePosixPath(f.remote_path) for f in selected]
        missing = [str(p) for p in paths if not p.is_file()]
        if missing:
            raise StudyError(
                f"{study_id}: not in the cache: " + ", ".join(missing[:3])
            )
    else:
        paths = _download(repo, study_id, cache, selected, refresh, verbose)

    dataset = mzml_to_mzstack(
        paths,
        path,
        partitioning=partitioning,
        compression=compression,
        row_group_size=row_group_size,
        verbose=verbose,
    )

    from ..samples import write_sample_metadata

    frame = _metadata_frame(model, selected, study_id)
    write_sample_metadata(dataset, frame, paths)
    if verbose:
        print(f"  sample metadata: {len(frame)} row(s)")
    return dataset


def describe_files(files: Iterable[StudyFile]) -> str:
    """The selection as a table, for ``--list``."""
    files = list(files)
    if not files:
        return "(no files)"
    width = max(len(f.assay) for f in files)
    sample_width = max(len(f.sample_name) for f in files)
    lines = [
        f"{f.assay:<{width}}  {f.sample_name:<{sample_width}}  {f.remote_path}"
        for f in files
    ]
    lines.append(f"{len(files)} file(s)")
    return "\n".join(lines)
