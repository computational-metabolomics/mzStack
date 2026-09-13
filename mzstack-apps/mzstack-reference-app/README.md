# mzstack-reference-app

The reference mzStack web application: a Flask API over the `mzstack` Python
library, and a Vue frontend for reading, querying and plotting datasets.

```
mzstack-app-api/   Flask API   -> mzstack-py
mzstack-app-ui/    Vue + Vite + Visual Framework 2.0 + Plotly
```

## What it does

- **Read** existing mzStack datasets — the workspace is scanned, and datasets
  elsewhere on disk can be registered by path.
- **Create** datasets from mzML files, from mzPeak archives, or from a
  MetaboLights study accession. None finishes inside a request, so all three
  run as background jobs with a live log.
- **Query** by field or with MassQL, and click a result row to see the
  spectrum it selected.
- **Plot** chromatograms (TIC, base peak, and extracted ion) — split per
  sample, overlaid or faceted — and the spectra behind them (centroid sticks
  or profile traces). A click on a chromatogram point opens the spectrum
  behind it and marks the scan on the trace.

There is no database. The datasets on disk are the state.

## Quick start

Two processes in development:

```bash
# terminal 1 — API
cd mzstack-app-api
uv venv --python 3.12 && uv pip install -e '.[dev]'
MZSTACK_WORKSPACE=~/mzstack-workspace .venv/bin/flask --app mzstack_api run --port 5000

# terminal 2 — UI
cd mzstack-app-ui
npm install && npm run dev        # http://localhost:5173
```

One process in production:

```bash
cd mzstack-app-ui && npm run build
cd ../mzstack-app-api && .venv/bin/flask --app mzstack_api run --port 5000
```

Each half has its own README with the details: [API](mzstack-app-api/README.md),
[UI](mzstack-app-ui/README.md).

## Design

The library is the whole domain layer. The API exposes `mzstack`'s public API
over HTTP and adds no mass-spectrometry logic of its own, with one exception:
`mzstack` has no chromatogram function, so TIC/BPC/XIC assembly lives in
`services/chromatograms.py` — built from the library's own columns, filters and
peak arrays. That module also does the per-sample split (`group_by=dataOrigin`),
inside the one dataset open, so a grouped chromatogram costs no more reads than
an ungrouped one.

Retention time is in **seconds** and polarity is **1 positive / 0 negative**
everywhere in the API, matching the mzStack format. The UI converts to minutes
for display at the edge, and nowhere else.
