<script setup lang="ts">
/**
 * Everything you can ask a dataset, and the one viewer that answers.
 *
 * Three panels: a chromatogram, a single spectrum plot, and a query. The
 * spectrum sits between the two panels that feed it -- click a peak in the
 * trace above, or a row in the results below, and it draws what you picked.
 * The selection lives here, not in either panel, which is what lets the loop
 * run in both directions.
 *
 * The viewer is not sticky. A full-width `position: sticky` panel keeps its
 * normal-flow box where it started, so the panel after it scrolls underneath
 * instead of being pushed, and the two paint through each other. Pinning it
 * would take a second column. `select` scrolls it into view instead.
 */
import { nextTick, ref } from 'vue'

import { api } from '@/api/client'
import type { Spectrum } from '@/api/types'
import ChromatogramPanel from '@/components/ChromatogramPanel.vue'
import SpectraSearch from '@/components/SpectraSearch.vue'
import SpectrumPlot from '@/components/SpectrumPlot.vue'
import { useAsync } from '@/composables/useAsync'

import { useDataset } from './useDataset'

const { dataset, datasetId } = useDataset()

type Source = 'chromatogram' | 'results'

const selectedId = ref<number | null>(null)
const source = ref<Source | null>(null)
const spectrum = useAsync<Spectrum>()

const ORIGIN: Record<Source, string> = {
  chromatogram: 'selected in the chromatogram',
  results: 'selected in the results table',
}

const viewer = ref<HTMLElement | null>(null)

/** Read live, not once: the setting can change while the page is open. */
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)')

async function select(id: number, from: Source) {
  selectedId.value = id
  source.value = from
  spectrum.run(() => api.spectrum(datasetId, id))

  // Picking a row in the results means scrolling past the viewer to get
  // there, so the answer would land off-screen behind the reader. `nearest`
  // scrolls only far enough to bring it into view, and does nothing at all
  // when it already is.
  await nextTick()
  viewer.value?.scrollIntoView({
    behavior: reducedMotion.matches ? 'auto' : 'smooth',
    block: 'nearest',
  })
}
</script>

<template>
  <div v-if="dataset" class="vf-stack vf-stack--600">
    <ChromatogramPanel
      :dataset="dataset"
      :dataset-id="datasetId"
      :selected-spectrum-id="selectedId"
      @select="select($event, 'chromatogram')"
    />

    <div ref="viewer" class="mz-panel">
      <div class="mz-panel__header mz-cluster">
        <strong>Spectrum viewer</strong>
        <span v-if="selectedId !== null" class="vf-text-body--3 mz-tabular">
          scan {{ selectedId }}
        </span>
        <span v-if="source" class="vf-text-body--3 vf-u-text-color--grey">
          {{ ORIGIN[source] }}
        </span>
      </div>

      <p
        v-if="spectrum.error.value"
        class="vf-text-body--3 vf-u-text-color--red vf-u-padding--400"
      >
        {{ spectrum.error.value }}
      </p>

      <div v-else-if="selectedId === null" class="mz-plot__empty">
        Click a point in the chromatogram, or a row in the results table,
        to plot the spectrum behind it.
      </div>

      <SpectrumPlot v-else :spectrum="spectrum.data.value" bare />
    </div>

    <SpectraSearch
      :dataset="dataset"
      :dataset-id="datasetId"
      :selected-spectrum-id="selectedId"
      @select="select($event, 'results')"
    />
  </div>
</template>
