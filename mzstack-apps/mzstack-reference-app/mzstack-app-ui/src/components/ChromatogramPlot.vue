<script setup lang="ts">
/**
 * A chromatogram, flat or split per sample.
 *
 * The API speaks seconds; chromatograms are read in minutes, so the
 * conversion happens here and nowhere else. Clicking a point emits the
 * spectrum behind it, which is how the view lets you go from a peak in the
 * trace to the spectrum that produced it -- and the scan currently being
 * viewed is drawn back onto the trace as a vertical rule, so the two halves
 * of that loop stay visibly connected.
 *
 * Overlay and facet are one draw path: a facet is the same traces on stacked
 * y-axes sharing a single x, so switching modes is a relayout rather than a
 * teardown.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { AnyChromatogram, ChromatogramSeries } from '@/api/types'
import { isGrouped, toSeries } from '@/api/types'

import {
  AXIS,
  CONFIG,
  INTENSITY_AXIS,
  LAYOUT,
  loadPlotly,
  marker,
  seriesColour,
  traceType,
} from './plotly'

const props = withDefaults(
  defineProps<{
    chromatogram: AnyChromatogram | null
    mode?: 'overlay' | 'facet'
    selectedSpectrumId?: number | null
    /** Drop the surrounding panel, for a caller that supplies its own. */
    bare?: boolean
  }>(),
  { mode: 'overlay', selectedSpectrumId: null, bare: false },
)
const emit = defineEmits<{ select: [spectrumId: number] }>()

const el = ref<HTMLDivElement | null>(null)
let plotly: unknown = null
let bound = false

const TITLES: Record<string, string> = {
  tic: 'Total ion current',
  bpc: 'Base peak intensity',
  xic: 'Extracted ion current',
}

/** Vertical gap between facet rows, as a fraction of the plot height. */
const FACET_GAP = 0.06

const series = computed<ChromatogramSeries[]>(() =>
  props.chromatogram ? toSeries(props.chromatogram) : [],
)
const grouped = computed(
  () => !!props.chromatogram && isGrouped(props.chromatogram),
)
const faceted = computed(
  () => props.mode === 'facet' && grouped.value && series.value.length > 1,
)

const totalPoints = computed(() =>
  series.value.reduce((sum, s) => sum + s.n_points, 0),
)
const totalMatching = computed(() =>
  series.value.reduce((sum, s) => sum + (s.n_matching ?? 0), 0),
)

/** A sample's short name: the file, not the path that led to it. */
function shortName(name: string | undefined, index: number): string {
  if (!name) return `series ${index + 1}`
  const base = name.split('/').pop() || name
  return base.replace(/\.(mzML|mzXML|mzpeak)$/i, '')
}

/**
 * The retention time of the selected scan, in minutes, and which series holds
 * it -- the rule is drawn only on the subplot the scan actually belongs to.
 */
const selectedAt = computed<{ minutes: number; index: number } | null>(() => {
  const id = props.selectedSpectrumId
  if (id === null || id === undefined) return null
  for (let i = 0; i < series.value.length; i++) {
    const at = series.value[i].spectrum_ids.indexOf(id)
    if (at !== -1) return { minutes: series.value[i].rtime[at] / 60, index: i }
  }
  return null
})

/** `y`, `y2`, `y3`… — Plotly numbers every axis after the first. */
function axisRef(index: number): string {
  return index === 0 ? 'y' : `y${index + 1}`
}

function axisKey(index: number): string {
  return index === 0 ? 'yaxis' : `yaxis${index + 1}`
}

