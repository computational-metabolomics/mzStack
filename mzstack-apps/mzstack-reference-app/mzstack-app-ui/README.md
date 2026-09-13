# mzstack-app-ui

The mzStack web frontend: Vue 3, Vite, Visual Framework 2.0, Plotly.

## Run

The API must be running first (see [`../mzstack-app-api`](../mzstack-app-api)).

```bash
npm install
npm run dev            # http://localhost:5173, proxying /api to :5000
```

Point the proxy elsewhere with `MZSTACK_API_URL=http://host:port npm run dev`.

For production, build once and let Flask serve both halves from one process:

```bash
npm run build          # -> dist/, which the API serves with an SPA fallback
```

## Shape

```
src/
  api/client.ts        the only place that calls the API; typed per endpoint
  api/types.ts         the response shapes, and the unit conventions
  composables/         useAsync (request state), useJob (polling)
  components/          DataTable, FileTree, JobProgress, the panels and plots
  views/               one per page; dataset tabs under views/dataset/
```

Two dataset tabs. **Overview** is what the dataset *is*; **Data** is everything
you can ask it — chromatograms, a spectra search that is either a field filter
or a MassQL query, and one spectrum viewer fed by whichever of the two selected
a scan last.

`client.ts` unwraps the API's `{error: {type, message}}` envelope into a thrown
`ApiError`, so views show the API's own message. Several of those come
straight from the `mzstack` library and name the exact package to install.

### Conventions

**Minutes in, seconds out.** The API speaks retention time in seconds, as the
mzStack format stores it. Every input here is in minutes, and the conversion
happens at the boundary — `FilterPanel` and `ChromatogramPanel` on the way in,
`ChromatogramPlot` on the way out. Nowhere else.

**One selection, one viewer.** `DataTab` owns the selected scan; the
chromatogram panel and the search panel both emit into it and neither fetches a
spectrum itself. The loop runs both ways — click a peak to see its spectrum, or
find a spectrum by query and see where it sits in the trace, marked with a
vertical rule. The viewer sits *between* its two feeds, and `select` brings it
into view with `scrollIntoView({block: 'nearest'})`, which does nothing when it
is already visible.

It is **not** sticky. `position: sticky` leaves an element's normal-flow box
where it started, so a full-width sticky panel does not push what follows: the
query panel scrolls underneath it and the two paint through each other.
Pinning the viewer takes a second column.

**One table, either question.** Fields and MassQL share a panel, a results
table, a pager and a selection. `POST /query` projects the scans a query
selected back through the spectra path, so both modes return the same columns
in the same units, and `spectrum_id_` addresses a row whichever asked.

**One tolerance, two units.** A mass window is a value plus Da or ppm, never
both. The library takes the wider of the two windows and the API defaults `ppm`
to 20, so `toleranceParams` in `api/types.ts` sends the unit that was *not*
chosen as an explicit `0`.

**Samples are `dataOrigin`, not runs.** Converting several mzML files in one go
produces a *single* run holding every sample. `n_samples` counts distinct
`dataOrigin` values, and decides whether a chromatogram is split per sample.

**vf-core, precompiled.** `@visual-framework/vf-core` is the Sass/gulp
authoring pipeline, not a stylesheet. Each `vf-*` component package ships a
self-contained `.css` that inlines the design tokens; `style.css` imports those
directly and no Sass toolchain is involved. Anything vf has no class for — plot
containers, table overflow, the file tree — lives in the small `mz-` app layer
at the bottom of that file, reading vf custom properties rather than restating
colours.

**The file tree's checkbox is native.** `.vf-form__checkbox` is `opacity: 0`;
vf paints the box from a sibling `.vf-form__label::before`, so that class on a
bare input renders nothing at all, and the pattern has no indeterminate state.
A tree needs all three states — a directory with only some of its files picked
— so `FileTree` uses the UA control with `accent-color` to keep it in the vf
palette.

**Plotly is lazy.** The largest dependency by a wide margin (~4 MB against the
~97 kB rest of the bundle), and most of the app never plots. It loads on the
first chart mount and stays out of the initial bundle.

**`flush: 'post'` on plot watchers.** Both plot components render their
container inside a `v-else`, so it does not exist until the render *after* data
arrives. A default pre-flush watcher fires while the template ref is still null
and draws nothing.

**WebGL is optional.** `traceType()` picks `scattergl` only above a few
thousand points and only when WebGL works; a gl trace on a machine without it
renders as nothing at all.

## Check

```bash
npm run typecheck
npm run build
```
