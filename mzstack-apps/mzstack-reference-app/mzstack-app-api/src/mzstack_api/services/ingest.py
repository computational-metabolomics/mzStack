"""Creating datasets.

Every function here builds the work as a closure and hands it to the job
registry. None of them finishes inside a request: converting mzML is minutes
of parsing, and a MetaboLights study downloads over FTP.

All three delegate to `mzstack.ingest`. Nothing about the mzStack format is
reimplemented -- these functions choose the destination, run the library's
converter, and invalidate the reader's cached view afterwards.

**On locking.** `store.dataset_lock` guards the shared DuckDB connection, and
an ingest takes it only if it uses that connection. Converting mzML writes
Parquet through pyarrow and never touches DuckDB, so those jobs run unlocked.
Indexing mzPeak archives and building projections query DuckDB and take the
lock.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mzstack.projections import PROJECTION_TYPES

from ..errors import BadRequest
from ..jobs import Job, JobRegistry
from ..registry import Registry
from ..store import dataset_lock, invalidate

__all__ = [
    "from_metabolights",
    "from_mzml",
    "from_mzpeak",
    "add_archives",
    "build_projection_job",
    "drop_projection_now",
]


def from_mzml(
    jobs: JobRegistry,
    registry: Registry,
    files: list[Path],
    name: str,
    partition: bool = False,
) -> Job:
    """Convert mzML files into a new native dataset in the workspace."""
    target = registry.workspace_path_for(name)
    partitioning = ("dataOrigin",) if partition else ()

    def work(job: Job) -> dict[str, Any]:
        from mzstack.ingest import mzml_to_mzstack

        print(f"Converting {len(files)} mzML file(s) into {target}")
        # Unlocked: pyarrow writes the Parquet, DuckDB is not involved.
        mzml_to_mzstack(
            [str(f) for f in files],
            target,
            partitioning=partitioning,
            verbose=True,
        )
        invalidate(target)
        return _created(registry, target)

    return jobs.submit(
        "mzml",
        {"files": [str(f) for f in files], "name": name, "partition": partition},
        work,
    )


def from_mzpeak(
    jobs: JobRegistry,
    registry: Registry,
    archives: list[Path],
    name: str,
    link: str = "reference",
) -> Job:
    """Index mzPeak archives as a new dataset.

    With ``link="reference"`` -- the default -- the archives stay where they
    are and are never modified; only an index is written.
    """
    _check_link(link)
    target = registry.workspace_path_for(name)

    def work(job: Job) -> dict[str, Any]:
        from mzstack.ingest import create_mzpeak_dataset

        print(f"Indexing {len(archives)} archive(s) into {target} (link={link})")
        with dataset_lock:
            create_mzpeak_dataset(
                [str(a) for a in archives], target, link=link, verbose=True
            )
        invalidate(target)
        return _created(registry, target)

    return jobs.submit(
        "mzpeak",
        {"archives": [str(a) for a in archives], "name": name, "link": link},
        work,
    )


def add_archives(
    jobs: JobRegistry,
    registry: Registry,
    dataset_id: str,
    archives: list[Path],
    link: str = "reference",
) -> Job:
    """Add mzPeak archives to a dataset that already exists."""
    _check_link(link)
    target = registry.path_for(dataset_id)

    def work(job: Job) -> dict[str, Any]:
        from mzstack.ingest import add_mzpeak_archives

        print(f"Adding {len(archives)} archive(s) to {target}")
        with dataset_lock:
            add_mzpeak_archives(
                target, [str(a) for a in archives], link=link, verbose=True
            )
        invalidate(target)
        return {"dataset_id": dataset_id, "path": str(target)}

    return jobs.submit(
        "mzpeak-add",
        {
            "dataset_id": dataset_id,
            "archives": [str(a) for a in archives],
            "link": link,
        },
        work,
    )


def from_metabolights(
    jobs: JobRegistry,
    registry: Registry,
    study_id: str,
    name: str | None = None,
    assays: list[str] | None = None,
    samples: list[str] | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> Job:
    """Download a MetaboLights study's mzML and convert it.

    The study's ISA-Tab annotation is written alongside as sample metadata, so
    the resulting dataset knows which sample each spectrum came from.
    """
    study = study_id.strip().strip("/").upper()
    if not study:
        raise BadRequest("A MetaboLights accession is required, e.g. MTBLS341.")
    target = registry.workspace_path_for(name or study)

    def work(job: Job) -> dict[str, Any]:
        from mzstack.ingest import metabolights_to_mzstack

        print(f"Fetching {study} into {target}")
        # Unlocked: this downloads over FTP and then converts mzML, neither of
        # which touches DuckDB. Other datasets stay readable while it runs.
        metabolights_to_mzstack(
            study,
            target,
            assays=tuple(assays or ()),
            samples=tuple(samples or ()),
            include=tuple(include or ()),
            exclude=tuple(exclude or ()),
            verbose=True,
        )
        invalidate(target)
        return _created(registry, target)

    return jobs.submit(
        "metabolights",
        {
            "study_id": study,
            "name": name or study,
            "assays": assays or [],
            "samples": samples or [],
            "include": include or [],
            "exclude": exclude or [],
        },
        work,
    )


def build_projection_job(
    jobs: JobRegistry, registry: Registry, dataset_id: str, type_: str
) -> Job:
    """Build a signal projection. Slow and disk-hungry, so it is a job."""
    _check_projection(type_)
    target = registry.path_for(dataset_id)

    def work(job: Job) -> dict[str, Any]:
        from mzstack import build_projection

        print(f"Building the {type_} projection for {target}")
        with dataset_lock:
            build_projection(target, type=type_, verbose=True)
        invalidate(target)
        return {"dataset_id": dataset_id, "type": type_}

    return jobs.submit(
        "projection", {"dataset_id": dataset_id, "type": type_}, work
    )


def drop_projection_now(registry: Registry, dataset_id: str, type_: str) -> dict:
    """Delete a projection. Fast enough to do inside the request."""
    _check_projection(type_)
    target = registry.path_for(dataset_id)
    from mzstack import drop_projection

    with dataset_lock:
        drop_projection(target, type=type_, verbose=False)
    invalidate(target)
    return {"dataset_id": dataset_id, "type": type_}


def _created(registry: Registry, target: Path) -> dict[str, Any]:
    """Identify a freshly built dataset the way the listing will.

    The spectrum count comes back with it. An ingest that read nothing still
    succeeds -- a file that is not really mzML yields an empty dataset rather
    than an error -- so a count of zero is what reports that.
    """
    from mzstack.format.manifest import Manifest

    from ..registry import slugify

    n_spectra = Manifest.read(target).n_spectra
    if n_spectra == 0:
        print(
            "Warning: the ingest completed but read no spectra. Check that "
            "the input files are what you expected."
        )
    return {
        "dataset_id": slugify(target.name),
        "path": str(target),
        "n_spectra": n_spectra,
    }


def _check_link(link: str) -> None:
    if link not in ("reference", "copy"):
        raise BadRequest(f"link must be 'reference' or 'copy', not {link!r}")


def _check_projection(type_: str) -> None:
    if type_ not in PROJECTION_TYPES:
        raise BadRequest(
            f"Unknown projection {type_!r}; expected one of "
            + ", ".join(PROJECTION_TYPES)
        )