async function draw() {
  const data = props.chromatogram
  if (!el.value || !data) return

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const Plotly: any = plotly ?? (plotly = await loadPlotly())
  if (!el.value) return

  const list = series.value
  const stacked = faceted.value

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const traces = list.map((s, i) => {
    const minutes = s.rtime.map((seconds) => seconds / 60)
    const colour = seriesColour(i)
    const name = shortName(s.name, i)
    return {
      x: minutes,
      y: s.intensity,
      customdata: s.spectrum_ids,
      name,
      type: traceType(minutes.length),
      mode: 'lines+markers',
      line: { color: colour, width: 1.5 },
      marker: { size: 4, color: colour },
      xaxis: 'x',
      yaxis: stacked ? axisRef(i) : 'y',
      hovertemplate:
        `<b>%{x:.3f} min</b><br>intensity %{y:.4g}` +
        `<br>spectrum %{customdata}` +
        (grouped.value ? `<br>${name}` : '') +
        `<extra></extra>`,
    }
  })

  const title = TITLES[data.type] ?? 'intensity'
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const layout: any = {
    ...LAYOUT,
    // A legend only earns its space when there is more than one trace to tell
    // apart, and a facet already labels each row on its axis.
    showlegend: grouped.value && !stacked && list.length > 1,
    legend: { orientation: 'h', y: -0.2, font: { size: 11 } },
    xaxis: {
      ...AXIS,
      title: { text: 'retention time (min)' },
      anchor: stacked ? axisRef(list.length - 1) : 'y',
    },
  }

  if (stacked) {
    // Rows share one x-axis: every trace points at `x`, and each gets its own
    // slice of the vertical domain, top row first.
    const height = (1 - FACET_GAP * (list.length - 1)) / list.length
    list.forEach((s, i) => {
      const top = 1 - i * (height + FACET_GAP)
      layout[axisKey(i)] = {
        ...INTENSITY_AXIS,
        domain: [Math.max(0, top - height), top],
        anchor: 'x',
        title: { text: shortName(s.name, i), font: { size: 10 } },
      }
    })
  } else {
    layout.yaxis = { ...INTENSITY_AXIS, title: { text: title } }
  }

  const at = selectedAt.value
  if (at) {
    layout.shapes = stacked
      ? [marker(at.minutes, axisRef(at.index))]
      : [marker(at.minutes)]
  }

  await Plotly.react(el.value, traces, layout, CONFIG)

  if (!bound) {
    bound = true
    // Plotly adds its event emitter to the element it draws into; that is not
    // part of the DOM typings.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(el.value as any).on('plotly_click', (event: any) => {
      const point = event?.points?.[0]
      if (point && typeof point.customdata === 'number') {
        emit('select', point.customdata)
      }
    })
  }
}

onMounted(draw)
// `flush: 'post'` matters: the container is inside a `v-else`, so it does not
// exist until the render that follows the data arriving. A default (pre)
// watcher would run while the ref is still null and draw nothing.
watch(
  () => [props.chromatogram, props.mode, props.selectedSpectrumId],
  draw,
  { flush: 'post' },
)

onBeforeUnmount(() => {
  if (el.value && plotly) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    ;(plotly as any).purge(el.value)
    bound = false
  }
})
</script>

<template>
  <div :class="bare ? '' : 'mz-panel'">
    <div v-if="!chromatogram" class="mz-plot__empty">
      Choose a chromatogram to draw.
    </div>
    <template v-else>
      <!--
        Bare, this row sits between the caller's controls and the plot, so it
        takes its rule on top rather than underneath.
      -->
      <div
        class="mz-cluster"
        :class="bare ? 'mz-panel__strip' : 'mz-panel__header'"
      >
        <strong>{{ TITLES[chromatogram.type] ?? chromatogram.type }}</strong>
        <span class="vf-text-body--3">MS{{ chromatogram.ms_level }}</span>
        <span v-if="chromatogram.mz_window" class="vf-text-body--3 mz-tabular">
          m/z {{ chromatogram.mz_window[0].toFixed(4) }} –
          {{ chromatogram.mz_window[1].toFixed(4) }}
        </span>
        <span class="vf-text-body--3 mz-tabular">{{ totalPoints }} scans</span>
        <span v-if="grouped" class="vf-text-body--3 mz-tabular">
          {{ series.length }} samples
        </span>
        <span
          v-if="chromatogram.type === 'xic'"
          class="vf-text-body--3 mz-tabular"
        >
          {{ totalMatching }} with a peak in the window
        </span>
        <span class="mz-push-right vf-text-body--3 vf-u-text-color--grey">
          click a point to plot its spectrum
        </span>
      </div>

      <div
        ref="el"
        :class="['mz-plot', faceted ? 'mz-plot--facet' : '']"
        :style="faceted ? { height: `${Math.max(18, series.length * 11)}rem` } : undefined"
      ></div>

    </template>
  </div>
</template>
