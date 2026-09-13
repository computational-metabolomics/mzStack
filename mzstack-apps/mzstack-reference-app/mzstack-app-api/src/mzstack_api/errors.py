"""API errors, and the mapping from library errors onto status codes.

`mzstack` already raises messages written for a person to read -- the CLI
prints them verbatim -- so the handlers pass them through rather than
inventing their own.
"""

from __future__ import annotations

import logging
from typing import Any

from flask import Flask, jsonify
from mzstack.errors import (
    ArchiveError,
    FormatError,
    MzStackError,
    NotTranslatable,
    StudyError,
)
from werkzeug.exceptions import HTTPException

__all__ = ["ApiError", "BadRequest", "Forbidden", "NotFound", "register_error_handlers"]

log = logging.getLogger(__name__)


class ApiError(Exception):
    """An error the client caused, with the status code to report it as."""

    status = 400

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        if status is not None:
            self.status = status


class BadRequest(ApiError):
    status = 400


class Forbidden(ApiError):
    status = 403


class NotFound(ApiError):
    status = 404


def _payload(type_: str, message: str) -> dict[str, Any]:
    return {"error": {"type": type_, "message": message}}


def register_error_handlers(app: Flask) -> None:
    """Make every failure a JSON body of the same shape."""

    @app.errorhandler(ApiError)
    def _api_error(exc: ApiError):
        return jsonify(_payload(type(exc).__name__, exc.message)), exc.status

    @app.errorhandler(FormatError)
    def _format_error(exc: FormatError):
        # A path that holds no manifest: the dataset the client named is not
        # there, which is a 404 rather than a malformed request.
        return jsonify(_payload("FormatError", str(exc))), 404

    @app.errorhandler(FileNotFoundError)
    def _missing_file(exc: FileNotFoundError):
        return jsonify(_payload("FileNotFoundError", str(exc))), 404

    @app.errorhandler(NotTranslatable)
    def _not_translatable(exc: NotTranslatable):
        return jsonify(_payload("NotTranslatable", str(exc))), 400

    @app.errorhandler(ArchiveError)
    def _archive_error(exc: ArchiveError):
        return jsonify(_payload("ArchiveError", str(exc))), 400

    @app.errorhandler(StudyError)
    def _study_error(exc: StudyError):
        return jsonify(_payload("StudyError", str(exc))), 400

    @app.errorhandler(MzStackError)
    def _mzstack_error(exc: MzStackError):
        # Unknown variable names, missing optional extras, bad filters.
        return jsonify(_payload("MzStackError", str(exc))), 400

    @app.errorhandler(ValueError)
    def _value_error(exc: ValueError):
        return jsonify(_payload("ValueError", str(exc))), 400

    @app.errorhandler(HTTPException)
    def _http_error(exc: HTTPException):
        return jsonify(_payload(exc.name, exc.description or exc.name)), exc.code

    @app.errorhandler(Exception)
    def _unexpected(exc: Exception):
        log.exception("unhandled error")
        return jsonify(_payload("InternalError", str(exc))), 500
