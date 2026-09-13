<script setup lang="ts">
/**
 * The landing page: what mzStack is, and the two ways in.
 *
 * No search box: there is no cross-dataset index. Searching happens inside a
 * dataset.
 */
const FEATURES = [
  {
    title: 'Columnar storage',
    text: `Spectra become Parquet: per-spectrum metadata in one table, peak
      arrays in another. Read the columns a question needs and leave the
      signal on disk.`,
  },
  {
    title: 'Convert from mzML',
    text: `Point at one mzML file or a directory of them. Each run keeps its
      identity as a sample, so a study stays one dataset without losing track
      of which file a scan came from.`,
  },
  {
    title: 'MetaboLights ingest',
    text: `Give a study accession and the files are fetched and converted in
      the background, with a live log — none of these finish inside a
      request.`,
  },
  {
    title: 'Projections',
    text: `Signal caches, sorted the way a question wants to read it. An m/z
      sorted projection turns an extracted ion chromatogram from a full scan
      of the peak lists into a range read.`,
  },
  {
    title: 'Lazy filtering',
    text: `Filters compose without reading anything: MS level, retention time,
      polarity, precursor, sample. Only the last step touches the signal, by
      which point there is far less of it to touch.`,
  },
  {
    title: 'MassQL queries',
    text: `The community query language for mass spectrometry, run against a
      dataset in place. Click a result row to see the spectrum it selected.`,
  },
]

const STEPS = [
  {
    n: 1,
    title: 'Ingest',
    text: 'mzML, mzPeak archives, or a MetaboLights accession.',
  },
  {
    n: 2,
    title: 'Project',
    text: 'Build the signal caches the questions you ask will want.',
  },
  {
    n: 3,
    title: 'Query',
    text: 'Filter by field, or ask in MassQL.',
  },
  {
    n: 4,
    title: 'Visualise',
    text: 'Chromatograms per sample, and the spectrum behind any scan.',
  },
]
</script>

<template>
  <div>
    <section class="mz-hero__bg">
      <div class="mz-container">
        <div class="mz-hero__box">
          <span class="vf-badge vf-badge--primary" style="text-transform: none">
            mzStack
          </span>

          <h1 class="vf-hero__heading">
            A framework for storing, querying, and visualising mass
            spectrometry data
          </h1>
          <p class="vf-hero__text">
            A columnar engine for LC-MS data. mzStack stores spectra and signal
            data in a layout optimised for analytical queries, reading only the
            columns required by filters and MassQL. The same data powers the
            chromatograms and spectra behind every result.
          </p>
          <div class="mz-cluster vf-u-margin__top--600">
            <RouterLink to="/datasets" class="vf-button vf-button--primary">
              Browse datasets
            </RouterLink>
            <RouterLink to="/create" class="vf-button vf-button--secondary">
              Create a dataset
            </RouterLink>
          </div>
        </div>
      </div>
    </section>

    <div class="mz-container">
      <div>
        <section class="mz-section">
          <h2 class="vf-text-body--1 vf-u-margin__bottom--400">
            What it does
          </h2>
          <div class="vf-grid vf-grid__col-3 vf-u-grid-gap--600">
            <article
              v-for="feature in FEATURES"
              :key="feature.title"
              class="vf-card vf-card--bordered"
            >
              <div class="vf-card__content">
                <h3 class="vf-card__heading">{{ feature.title }}</h3>
                <p class="vf-card__text">{{ feature.text }}</p>
              </div>
            </article>
          </div>
        </section>

        <section class="mz-section">
          <h2 class="vf-text-body--1 vf-u-margin__bottom--400">
            How it works
          </h2>
          <div class="vf-grid vf-grid__col-4 vf-u-grid-gap--400">
            <div
              v-for="step in STEPS"
              :key="step.n"
              class="mz-panel vf-u-padding--500"
            >
              <span class="vf-badge vf-badge--primary">{{ step.n }}</span>
              <h3 class="vf-text-body--2 vf-u-margin__top--200">
                {{ step.title }}
              </h3>
              <p class="vf-text-body--3 vf-u-text-color--grey--dark">
                {{ step.text }}
              </p>
            </div>
          </div>
        </section>

      </div>
    </div>
  </div>
</template>
