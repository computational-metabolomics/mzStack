"""What this server is, and which optional features it can offer.

The UI reads `extras` to decide whether to show the query and MetaboLights
pages at all, rather than letting the user fill in a form that can only fail.
"""

from __future__ import annotations

import importlib.util

from flask import Blueprint, jsonify
from mzstack import FORMAT_VERSION
from mzstack import __version__ as mzstack_version

from ..extensions import config

bp = Blueprint("health", __name__, url_prefix="/api")


def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


@bp.get("/health")
def health():
    cfg = config()
    return jsonify(
        {
            "status": "ok",
            "mzstack_version": mzstack_version,
            "format_version": FORMAT_VERSION,
            "workspace": str(cfg.workspace),
            "data_roots": [str(r) for r in cfg.data_roots],
            "extras": {
                "massql": _installed("massql"),
                "metabolights": _installed("metabolights_utils"),
                "mzpeak": _installed("mzpeak"),
            },
            "limits": {
                "max_points": cfg.max_points,
                "max_rows": cfg.max_rows,
            },
        }
    )
