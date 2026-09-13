"""Browsing the server's filesystem, within limits.

Source files are picked on the machine running the API rather than uploaded:
mzML files are routinely gigabytes, and mzPeak datasets are read in place and
must never be copied through a browser.

That makes path handling security-relevant. `resolve_within` is the only way a
client-supplied path becomes a `Path`, and it refuses anything that does not
land inside a configured data root -- after resolution, so `..` and symlinks
cannot walk out.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mzstack import is_mzstack_dataset
from mzstack.ingest.mzml import MZML_EXTENSIONS

from .config import Config
from .errors import BadRequest, Forbidden, NotFound

__all__ = ["FileEntry", "classify", "list_directory", "resolve_within", "roots"]

#: What the ingest endpoints can each accept.
KIND_DIRECTORY = "directory"
KIND_MZML = "mzml"
KIND_MZPEAK = "mzpeak"
KIND_MZSTACK = "mzstack"
KIND_OTHER = "other"


@dataclass(frozen=True)
class FileEntry:
    name: str
    path: Path
    kind: str
    size: int | None

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "path": str(self.path),
            "kind": self.kind,
            "size": self.size,
        }


def roots(config: Config) -> list[Path]:
    """The directories a client may browse."""
    return [r for r in config.data_roots if r.is_dir()]


def resolve_within(config: Config, raw: str | None) -> Path:
    """A client-supplied path, resolved and confined to a data root."""
    if not raw:
        # No path given: the first root is the natural starting point.
        available = roots(config)
        if not available:
            raise NotFound(
                "No browsable directory is configured. Set MZSTACK_DATA_ROOTS "
                "or MZSTACK_WORKSPACE."
            )
        return available[0]

    try:
        path = Path(raw).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise BadRequest(f"Invalid path: {raw!r}") from exc

    for root in config.data_roots:
        if path == root or path.is_relative_to(root):
            return path
    raise Forbidden(
        f"{path} is outside the configured data roots "
        f"({', '.join(str(r) for r in config.data_roots)})"
    )


def classify(path: Path) -> str:
    """What `path` is, as far as the ingest endpoints are concerned."""
    name = path.name.lower()
    if path.is_dir():
        if is_mzstack_dataset(path):
            return KIND_MZSTACK
        if _is_mzpeak_archive(path):
            return KIND_MZPEAK
        return KIND_DIRECTORY
    if name.endswith(MZML_EXTENSIONS):
        return KIND_MZML
    if name.endswith(".mzpeak"):
        # A zipped archive; mzstack unpacks it during indexing.
        return KIND_MZPEAK
    return KIND_OTHER


def list_directory(config: Config, raw: str | None) -> dict[str, object]:
    """One directory's contents, plus enough context for a breadcrumb."""
    path = resolve_within(config, raw)
    if not path.is_dir():
        raise BadRequest(f"Not a directory: {path}")

    entries: list[FileEntry] = []
    for child in sorted(
        path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())
    ):
        if child.name.startswith("."):
            continue
        try:
            size = None if child.is_dir() else child.stat().st_size
        except OSError:
            continue
        entries.append(FileEntry(child.name, child, classify(child), size))

    return {
        "path": str(path),
        "parent": str(path.parent) if _within_roots(config, path.parent) else None,
        "roots": [str(r) for r in roots(config)],
        "entries": [e.as_dict() for e in entries],
    }


def resolve_all(
    config: Config, raws: list[str], expect: str | None = None
) -> list[Path]:
    """Several client-supplied paths, each confined and checked to exist."""
    if not raws:
        raise BadRequest("No files given.")
    resolved = []
    for raw in raws:
        path = resolve_within(config, raw)
        if not path.exists():
            raise NotFound(f"No such file: {path}")
        if expect is not None and classify(path) != expect:
            raise BadRequest(f"{path} is not {expect}")
        resolved.append(path)
    return resolved


def _within_roots(config: Config, path: Path) -> bool:
    return any(path == r or path.is_relative_to(r) for r in config.data_roots)


def _is_mzpeak_archive(path: Path) -> bool:
    try:
        from mzstack.mzpeak import is_archive
    except ImportError:  # pragma: no cover - core install always has it
        return False
    try:
        return bool(is_archive(path))
    except Exception:  # noqa: BLE001 - a malformed directory is just "not one"
        return False
