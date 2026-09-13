"""Fixtures: a real dataset, in a temporary workspace, behind a test client."""

from __future__ import annotations

from pathlib import Path

import pytest
from mzml_builder import SPECTRA, write_simple
from mzstack import duck
from mzstack.ingest import mzml_to_mzstack

from mzstack_api import create_app
from mzstack_api.config import Config

__all__ = ["SPECTRA"]


@pytest.fixture(autouse=True)
def _reset_duckdb():
    """A fresh connection and view registry per test.

    Views are cached per dataset path for the life of the process, and
    `tmp_path` directories are reused across a session often enough that a
    stale view would otherwise leak between tests. Mirrors mzstack-py's own
    fixture.
    """
    duck.close()
    yield
    duck.close()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def source_mzml(tmp_path: Path) -> Path:
    return write_simple(tmp_path / "simple.mzML")


@pytest.fixture
def dataset(workspace: Path, source_mzml: Path) -> Path:
    """A four-spectrum native dataset sitting in the workspace."""
    path = workspace / "simple"
    mzml_to_mzstack(source_mzml, path)
    return path


@pytest.fixture
def two_run_dataset(workspace: Path, tmp_path: Path) -> Path:
    """The same four spectra ingested twice, as two runs in one dataset.

    Two `dataOrigin` values is the smallest thing that makes a per-sample
    split observable; the identical signal is deliberate, so a test can assert
    that the two series partition the single-trace result exactly.
    """
    sources = [
        write_simple(tmp_path / "sample_a.mzML"),
        write_simple(tmp_path / "sample_b.mzML"),
    ]
    path = workspace / "paired"
    mzml_to_mzstack(sources, path)
    return path


@pytest.fixture
def config(workspace: Path, tmp_path: Path) -> Config:
    return Config(workspace=workspace, data_roots=(workspace, tmp_path))


@pytest.fixture
def app(config: Config):
    application = create_app(config)
    application.config.update(TESTING=True)
    return application


@pytest.fixture
def client(app):
    return app.test_client()
