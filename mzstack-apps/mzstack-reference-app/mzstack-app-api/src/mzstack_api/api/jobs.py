"""Background job status."""

from __future__ import annotations

from flask import Blueprint, jsonify

from ..extensions import jobs

bp = Blueprint("jobs", __name__, url_prefix="/api/jobs")


@bp.get("")
def list_jobs():
    return jsonify({"jobs": [job.as_dict() for job in jobs().all()]})


@bp.get("/<job_id>")
def get_job(job_id: str):
    return jsonify(jobs().get(job_id).as_dict())
