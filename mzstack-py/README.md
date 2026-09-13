# mzstack

Storage, querying and spectral matching for mass spectrometry data, on the
**mzStack** format.

An mzStack dataset is a directory identified by an `mzStack.json` manifest,
holding one or more *runs*. A run is either:

- **`native`** — converted from raw MS data files (mzML), with peaks stored
  alongside the metadata as `list<double>` columns; or
- **`mzpeak`** — an index over external [HUPO-PSI mzPeak][mzpeak] archives,
  which are read where they are and **never modified or copied**.

The manifest records which kind each run is, so the two never have to be told
apart by inspecting the directory.

The R package [`MsBackendParquet`][r] is the reference implementation of the
same format. Datasets written by either are readable by the other, and the
test suite checks that in both directions for both run kinds — down to the
set of column names each writes.

## Install

```bash
pip install mzstack                   # storage core
pip install 'mzstack[massql]'         # + the MassQL query layer
pip install 'mzstack[blink]'          # + BLINK spectral matching
pip install 'mzstack[metabolights]'   # + MetaboLights study ingest
pip install 'mzstack[all]'
```

Reading mzPeak archives in the point layout needs nothing beyond the core.
`blink` is installed from git (it is not on PyPI) and pulls in torch and
rdkit of its own, so it is kept out of the core dependency set. The
`[mzpeak]` extra adds the upstream reference reader for archives this package
declines. The `[metabolights]` extra adds EBI's `metabolights-utils`, which
pulls pydantic, httpx and click of its own.

## Use

```python
from mzstack import MzStack
from mzstack.ingest import mzml_to_mzstack

mzml_to_mzstack(["QC01.mzML", "QC02.mzML"], "store/")

ds = MzStack("store/")
ds.spectra_data(columns=["msLevel", "rtime", "precursorMz"])
ds.filter_ms_level(2).filter_rt((60, 300))          # SECONDS
ds.filter_precursor_mz_values(195.0877, ppm=20)
ds.filter_contains_mz(226.18, tolerance=0.01)       # searches the signal
ds.peaks_data()                                     # [(mz, intensity), …]
ds.massql("QUERY scaninfo(MS2DATA) WHERE MS2PROD=226.18")
```

Indexing mzPeak archives instead, leaving them untouched:

```python
from mzstack.ingest import create_mzpeak_dataset

create_mzpeak_dataset(["QC01.mzpeak", "QC02.mzpeak"], "store/")
```

Fetching a public MetaboLights study, or a subset of one:

```python
from mzstack.ingest import list_study_files, metabolights_to_mzstack

list_study_files("MTBLS341")             # ISA-Tab only; nothing large moves

metabolights_to_mzstack(
    "MTBLS341", "store/",
    assays=["a_MTBLS341_exudate_NEG_LCMS.txt"],   # one assay of six
    exclude=["*Exp1*"],                           # patterns; samples= also works
)
```

Only the selected files are downloaded, into `~/.cache/mzstack/metabolights`,
and the study's own sample and assay annotation is written as the dataset's
`sample_metadata.parquet`. mzML is taken from either the `Raw Spectral Data
File` or the `Derived Spectral Data File` column — most studies converted to
mzML and so reference it as derived. A study whose assays reference no mzML
at all is refused rather than half-ingested.

Spectral matching with BLINK:

```python
from mzstack.match import annotate_hits, blink_match
from mzstack.reference import ReferenceLibrary

library = ReferenceLibrary.from_msp("MassBank.msp", "library/")
hits = blink_match(ds.filter_ms_level(2), library, tolerance=0.01, top_n=5)
annotate_hits(hits, library)     # adds name, InChIKey, SMILES
```

### Querying

`ds.massql(q)` answers scan-level queries — `scaninfo`, `scannum`, `scanmz` —
as a single DuckDB statement, and hands everything else to massql's evaluator.
The difference is large on profile data: the evaluator needs one row per peak
for the whole selection, while a scan-level question needs per-spectrum
metadata plus one `sum(intensity)`. On a 4,256-spectrum study with profile MS1
(39M peaks), `QUERY scaninfo(MS2DATA)` goes from ~40 s to well under a second.

The compiler covers retention-time, scan, polarity, charge and precursor
conditions, plus `MS2PROD` / `MS2NL` / `MS1MZ` peak conditions with m/z or ppm
tolerances. Anything else — `scansum`, bare `MS1DATA` / `MS2DATA`, intensity
qualifiers, `X` variables — is declined and falls back, so the answer is the
same either way. `engine="engine"` forces the evaluator; the two are held
together by a differential test.

