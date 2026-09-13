# mzstack-app-api

An HTTP API over the [`mzstack`](../../mzstack-py) library. It finds datasets,
exposes the library's filters, queries and peak arrays as JSON, and runs the
long ingests in the background.

It stores nothing of its own. There is no database: the datasets on disk are
the state, and the API re-reads them on every listing, so a dataset created by
the `mzstack` CLI while the server is running shows up without a restart.

## Install

`mzstack` is local and unpublished, so it is resolved from the sibling
checkout (`[tool.uv.sources]` in `pyproject.toml`):

```bash
uv venv --python 3.12
uv pip install -e '.[dev]'          # includes the massql extra
uv pip install -e '.[metabolights]' # optional: MetaboLights ingest
```

## Run

```bash
export MZSTACK_WORKSPACE=~/mzstack-workspace
export MZSTACK_DATA_ROOTS=/data/ms:/data/studies   # browsable source files
.venv/bin/flask --app mzstack_api run --port 5000
```

| Variable | Default | Meaning |
| --- | --- | --- |
| `MZSTACK_WORKSPACE` | `~/mzstack-workspace` | Scanned for datasets; new ones are created here |
| `MZSTACK_DATA_ROOTS` | the workspace | `:`-separated directories the file browser may list |
| `MZSTACK_MAX_POINTS` | 20000 | Peaks returned for one spectrum before decimating |
| `MZSTACK_MAX_ROWS` | 1000 | Rows returned by one table or query request |
| `MZSTACK_JOB_WORKERS` | 2 | Threads available to run ingests |
| `MZSTACK_UI_DIST` | `../mzstack-app-ui/dist` | Built frontend to serve, if present |

## Shape

```
src/mzstack_api/
  config.py          environment -> Config
  registry.py        workspace scan + datasets.json, ids for datasets
  store.py           opening datasets under the DuckDB lock
  jobs.py            thread pool + in-memory job records
  files.py           sandboxed filesystem browsing
  serialization.py   pandas/numpy -> JSON-safe values
  errors.py          library errors -> status codes
  services/          the only code that touches mzstack
  api/               blueprints: parse, call one service, return JSON
```

Blueprints parse requests and choose status codes; services do the work and
return plain Python. Mass-spectrometry logic lives in `services/`, not `api/`.

### Three things to know

**Units.** The mzStack view stores retention time in **seconds** and polarity
as **1 positive / 0 negative**, and the API speaks those throughout — MassQL
included. MassQL's own frame is peak-level and reports `rt` in minutes, and
`services/query.py` does not return it: it takes the `scan` ids the query
selected and reads them back through the spectra path, so a MassQL result and
a field filter are the same table. The UI converts for display; the API does
not convert for storage.

**One reader at a time.** `mzstack` reads through a single process-wide DuckDB
connection that is not locked around queries. `store.opened` holds a
process-wide lock for the whole build-and-fetch, so concurrent requests cannot
interleave statements on it. Without that lock, reads return wrong rows rather
than errors; `tests/test_concurrency.py` covers it.

Ingests that do not use DuckDB — converting mzML, fetching MetaboLights, the
long ones — run *without* that lock, so other datasets stay readable while
they run.

**Chromatograms are ours.** `mzstack` has no chromatogram API. TIC and BPC are
read from the `totIonCurrent` and `basePeakIntensity` columns; XIC is
assembled in `services/chromatograms.py` from `filter_contains_mz` and
`peaks_data`. It is the only domain logic the API adds, and it uses only the
library's public API.

Any of the three can be split per sample with `group_by=dataOrigin`, which
moves the arrays into a `series` list — one entry per source file, each with
its own full retention-time axis, so a sample missing the compound draws a
flat line. The split happens inside the single dataset open and costs no more
reads than an ungrouped trace. Without `group_by` the response shape is
unchanged.

## Endpoints

```
GET    /api/health
GET    /api/datasets
POST   /api/datasets/register              {path}
DELETE /api/datasets/<id>/register
GET    /api/datasets/<id>
GET    /api/datasets/<id>/samples
GET    /api/datasets/<id>/projections
POST   /api/datasets/<id>/projections      {type}            -> 202 job
DELETE /api/datasets/<id>/projections/<type>

GET    /api/datasets/<id>/spectra          ?ms_level= &rt_min= &rt_max= (seconds)
                                           &polarity= &data_origin= &precursor_mz=
                                           &contains_mz= &tolerance= &ppm=
                                           &columns= &limit= &offset=
GET    /api/datasets/<id>/spectra/<spectrum_id>   ?max_points=
GET    /api/datasets/<id>/chromatogram     ?type=tic|bpc|xic &mz= &tolerance=
                                           &ppm= &ms_level= &rt_min= &rt_max=
                                           &group_by=dataOrigin
POST   /api/datasets/<id>/query            {query, engine?, ms_level?, rt?,
                                            limit?, offset?}  -> spectra columns

POST   /api/ingest/mzml                    {files[], name, partition?}  -> 202 job
POST   /api/ingest/mzpeak                  {archives[], name, link}     -> 202 job
POST   /api/ingest/mzpeak/add              {dataset_id, archives[], link}
GET    /api/metabolights/<study_id>/files
POST   /api/ingest/metabolights            {study_id, name?, assays[], ...}

GET    /api/jobs
GET    /api/jobs/<job_id>
GET    /api/files                          ?path=
```

Every error is `{"error": {"type", "message"}}`. Messages from `mzstack` are
passed through unchanged — they already say which package to install or which
variable does not exist.

## Test

```bash
.venv/bin/python -m pytest
.venv/bin/ruff check src tests
```

Fixtures build a real mzML file and convert it with the real ingest, so the
tests exercise the same path a user does. Several cross-check the API against
the library directly.
