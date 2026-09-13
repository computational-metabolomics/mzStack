"""HTTP blueprints.

Each module here parses a request, calls one service and returns JSON. Any
mass-spectrometry logic that appears in this package is in the wrong place.
"""

from __future__ import annotations

from flask import Flask

from . import chromatograms, datasets, files, health, ingest, jobs, query, spectra

BLUEPRINTS = (
    health.bp,
    datasets.bp,
    spectra.bp,
    chromatograms.bp,
    query.bp,
    ingest.bp,
    jobs.bp,
    files.bp,
)


def register_blueprints(app: Flask) -> None:
    for bp in BLUEPRINTS:
        app.register_blueprint(bp)
