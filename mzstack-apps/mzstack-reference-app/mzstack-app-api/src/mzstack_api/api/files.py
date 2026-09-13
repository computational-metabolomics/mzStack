"""Browsing the server's filesystem to pick source files."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..extensions import config
from ..files import list_directory

bp = Blueprint("files", __name__, url_prefix="/api/files")


@bp.get("")
def browse():
    return jsonify(list_directory(config(), request.args.get("path")))
