"""MassQL queries."""

from __future__ import annotations

from flask import Blueprint, jsonify

from ..errors import BadRequest
from ..extensions import config, registry
from ..services import query as service
from .params import body, int_field

bp = Blueprint("query", __name__, url_prefix="/api/datasets")


@bp.post("/<dataset_id>/query")
def run_query(dataset_id: str):
    cfg = config()
    path = registry().path_for(dataset_id)
    data = body()
    rt = data.get("rt") or [None, None]
    offset = int_field(data, "offset", 0)
    if offset < 0:
        raise BadRequest(f"offset must not be negative, not {offset}")
    return jsonify(
        service.run(
            path,
            query=str(data.get("query", "")),
            engine=str(data.get("engine", service.ENGINES[0])),
            ms_level=data.get("ms_level"),
            rt_min=rt[0],
            rt_max=rt[1],
            limit=min(int_field(data, "limit", cfg.max_rows), cfg.max_rows),
            offset=offset,
        )
    )
