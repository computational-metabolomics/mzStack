# mzstack-apps

Applications built on the mzStack libraries. The libraries themselves live
beside this directory: [`mzstack-py`](../mzstack-py) and
[`mzstack-r`](../mzstack-r).

```
mzstack-reference-app/   Flask API + Vue UI   -> mzstack-py
```

Each application is self-contained: its own dependencies, its own build, its
own README. Nothing here is shared between applications, and nothing here is
depended on by the libraries — the arrow points one way, from an application to
a library.

## The applications

- **[mzstack-reference-app](mzstack-reference-app)** — the reference
  implementation, and the one that exercises the whole library surface. A Flask
  API that exposes `mzstack`'s public API over HTTP, and a Vue frontend for
  browsing datasets, querying spectra with MassQL or by field, and plotting
  chromatograms and spectra.

More are planned, including a Shiny application over `mzstack-r`.
