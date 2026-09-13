"""Spectra: the filtered table, and one spectrum's peaks."""

from __future__ import annotations

from flask import Blueprint, jsonify

from ..extensions import config, registry
from ..services import spectra as service
from .params import float_arg, float_list, int_arg, int_list, str_list

bp = Blueprint("spectra", __name__, url_prefix="/api/datasets")


def _filter() -> service.SpectraFilter:
    """The query string as a filter. Retention time is in seconds."""
    return service.SpectraFilter(
        ms_level=int_list("ms_level"),
        rt_min=float_arg("rt_min"),
        rt_max=float_arg("rt_max"),
        polarity=int_list("polarity"),
        data_origin=str_list("data_origin"),
        precursor_mz=float_list("precursor_mz"),
        precursor_mz_min=float_arg("precursor_mz_min"),
        precursor_mz_max=float_arg("precursor_mz_max"),
        contains_mz=float_list("contains_mz"),
        tolerance=float_arg("tolerance", 0.0),
        ppm=float_arg("ppm", 20.0),
    )


@bp.get("/<dataset_id>/spectra")
def list_spectra(dataset_id: str):
    cfg = config()
    path = registry().path_for(dataset_id)
    limit = min(int_arg("limit", 100), cfg.max_rows)
    offset = max(int_arg("offset", 0), 0)
    return jsonify(
        service.page(
            path,
            _filter(),
            columns=str_list("columns") or None,
            limit=limit,
            offset=offset,
        )
    )


@bp.get("/<dataset_id>/spectra/<int:spectrum_id>")
def get_spectrum(dataset_id: str, spectrum_id: int):
    cfg = config()
    path = registry().path_for(dataset_id)
    max_points = min(int_arg("max_points", cfg.max_points), cfg.max_points)
    return jsonify(service.one(path, spectrum_id, max_points))
