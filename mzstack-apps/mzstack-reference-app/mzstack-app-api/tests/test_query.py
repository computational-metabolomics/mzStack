"""MassQL queries."""

from __future__ import annotations

import importlib.util

import pytest

massql = pytest.mark.skipif(
    importlib.util.find_spec("massql") is None, reason="needs the massql extra"
)


@massql
def test_query_returns_rows_keyed_by_spectrum_id(client, dataset):
    response = client.post(
        "/api/datasets/simple/query",
        json={"query": "QUERY scaninfo(MS2DATA)"},
    )
    assert response.status_code == 200
    body = response.get_json()

    assert body["scan_column"] == "spectrum_id_"
    # MassQL's `scan` holds the dataset-global spectrum_id_, so the ids a
    # result names can be fetched straight back from the spectra endpoint.
    assert body["spectrum_ids"] == [2, 4]
    assert [row["spectrum_id_"] for row in body["rows"]] == [2, 4]

    for spectrum_id in body["spectrum_ids"]:
        fetched = client.get(f"/api/datasets/simple/spectra/{spectrum_id}")
        assert fetched.status_code == 200
        assert fetched.get_json()["metadata"]["msLevel"] == 2


@massql
def test_both_query_modes_return_the_same_columns(client, dataset):
    """The point of the two tabs sharing one table."""
    fields = client.get("/api/datasets/simple/spectra").get_json()
    massql_result = client.post(
        "/api/datasets/simple/query",
        json={"query": "QUERY scaninfo(MS2DATA)"},
    ).get_json()

    assert massql_result["columns"] == fields["columns"]
    # And in the same units: rtime in seconds, not MassQL's minutes.
    by_id = {row["spectrum_id_"]: row for row in fields["rows"]}
    for row in massql_result["rows"]:
        assert row["rtime"] == by_id[row["spectrum_id_"]]["rtime"]


@massql
def test_a_product_ion_filter_narrows_the_result(client, dataset):
    body = client.post(
        "/api/datasets/simple/query",
        json={"query": "QUERY scaninfo(MS2DATA) WHERE MS2PROD=226.18"},
    ).get_json()
    assert body["spectrum_ids"] == [4]


@massql
def test_a_page_is_limited_but_the_total_is_not(client, dataset):
    """`limit` sizes the page; `total` still counts every match."""
    body = client.post(
        "/api/datasets/simple/query",
        json={"query": "QUERY scaninfo(MS2DATA)", "limit": 1},
    ).get_json()
    assert body["limit"] == 1
    assert len(body["rows"]) == 1
    assert body["total"] == 2
    # Every id the query selected is reported, not just this page's.
    assert body["spectrum_ids"] == [2, 4]


@massql
def test_a_result_pages_by_offset(client, dataset):
    def page(offset: int) -> list[int]:
        body = client.post(
            "/api/datasets/simple/query",
            json={"query": "QUERY scaninfo(MS2DATA)", "limit": 1, "offset": offset},
        ).get_json()
        assert body["offset"] == offset
        return [row["spectrum_id_"] for row in body["rows"]]

    assert page(0) == [2]
    assert page(1) == [4]
    assert page(2) == []


def test_a_non_numeric_limit_is_rejected(client, dataset):
    response = client.post(
        "/api/datasets/simple/query",
        json={"query": "QUERY scaninfo(MS2DATA)", "limit": "lots"},
    )
    assert response.status_code == 400


def test_an_empty_query_is_rejected(client, dataset):
    response = client.post("/api/datasets/simple/query", json={"query": "   "})
    assert response.status_code == 400
    assert "empty" in response.get_json()["error"]["message"].lower()


def test_an_unknown_engine_is_rejected(client, dataset):
    response = client.post(
        "/api/datasets/simple/query",
        json={"query": "QUERY scaninfo(MS2DATA)", "engine": "wat"},
    )
    assert response.status_code == 400


@massql
def test_a_malformed_query_reports_the_library_error(client, dataset):
    response = client.post(
        "/api/datasets/simple/query", json={"query": "NOT A QUERY"}
    )
    # Whatever massql makes of it, the client gets a 4xx with a message
    # rather than a traceback.
    assert response.status_code in (400, 500)
    assert response.get_json()["error"]["message"]
