"""Creating datasets through the API, end to end."""

from __future__ import annotations

import time

from mzstack import is_mzstack_dataset


def _await(client, job_id: str, timeout: float = 60.0) -> dict:
    """Poll a job the way the UI does, until it stops running."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/jobs/{job_id}").get_json()
        if body["status"] in ("succeeded", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def test_mzml_ingest_creates_a_usable_dataset(client, workspace, source_mzml):
    response = client.post(
        "/api/ingest/mzml", json={"files": [str(source_mzml)], "name": "converted"}
    )
    assert response.status_code == 202
    job = response.get_json()
    assert response.headers["Location"] == f"/api/jobs/{job['id']}"

    finished = _await(client, job["id"])
    assert finished["status"] == "succeeded", finished["error"]
    assert finished["result"]["dataset_id"] == "converted"
    assert is_mzstack_dataset(workspace / "converted")

    # The new dataset is immediately listable and readable.
    listed = client.get("/api/datasets").get_json()["datasets"]
    assert "converted" in {d["id"] for d in listed}

    overview = client.get("/api/datasets/converted").get_json()
    assert overview["n_spectra"] == 4
    assert overview["ms_levels"] == {"1": 2, "2": 2}

    spectrum = client.get("/api/datasets/converted/spectra/1").get_json()
    assert spectrum["n_peaks"] == 3


def test_ingest_progress_is_captured_as_a_log(client, source_mzml):
    job = client.post(
        "/api/ingest/mzml", json={"files": [str(source_mzml)], "name": "logged"}
    ).get_json()
    finished = _await(client, job["id"])
    # mzstack reports progress on stdout; it belongs to the job, not the
    # server's console.
    assert any("Converting" in line for line in finished["log"])


def test_a_failing_ingest_reports_the_error_rather_than_crashing(
    client, workspace, source_mzml
):
    (workspace / "taken").mkdir()
    response = client.post(
        "/api/ingest/mzml", json={"files": [str(source_mzml)], "name": "taken"}
    )
    # The clash is caught before the job is queued: the name is checked when
    # the destination is chosen.
    assert response.status_code == 400
    assert "already exists" in response.get_json()["error"]["message"]


def test_a_file_that_is_not_really_mzml_warns_instead_of_failing(
    client, workspace, tmp_path
):
    """An ingest that reads nothing succeeds, reporting a count of zero.

    `mzml_to_mzstack` looks for spectrum elements and finds none; it does not
    raise. The response carries the count and a warning.
    """
    fake = tmp_path / "broken.mzML"
    fake.write_text("<not-mzml/>", encoding="utf-8")

    job = client.post(
        "/api/ingest/mzml", json={"files": [str(fake)], "name": "empty"}
    ).get_json()
    finished = _await(client, job["id"])

    assert finished["status"] == "succeeded"
    assert finished["result"]["n_spectra"] == 0
    assert any("read no spectra" in line for line in finished["log"])


def test_a_job_that_raises_is_recorded_as_failed(client):
    """The registry's failure path, exercised directly.

    No ingest in the library reliably raises on bad input, so the behaviour
    that matters -- a raising job becomes a readable error rather than a dead
    thread -- is tested at the level where it lives.
    """
    from mzstack_api.extensions import KEY

    registry = client.application.extensions[KEY].jobs

    def explode(job):
        print("about to fail")
        raise RuntimeError("no good")

    job = registry.submit("test", {}, explode)
    finished = _await(client, job.id)

    assert finished["status"] == "failed"
    assert finished["error"] == "no good"
    assert finished["finished_at"] is not None
    # Output produced before the failure is kept: it is usually the clue.
    assert "about to fail" in finished["log"]


def test_missing_dataset_name_is_rejected(client, source_mzml):
    response = client.post("/api/ingest/mzml", json={"files": [str(source_mzml)]})
    assert response.status_code == 400
    assert "name" in response.get_json()["error"]["message"]


def test_jobs_are_listed_newest_first(client, source_mzml):
    first = client.post(
        "/api/ingest/mzml", json={"files": [str(source_mzml)], "name": "one"}
    ).get_json()
    _await(client, first["id"])
    second = client.post(
        "/api/ingest/mzml", json={"files": [str(source_mzml)], "name": "two"}
    ).get_json()
    _await(client, second["id"])

    listed = client.get("/api/jobs").get_json()["jobs"]
    assert [j["id"] for j in listed[:2]] == [second["id"], first["id"]]


def test_unknown_job_is_a_404(client):
    assert client.get("/api/jobs/nope").status_code == 404


def test_projection_can_be_built_and_dropped(client, dataset):
    job = client.post(
        "/api/datasets/simple/projections", json={"type": "mzsorted"}
    ).get_json()
    finished = _await(client, job["id"])
    assert finished["status"] == "succeeded", finished["error"]

    state = client.get("/api/datasets/simple/projections").get_json()
    assert state["current"]["mzsorted"]["complete"] is True

    # Results are identical with the projection in place; only speed changes.
    body = client.get(
        "/api/datasets/simple/spectra?contains_mz=226.18&tolerance=0.01"
    ).get_json()
    assert [r["spectrum_id_"] for r in body["rows"]] == [3, 4]

    assert client.delete("/api/datasets/simple/projections/mzsorted").status_code == 200
    state = client.get("/api/datasets/simple/projections").get_json()
    assert state["current"]["mzsorted"]["complete"] is False


def test_unknown_projection_type_is_rejected(client, dataset):
    response = client.post(
        "/api/datasets/simple/projections", json={"type": "sideways"}
    )
    assert response.status_code == 400
