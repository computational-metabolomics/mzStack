"""Concurrent reads.

`mzstack` reads through one process-wide DuckDB connection that is not locked
around queries, so parallel requests would interleave statements on it and
return each other's rows. `store.opened` serialises them. This is the test
that would catch that guard being removed.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor


def test_parallel_requests_return_their_own_rows(client, dataset):
    """Each thread asks a different question and must get its own answer."""
    questions = [
        ("ms_level=1", [1, 3]),
        ("ms_level=2", [2, 4]),
        ("contains_mz=226.18&tolerance=0.01", [3, 4]),
        ("rt_min=55&rt_max=65", [1]),
    ]

    def ask(pair):
        query, expected = pair
        body = client.get(f"/api/datasets/simple/spectra?{query}").get_json()
        return query, [row["spectrum_id_"] for row in body["rows"]], expected

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(ask, questions * 6))

    for query, got, expected in results:
        assert got == expected, f"{query} returned {got}"


def test_parallel_peak_reads_are_consistent(client, dataset):
    """Peak fetches take a different DuckDB path and must be safe too."""
    expected = {
        1: [100.0, 200.0, 300.0],
        3: [100.0, 226.18, 300.5],
    }

    def ask(spectrum_id):
        body = client.get(f"/api/datasets/simple/spectra/{spectrum_id}").get_json()
        return spectrum_id, body["mz"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(ask, [1, 3] * 15))

    for spectrum_id, mz in results:
        assert mz == expected[spectrum_id]


def test_reads_continue_while_an_ingest_runs(client, workspace, source_mzml):
    """Reads answer while a conversion is running.

    Converting mzML never touches DuckDB, so the ingest job does not hold the
    read lock. Holding it would deadlock or block this test.
    """
    from mzstack.ingest import mzml_to_mzstack

    mzml_to_mzstack(source_mzml, workspace / "existing")

    started = client.post(
        "/api/ingest/mzml", json={"files": [str(source_mzml)], "name": "while-busy"}
    )
    assert started.status_code == 202

    body = client.get("/api/datasets/existing/spectra").get_json()
    assert body["total"] == 4
