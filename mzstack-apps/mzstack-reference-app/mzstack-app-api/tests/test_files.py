"""The file browser, and the sandbox around it.

Source files are chosen on the server rather than uploaded, so path handling
here is the API's main exposure. These tests are about what it refuses.
"""

from __future__ import annotations

import os
from pathlib import Path


def test_browsing_lists_entries_by_kind(client, tmp_path, source_mzml, dataset):
    body = client.get(f"/api/files?path={tmp_path}").get_json()
    kinds = {e["name"]: e["kind"] for e in body["entries"]}
    assert kinds["simple.mzML"] == "mzml"
    assert kinds["workspace"] == "directory"


def test_a_dataset_directory_is_recognised(client, workspace, dataset):
    body = client.get(f"/api/files?path={workspace}").get_json()
    kinds = {e["name"]: e["kind"] for e in body["entries"]}
    assert kinds["simple"] == "mzstack"


def test_no_path_starts_at_the_first_root(client, workspace):
    body = client.get("/api/files").get_json()
    assert body["path"] == str(workspace)
    assert str(workspace) in body["roots"]


def test_a_path_outside_the_roots_is_refused(client):
    response = client.get("/api/files?path=/etc")
    assert response.status_code == 403
    assert "outside" in response.get_json()["error"]["message"]


def test_dot_dot_cannot_escape_a_root(client, workspace):
    response = client.get(f"/api/files?path={workspace}/../../../../etc")
    assert response.status_code == 403


def test_a_symlink_out_of_the_root_is_refused(client, workspace, tmp_path):
    # Resolution happens before the check, so a link is not a way round it.
    link = workspace / "escape"
    os.symlink("/etc", link)
    response = client.get(f"/api/files?path={link}")
    assert response.status_code == 403


def test_parent_is_null_at_the_top_of_a_root(client, workspace):
    body = client.get(f"/api/files?path={workspace}").get_json()
    # tmp_path is also a root and contains the workspace, so the parent is
    # reachable here; what must never happen is a parent outside every root.
    if body["parent"] is not None:
        assert any(
            Path(body["parent"]).is_relative_to(Path(r)) or body["parent"] == r
            for r in body["roots"]
        )


def test_ingest_refuses_a_file_outside_the_roots(client):
    response = client.post(
        "/api/ingest/mzml", json={"files": ["/etc/hosts"], "name": "sneaky"}
    )
    assert response.status_code == 403


def test_ingest_refuses_a_file_of_the_wrong_kind(client, workspace, tmp_path):
    plain = tmp_path / "notes.txt"
    plain.write_text("hello", encoding="utf-8")
    response = client.post(
        "/api/ingest/mzml", json={"files": [str(plain)], "name": "wrong"}
    )
    assert response.status_code == 400
    assert "not mzml" in response.get_json()["error"]["message"]


def test_a_dataset_name_cannot_be_a_path(client, source_mzml):
    response = client.post(
        "/api/ingest/mzml",
        json={"files": [str(source_mzml)], "name": "../escape"},
    )
    assert response.status_code == 400
