"""Dataset discovery, identity and the overview endpoint."""

from __future__ import annotations

from pathlib import Path

from mzstack import open_dataset
from mzstack.ingest import mzml_to_mzstack


def test_health_reports_the_workspace(client, workspace):
    body = client.get("/api/health").get_json()
    assert body["status"] == "ok"
    assert body["workspace"] == str(workspace)


def test_workspace_datasets_are_listed_without_registration(client, dataset):
    body = client.get("/api/datasets").get_json()
    assert [d["id"] for d in body["datasets"]] == ["simple"]
    entry = body["datasets"][0]
    assert entry["source"] == "workspace"
    assert entry["kind"] == "native"
    assert entry["n_spectra"] == 4


def test_a_dataset_created_while_running_appears(client, workspace, source_mzml):
    assert client.get("/api/datasets").get_json()["datasets"] == []
    mzml_to_mzstack(source_mzml, workspace / "late")
    body = client.get("/api/datasets").get_json()
    assert [d["id"] for d in body["datasets"]] == ["late"]


def test_overview_agrees_with_the_library(client, dataset):
    body = client.get("/api/datasets/simple").get_json()
    store = open_dataset(dataset)

    assert body["n_spectra"] == store.manifest.n_spectra == 4
    assert body["kind"] == store.kind
    assert body["ms_levels"] == {"1": 2, "2": 2}
    assert body["polarities"] == [1]
    assert body["variables"] == store.spectra_variables()

    # Retention time is reported in seconds, as the mzStack view defines it.
    df = store.spectra_data(columns=["rtime"])
    assert body["rtime_range"] == [df["rtime"].min(), df["rtime"].max()]
    assert body["rtime_range"] == [60.0, 78.0]


def test_sample_count_is_distinct_origins_not_runs(client, two_run_dataset):
    """Several mzML files convert into one run, so runs cannot count samples."""
    body = client.get("/api/datasets/paired").get_json()

    assert body["n_samples"] == 2
    assert len(body["runs"]) == 1, "one conversion, one run — this is the point"


def test_a_single_file_dataset_has_one_sample(client, dataset):
    assert client.get("/api/datasets/simple").get_json()["n_samples"] == 1


def test_unknown_dataset_is_a_404(client):
    response = client.get("/api/datasets/nope")
    assert response.status_code == 404
    assert response.get_json()["error"]["type"] == "NotFound"


def test_registering_a_dataset_outside_the_workspace(client, tmp_path, source_mzml):
    outside = tmp_path / "elsewhere" / "other"
    mzml_to_mzstack(source_mzml, outside)

    response = client.post("/api/datasets/register", json={"path": str(outside)})
    assert response.status_code == 201
    assert response.get_json()["id"] == "other"

    listed = client.get("/api/datasets").get_json()["datasets"]
    assert {d["id"]: d["source"] for d in listed} == {"other": "registered"}

    # Registering twice is idempotent rather than an error.
    again = client.post("/api/datasets/register", json={"path": str(outside)})
    assert again.status_code == 201
    assert len(client.get("/api/datasets").get_json()["datasets"]) == 1


def test_registering_a_non_dataset_is_rejected(client, tmp_path):
    plain = tmp_path / "not-a-dataset"
    plain.mkdir()
    response = client.post("/api/datasets/register", json={"path": str(plain)})
    assert response.status_code == 400
    assert "mzStack.json" in response.get_json()["error"]["message"]


def test_unregistering_leaves_the_directory_alone(client, tmp_path, source_mzml):
    outside = tmp_path / "elsewhere" / "other"
    mzml_to_mzstack(source_mzml, outside)
    client.post("/api/datasets/register", json={"path": str(outside)})

    assert client.delete("/api/datasets/other/register").status_code == 204
    assert client.get("/api/datasets").get_json()["datasets"] == []
    assert (Path(outside) / "mzStack.json").is_file()


def test_a_broken_dataset_does_not_empty_the_listing(client, workspace, dataset):
    broken = workspace / "broken"
    broken.mkdir()
    (broken / "mzStack.json").write_text("{ not json", encoding="utf-8")

    listed = client.get("/api/datasets").get_json()["datasets"]
    by_id = {d["id"]: d for d in listed}
    assert set(by_id) == {"simple", "broken"}
    assert "error" in by_id["broken"]
    assert by_id["simple"]["n_spectra"] == 4


def test_projections_report_their_state(client, dataset):
    body = client.get("/api/datasets/simple/projections").get_json()
    assert set(body["types"]) == {"scansorted", "mzsorted"}
    assert body["current"]["mzsorted"]["complete"] is False
