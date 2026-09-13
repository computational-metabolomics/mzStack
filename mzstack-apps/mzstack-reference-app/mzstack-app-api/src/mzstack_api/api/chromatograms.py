"""Chromatogram extraction."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..extensions import registry
from ..services import chromatograms as service
from .params import float_arg, int_arg

bp = Blueprint("chromatograms", __name__, url_prefix="/api/datasets")


@bp.get("/<dataset_id>/chromatogram")
def get_chromatogram(dataset_id: str):
    path = registry().path_for(dataset_id)
    return jsonify(
        service.extract(
            path,
            type_=request.args.get("type", service.TIC).lower(),
            ms_level=int_arg("ms_level", 1),
            rt_min=float_arg("rt_min"),
            rt_max=float_arg("rt_max"),
            mz=float_arg("mz"),
            tolerance=float_arg("tolerance", 0.0),
            ppm=float_arg("ppm", 20.0),
            group_by=request.args.get("group_by") or None,
        )
    )
