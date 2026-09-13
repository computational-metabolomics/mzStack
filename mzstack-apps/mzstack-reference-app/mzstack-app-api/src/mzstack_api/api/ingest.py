"""Creating datasets, and looking at a MetaboLights study first.

Every creation endpoint answers 202 with a job: the work outlives the request,
and the `Location` header points at where to watch it.
"""

from __future__ import annotations

from flask import Blueprint, jsonify

from ..errors import BadRequest
from ..extensions import config, jobs, registry
from ..files import KIND_MZML, KIND_MZPEAK, resolve_all
from ..services import ingest as service
from ..services import metabolights as mtbls
from .params import body, bool_arg

bp = Blueprint("ingest", __name__, url_prefix="/api")


def _accepted(job):
    return jsonify(job.as_dict()), 202, {"Location": f"/api/jobs/{job.id}"}


def _names(data: dict, key: str) -> list[str]:
    values = data.get(key) or []
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise BadRequest(f"{key} should be a list of paths.")
    if not values:
        raise BadRequest(f"At least one entry in {key} is required.")
    return values


def _name(data: dict) -> str:
    name = str(data.get("name", "")).strip()
    if not name:
        raise BadRequest("A 'name' for the new dataset is required.")
    return name


@bp.post("/ingest/mzml")
def ingest_mzml():
    data = body()
    files = resolve_all(config(), _names(data, "files"), expect=KIND_MZML)
    job = service.from_mzml(
        jobs(),
        registry(),
        files,
        _name(data),
        partition=bool(data.get("partition", False)),
    )
    return _accepted(job)


@bp.post("/ingest/mzpeak")
def ingest_mzpeak():
    data = body()
    archives = resolve_all(config(), _names(data, "archives"), expect=KIND_MZPEAK)
    job = service.from_mzpeak(
        jobs(),
        registry(),
        archives,
        _name(data),
        link=str(data.get("link", "reference")),
    )
    return _accepted(job)


@bp.post("/ingest/mzpeak/add")
def ingest_mzpeak_add():
    data = body()
    dataset_id = str(data.get("dataset_id", "")).strip()
    if not dataset_id:
        raise BadRequest("A 'dataset_id' to add the archives to is required.")
    archives = resolve_all(config(), _names(data, "archives"), expect=KIND_MZPEAK)
    job = service.add_archives(
        jobs(),
        registry(),
        dataset_id,
        archives,
        link=str(data.get("link", "reference")),
    )
    return _accepted(job)


@bp.get("/metabolights/<study_id>/files")
def metabolights_files(study_id: str):
    return jsonify(mtbls.list_files(study_id, refresh=bool_arg("refresh")))


@bp.post("/ingest/metabolights")
def ingest_metabolights():
    data = body()
    job = service.from_metabolights(
        jobs(),
        registry(),
        study_id=str(data.get("study_id", "")),
        name=data.get("name"),
        assays=data.get("assays"),
        samples=data.get("samples"),
        include=data.get("include"),
        exclude=data.get("exclude"),
    )
    return _accepted(job)
