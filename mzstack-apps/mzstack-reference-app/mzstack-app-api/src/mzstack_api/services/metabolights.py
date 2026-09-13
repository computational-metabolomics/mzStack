"""Looking at a MetaboLights study before committing to downloading it.

Listing is cheap -- only the study's ISA-Tab is transferred, not its spectra --
so it happens inside the request, unlike the ingest it precedes. It lets the
user see the assays and samples on offer and pick a subset, rather than
guessing at glob patterns and waiting for a download to find out.
"""

from __future__ import annotations

from typing import Any

from ..errors import BadRequest

__all__ = ["list_files"]


def list_files(study_id: str, refresh: bool = False) -> dict[str, Any]:
    """Every mzML file a study references, grouped for a picker."""
    study = study_id.strip().strip("/").upper()
    if not study:
        raise BadRequest("A MetaboLights accession is required, e.g. MTBLS341.")

    # Import here: metabolights-utils is an optional extra, and the rest of
    # the API must work without it. A missing install raises StudyError with
    # the install command, which the error handler passes straight through.
    from mzstack.ingest import list_study_files

    files = list_study_files(study, refresh=refresh)
    return {
        "study_id": study,
        "n_files": len(files),
        "assays": sorted({f.assay for f in files}),
        "files": [
            {
                "assay": f.assay,
                "remote_path": f.remote_path,
                "name": f.name,
                "sample_name": f.sample_name,
                "assay_name": f.assay_name,
                "column": f.column,
            }
            for f in files
        ],
    }
