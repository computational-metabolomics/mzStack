"""Filtering, paging and fetching peaks."""

from __future__ import annotations

import numpy as np
from mzstack import open_dataset


def _ids(body) -> list[int]:
    return [row["spectrum_id_"] for row in body["rows"]]


def test_unfiltered_page_lists_every_spectrum(client, dataset):
    body = client.get("/api/datasets/simple/spectra").get_json()
    assert body["total"] == 4
    assert _ids(body) == [1, 2, 3, 4]
    assert "mz" not in body["columns"]  # peaks are fetched separately


def test_ms_level_filter_matches_the_library(client, dataset):
    body = client.get("/api/datasets/simple/spectra?ms_level=2").get_json()
    expected = list(open_dataset(dataset).filter_ms_level(2).spectrum_ids)
    assert _ids(body) == expected == [2, 4]
    assert body["total"] == 2


def test_retention_time_is_in_seconds(client, dataset):
    # 60 s and 72 s are MS1; asking for 55-65 s keeps only the first.
    body = client.get(
        "/api/datasets/simple/spectra?ms_level=1&rt_min=55&rt_max=65"
    ).get_json()
    assert _ids(body) == [1]


def test_contains_mz_narrows_to_spectra_holding_the_peak(client, dataset):
    body = client.get(
        "/api/datasets/simple/spectra?contains_mz=226.18&tolerance=0.01"
    ).get_json()
    assert _ids(body) == [3, 4]


def test_filters_compose(client, dataset):
    body = client.get(
        "/api/datasets/simple/spectra?ms_level=2&contains_mz=226.18&tolerance=0.01"
    ).get_json()
    assert _ids(body) == [4]


def test_paging_walks_the_selection(client, dataset):
    first = client.get("/api/datasets/simple/spectra?limit=2").get_json()
    second = client.get(
        "/api/datasets/simple/spectra?limit=2&offset=2"
    ).get_json()
    assert _ids(first) == [1, 2]
    assert _ids(second) == [3, 4]
    # `total` is the size of the selection, not of the page.
    assert first["total"] == second["total"] == 4


def test_paging_past_the_end_is_empty_not_an_error(client, dataset):
    body = client.get("/api/datasets/simple/spectra?offset=99").get_json()
    assert body["rows"] == []
    assert body["total"] == 4


def test_explicit_columns_are_honoured(client, dataset):
    body = client.get(
        "/api/datasets/simple/spectra?columns=spectrum_id_,rtime"
    ).get_json()
    assert body["columns"] == ["spectrum_id_", "rtime"]
    assert set(body["rows"][0]) == {"spectrum_id_", "rtime"}


def test_an_unknown_column_says_what_is_available(client, dataset):
    response = client.get("/api/datasets/simple/spectra?columns=nope")
    assert response.status_code == 400
    message = response.get_json()["error"]["message"]
    assert "nope" in message
    assert "rtime" in message  # the available set is listed


def test_one_spectrum_returns_its_peaks(client, dataset):
    body = client.get("/api/datasets/simple/spectra/3").get_json()
    store = open_dataset(dataset).select_ids([3])
    mz, intensity = store.peaks_data()[0]

    assert body["mz"] == mz.tolist()
    assert body["intensity"] == intensity.tolist()
    assert body["n_peaks"] == 3
    assert body["downsampled"] is False
    assert body["base_peak"] == {"mz": 300.5, "intensity": 50.0}
    assert body["metadata"]["msLevel"] == 1


def test_absent_values_serialise_as_null_not_nan(client, dataset):
    # MS1 scans have no precursor; NaN would make the response invalid JSON.
    body = client.get("/api/datasets/simple/spectra?ms_level=1").get_json()
    assert body["rows"][0]["precursorMz"] is None


def test_out_of_range_spectrum_is_a_404(client, dataset):
    response = client.get("/api/datasets/simple/spectra/99")
    assert response.status_code == 404
    assert "1 to 4" in response.get_json()["error"]["message"]


def test_large_spectra_are_decimated_to_the_limit(client, dataset, config):
    from mzstack_api.serialization import decimate

    n = config.max_points * 3
    mz = np.linspace(100.0, 1000.0, n)
    intensity = np.zeros(n)
    intensity[n // 2] = 999.0  # the one peak that must survive

    out_mz, out_intensity, downsampled = decimate(mz, intensity, config.max_points)
    assert downsampled is True
    assert len(out_mz) <= config.max_points
    assert max(out_intensity) == 999.0
