<script setup lang="ts">
/**
 * A mass spectrum.
 *
 * Centroided data is drawn as sticks and profile data as a continuous trace.
 * Hover comes from a separate invisible marker trace at the peak apexes; a
 * line trace made of null-separated segments has nothing to hover.
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { Spectrum } from '@/api/types'

import {
  AXIS,
  CONFIG,
  INTENSITY_AXIS,
  LAYOUT,
  loadPlotly,
  PRIMARY,
  sticks,
  traceType,
} from './plotly'

const props = withDefaults(
  defineProps<{
    spectrum: Spectrum | null
    /** Drop the surrounding panel, for a caller that supplies its own. */
    bare?: boolean
  }>(),
  { bare: false },
)

const el = ref<HTMLDivElement | null>(null)
let plotly: unknown = null

function isCentroided(spectrum: Spectrum): boolean {
  const flag = spectrum.metadata.centroided
  // Absent for some instruments, and defaults to sticks: a centroid list
  // drawn as a line invents slopes that are not in the data.
  return flag === undefined || flag === null ? true : Boolean(flag)
}

async function draw() {
  const spectrum = props.spectrum
  if (!el.value || !spectrum) return

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const Plotly: any = plotly ?? (plotly = await loadPlotly())
  if (!el.value) return

  const centroided = isCentroided(spectrum)
  const traces = centroided
    ? [
        {
          ...sticks(spectrum.mz, spectrum.intensity),
          type: 'scatter',
          mode: 'lines',
          line: { color: PRIMARY, width: 1 },
          hoverinfo: 'skip',
        },
        {
          x: spectrum.mz,
          y: spectrum.intensity,
          type: 'scatter',
          mode: 'markers',
          marker: { size: 6, opacity: 0 },
          hovertemplate: '<b>m/z %{x:.4f}</b><br>intensity %{y:.4g}<extra></extra>',
        },
      ]
    : [
        {
          x: spectrum.mz,
          y: spectrum.intensity,
          type: traceType(spectrum.mz.length),
          mode: 'lines',
          line: { color: PRIMARY, width: 1 },
          hovertemplate: '<b>m/z %{x:.4f}</b><br>intensity %{y:.4g}<extra></extra>',
        },
      ]

  const layout = {
    ...LAYOUT,
    xaxis: { ...AXIS, title: { text: 'm/z' } },
    yaxis: { ...INTENSITY_AXIS, title: { text: 'intensity' } },
    annotations: spectrum.base_peak
      ? [
          {
            x: spectrum.base_peak.mz,
            y: spectrum.base_peak.intensity,
            text: spectrum.base_peak.mz.toFixed(4),
            showarrow: false,
            yshift: 10,
            font: { size: 10, color: AXIS.titlefont.color },
          },
        ]
      : [],
  }

  await Plotly.react(el.value, traces, layout, CONFIG)
}

onMounted(draw)
// `flush: 'post'`: the plot container lives in a `v-else` branch and is only
// created on the render after a spectrum arrives. A pre-flush watcher fires
// with the ref still null.
watch(() => props.spectrum, draw, { flush: 'post' })

onBeforeUnmount(async () => {
  if (el.value && plotly) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(plotly as any).purge(el.value)
  }
})
</script>

<template>
  <div :class="bare ? '' : 'mz-panel'">
    <div v-if="!spectrum" class="mz-plot__empty">
      Select a spectrum to plot it.
    </div>
    <template v-else>
      <!--
        Bare, the caller has already named the scan in its own header, so this
        row carries only the metadata and skips the identifier rather than
        stating it twice.
      -->
      <div
        class="mz-cluster"
        :class="bare ? 'vf-u-padding--400' : 'mz-panel__header'"
      >
        <strong v-if="!bare">Spectrum {{ spectrum.spectrum_id }}</strong>
        <span class="vf-text-body--3">
          MS{{ spectrum.metadata.msLevel ?? '?' }}
        </span>
        <span
          v-if="typeof spectrum.metadata.rtime === 'number'"
          class="vf-text-body--3 mz-tabular"
        >
          RT {{ (spectrum.metadata.rtime / 60).toFixed(2) }} min
        </span>
        <span
          v-if="typeof spectrum.metadata.precursorMz === 'number'"
          class="vf-text-body--3 mz-tabular"
        >
          precursor {{ spectrum.metadata.precursorMz.toFixed(4) }}
        </span>
        <span class="vf-text-body--3 mz-tabular">
          {{ spectrum.n_peaks }} peaks
        </span>
        <span
          v-if="spectrum.downsampled"
          class="vf-text-body--3 vf-u-text-color--orange--dark"
        >
          thinned for display
        </span>
      </div>
      <div ref="el" class="mz-plot"></div>
    </template>
  </div>
</template>
