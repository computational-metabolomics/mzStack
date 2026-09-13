"""Dataset listing, overview, samples and projections."""

from __future__ import annotations

from flask import Blueprint, jsonify

from ..errors import BadRequest
from ..extensions import jobs, registry
from ..services import datasets as service
from ..services import ingest
from .params import body

bp = Blueprint("datasets", __name__, url_prefix="/api/datasets")


@bp.get("")
def list_datasets():
    entries = registry().entries()
    out = []
    for entry in entries:
        record = entry.as_dict()
        try:
            record.update(service.summarise(entry.path))
        except Exception as exc:  # noqa: BLE001
            # One unreadable dataset must not empty the whole list: report it
            # in place so the user can see which one needs attention.
            record["error"] = str(exc)
        out.append(record)
    return jsonify({"datasets": out})


@bp.post("/register")
def register_dataset():
    data = body()
    path = data.get("path")
    if not path:
        raise BadRequest("A 'path' to an existing mzStack dataset is required.")
    entry = registry().register(path)
    return jsonify(entry.as_dict()), 201


@bp.delete("/<dataset_id>/register")
def unregister_dataset(dataset_id: str):
    registry().unregister(dataset_id)
    return "", 204


@bp.get("/<dataset_id>")
def get_dataset(dataset_id: str):
    entry = registry().resolve(dataset_id)
    return jsonify({**entry.as_dict(), **service.describe(entry.path)})


@bp.get("/<dataset_id>/samples")
def get_samples(dataset_id: str):
    path = registry().path_for(dataset_id)
    return jsonify(service.sample_metadata(path))


@bp.get("/<dataset_id>/projections")
def get_projections(dataset_id: str):
    path = registry().path_for(dataset_id)
    return jsonify(service.projections(path))


@bp.post("/<dataset_id>/projections")
def build_projection(dataset_id: str):
    data = body()
    job = ingest.build_projection_job(
        jobs(), registry(), dataset_id, data.get("type", "scansorted")
    )
    return jsonify(job.as_dict()), 202, {"Location": f"/api/jobs/{job.id}"}


@bp.delete("/<dataset_id>/projections/<type_>")
def drop_projection(dataset_id: str, type_: str):
    return jsonify(ingest.drop_projection_now(registry(), dataset_id, type_))