### Command line

```bash
mzstack convert *.mzML store/ --metadata-file samples.csv
mzstack index *.mzpeak store/          # archives are not modified
mzstack metabolights MTBLS341 --list   # what is there, before downloading it
mzstack metabolights MTBLS341 store/ --assay a_MTBLS341_exudate_NEG_LCMS.txt
mzstack info store/
mzstack list store/
mzstack query store/ "QUERY scaninfo(MS2DATA) WHERE MS2PROD=226.18"
mzstack match store/ --reference library/ --top-n 5 -o hits.tsv
mzstack project store/ --type scansorted
```

## Conventions

**Both run kinds store mzPeak's column vocabulary on disk** — `ms_level`,
`time` in minutes, `scan_polarity` ±1, `spectrum_representation` as a CURIE,
`isolation_window_target` plus offsets, `data_origin`. One SQL view translates
them to canonical names for both, and either kind can be exported back to an
mzPeak archive mechanically.

mzstack reserves four column names mzPeak has no equivalent for:
`spectrum_id_` (the row key), `acquisition_num_` (the instrument scan number),
`scan_index` (position within the source file — `spectrum_index` is the
dataset's own unique key), and the `n_scans` / `n_selected_ions` /
`n_precursors` counts. `mz` and `intensity` keep their names, and any column
the map does not mention — a user-defined spectra variable — passes through
untouched in both directions.

Three retention-time and polarity conventions meet in this stack. Each
translation lives in exactly one module:

| | retention time | polarity |
|---|---|---|
| on disk (both kinds) | minutes | `1` / `-1` |
| mzStack view (canonical) | **seconds** | `1` / `0`, null unknown |
| MassQL frames | minutes | `1` pos / `2` neg / `0` unknown |

Both directions of the on-disk translation live in
`mzstack.format.columnmap`; canonical → MassQL in `mzstack.query.massql`.

Spectra are addressed by `spectrum_id_`: 1-based, contiguous across the whole
dataset, and allocated one block per run so adding a run never renumbers an
existing one. Every result — including MassQL's `scan` column and BLINK's
`query_id` — uses it, so results join straight back to the data.

## Projections

Signal is stored in spectrum order, which answers "give me these spectra's
peaks" directly. In that order every block of rows spans nearly the whole mass
range, so a filter on m/z prunes no blocks.

A projection is a re-ordered copy. Two exist:

- **`mzsorted`** — ordered by m/z, for `filter_contains_mz`;
- **`scansorted`** — ordered by `(spectrum_id_, mz)`, which is what the MassQL
  frames and BLINK want.

```python
from mzstack import build_projection
build_projection("store/", "mzsorted")
```

A projection is a **cache**: it can be dropped at any time, it is ignored once
stale, and results are identical with or without it; only the speed changes.
It costs roughly as much disk as the signal again.

## Extensions

Three things mzstack writes are not (yet) part of mzStack 0.1.0. All are
additive and ignored by other implementations:

- the **`scansorted`** projection (the specification defines `mzsorted` only);
- **`sample_metadata.parquet`**, one row per `dataOrigin`; the format has no
  per-sample metadata concept;
- the `activationType` spectra variable on native runs, which the map does
  not mention and so passes through under its own name.

## Not yet implemented

- **Compiled MassQL beyond scan-level queries.** `scansum`, bare `MS1DATA` /
  `MS2DATA`, intensity qualifiers and `X` variables still fall back to massql's
  evaluator. See *Querying* for what the compiler does cover.
- **The chunked mzPeak layout.** Only the point layout is supported; archives
  using the chunked one are rejected with a message naming it.
- **BLINK's REM / network scoring.** The plain sparse-cosine path is what is
  wired up. The model-based path needs trained random forests that upstream
  ships as version-pinned scikit-learn pickles.
- **Visualisation.**

## Development

```bash
uv venv --python 3.12
uv pip install -e '.[dev,massql]'
.venv/bin/python -m pytest          # 235 tests
.venv/bin/ruff check src tests
```

The cross-implementation tests in `tests/test_r_interop.py` need `Rscript`
with a `MsBackendParquet` new enough to share the mzPeak column vocabulary,
and skip with a message when it is absent or too old. They are the only
tests that can catch the two implementations drifting apart, so they belong
in CI even though they are optional locally.

[mzpeak]: https://github.com/HUPO-PSI/mzPeak
[r]: https://github.com/ossedb/MsBackendParquet
