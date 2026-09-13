"""Reading query-string and JSON parameters, with errors a user can act on."""

from __future__ import annotations

from typing import Any

from flask import request

from ..errors import BadRequest

__all__ = [
    "body",
    "bool_arg",
    "float_arg",
    "float_list",
    "int_arg",
    "int_field",
    "int_list",
    "str_list",
]


def body() -> dict[str, Any]:
    """The request's JSON object."""
    data = request.get_json(silent=True)
    if data is None:
        raise BadRequest("A JSON body is required.")
    if not isinstance(data, dict):
        raise BadRequest("The JSON body should be an object.")
    return data


def int_field(data: dict[str, Any], name: str, default: int) -> int:
    """A whole number from a JSON body, as `int_arg` is from a query string.

    A body is JSON, so a client can send `{"limit": "50"}` or `{"limit": null}`
    as easily as a number; without this they reach `int()` and come back as a
    500 rather than as the 400 they are.
    """
    raw = data.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise BadRequest(f"{name} must be a whole number, not {raw!r}") from exc


def int_arg(name: str, default: int | None = None) -> int | None:
    raw = request.args.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise BadRequest(f"{name} must be a whole number, not {raw!r}") from exc


def float_arg(name: str, default: float | None = None) -> float | None:
    raw = request.args.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise BadRequest(f"{name} must be a number, not {raw!r}") from exc


def bool_arg(name: str, default: bool = False) -> bool:
    raw = request.args.get(name)
    if raw is None or raw == "":
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def _split(name: str) -> list[str]:
    """Repeated parameters and comma-separated ones, treated alike."""
    values: list[str] = []
    for raw in request.args.getlist(name):
        values.extend(part.strip() for part in raw.split(",") if part.strip())
    return values


def str_list(name: str) -> list[str]:
    return _split(name)


def int_list(name: str) -> list[int]:
    out = []
    for value in _split(name):
        try:
            out.append(int(value))
        except ValueError as exc:
            raise BadRequest(
                f"{name} must be whole numbers, not {value!r}"
            ) from exc
    return out


def float_list(name: str) -> list[float]:
    out = []
    for value in _split(name):
        try:
            out.append(float(value))
        except ValueError as exc:
            raise BadRequest(f"{name} must be numbers, not {value!r}") from exc
    return out
