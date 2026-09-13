"""Chromatogram extraction -- the one place the API computes rather than relays."""

from __future__ import annotations

import pytest
from mzstack import open_dataset


def test_tic_matches_the_stored_total_ion_current(client, dataset):
    body = client.get("/api/datasets/simple/chromatogram?type=tic").get_json()
    expected = (
        open_dataset(dataset)
        .filter_ms_level(1)
        .spectra_data(columns=["rtime", "totIonCurrent"])
    )

    assert body["rtime"] == expected["rtime"].tolist()
    assert body["intensity"] == expected["totIonCurrent"].tolist()
    assert body["spectrum_ids"] == [1, 3]
    # MS1 only: the MS2 scans at 66 s and 78 s are not part of a TIC.
    assert body["n_points"] == 2


def test_bpc_uses_the_base_peak_column(client, dataset):
    body = client.get("/api/datasets/simple/chromatogram?type=bpc").get_json()
    assert body["intensity"] == [50.0, 50.0]


def test_ms_level_selects_which_scans_are_summarised(client, dataset):
    body = client.get(
        "/api/datasets/simple/chromatogram?type=tic&ms_level=2"
    ).get_json()
    assert body["spectrum_ids"] == [2, 4]


def test_retention_time_window_trims_the_trace(client, dataset):
    body = client.get(
        "/api/datasets/simple/chromatogram?type=tic&rt_min=70&rt_max=80"
    ).get_json()
    assert body["spectrum_ids"] == [3]


def test_xic_sums_intensity_inside_the_window(client, dataset):
    body = client.get(
        "/api/datasets/simple/chromatogram"
        "?type=xic&mz=226.18&tolerance=0.01&ms_level=1"
    ).get_json()

    # Only the second MS1 scan holds 226.18, at intensity 30.
    assert body["spectrum_ids"] == [1, 3]
    assert body["intensity"] == [0.0, 30.0]
    assert body["n_matching"] == 1
    assert body["mz_window"] == pytest.approx([226.17, 226.19])


def test_xic_keeps_non_matching_scans_as_baseline(client, dataset):
    """A trace of matches alone would draw apex-to-apex across the gaps."""
    body = client.get(
        "/api/datasets/simple/chromatogram?type=xic&mz=226.18&tolerance=0.01"
    ).get_json()
    assert len(body["rtime"]) == len(body["intensity"]) == 2
    assert body["intensity"][0] == 0.0


def test_xic_for_an_absent_mz_is_flat_rather_than_empty(client, dataset):
    body = client.get(
        "/api/datasets/simple/chromatogram?type=xic&mz=999.9&tolerance=0.01"
    ).get_json()
    assert body["intensity"] == [0.0, 0.0]
    assert body["n_matching"] == 0


def test_xic_without_an_mz_is_rejected(client, dataset):
    response = client.get("/api/datasets/simple/chromatogram?type=xic")
    assert response.status_code == 400
    assert "mz" in response.get_json()["error"]["message"]


def test_unknown_chromatogram_type_is_rejected(client, dataset):
    response = client.get("/api/datasets/simple/chromatogram?type=wat")
    assert response.status_code == 400
    assert "tic" in response.get_json()["error"]["message"]


def test_inverted_retention_time_window_is_rejected(client, dataset):
    response = client.get(
        "/api/datasets/simple/chromatogram?type=tic&rt_min=90&rt_max=10"
    )
    assert response.status_code == 400


# --- Splitting per sample -------------------------------------------------


def test_grouped_tic_returns_one_series_per_sample(client, two_run_dataset):
    body = client.get(
        "/api/datasets/paired/chromatogram?type=tic&group_by=dataOrigin"
    ).get_json()

    assert body["group_by"] == "dataOrigin"
    assert len(body["series"]) == 2
    assert [s["name"] for s in body["series"]] == sorted(
        s["name"] for s in body["series"]
    )
    # Both runs hold the same two MS1 scans.
    assert [s["n_points"] for s in body["series"]] == [2, 2]
    for series in body["series"]:
        assert series["intensity"] == [100.0, 100.0]


def test_grouped_series_partition_the_ungrouped_trace(client, two_run_dataset):
    """The split is a partition: same scans, sorted into series."""
    flat = client.get("/api/datasets/paired/chromatogram?type=tic").get_json()
    grouped = client.get(
        "/api/datasets/paired/chromatogram?type=tic&group_by=dataOrigin"
    ).get_json()

    a, b = (set(s["spectrum_ids"]) for s in grouped["series"])
    assert a.isdisjoint(b)
    assert a | b == set(flat["spectrum_ids"])
    assert sum(s["n_points"] for s in grouped["series"]) == flat["n_points"]


def test_grouping_leaves_the_ungrouped_shape_alone(client, two_run_dataset):
    """No `group_by`, no `series` -- the old response, unchanged."""
    body = client.get("/api/datasets/paired/chromatogram?type=tic").get_json()
    assert "series" not in body
    assert "group_by" not in body
    assert body["rtime"] and body["spectrum_ids"]


def test_grouped_xic_keeps_a_full_axis_per_series(client, two_run_dataset):
    """Each sample returns to baseline on its own axis, not a shared one."""
    body = client.get(
        "/api/datasets/paired/chromatogram"
        "?type=xic&mz=226.18&tolerance=0.01&group_by=dataOrigin"
    ).get_json()

    assert body["mz_window"] == pytest.approx([226.17, 226.19])
    for series in body["series"]:
        # Both MS1 scans, not only the one holding 226.18.
        assert series["n_points"] == 2
        assert series["intensity"] == [0.0, 30.0]
        assert series["n_matching"] == 1


def test_grouped_trace_respects_the_other_filters(client, two_run_dataset):
    body = client.get(
        "/api/datasets/paired/chromatogram"
        "?type=tic&ms_level=2&group_by=dataOrigin"
    ).get_json()
    assert len(body["series"]) == 2
    for series in body["series"]:
        assert series["n_points"] == 2


def test_unknown_grouping_is_rejected(client, two_run_dataset):
    response = client.get(
        "/api/datasets/paired/chromatogram?type=tic&group_by=msLevel"
    )
    assert response.status_code == 400
    assert "dataOrigin" in response.get_json()["error"]["message"]
