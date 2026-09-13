<script setup lang="ts">
/**
 * Chromatogram controls, and the plot they drive.
 *
 * Inputs are in minutes and the API speaks seconds; the conversion happens at
 * this boundary and nowhere else. A dataset holding more than one sample is
 * split by default, one trace per source file.
 */
import { computed, onMounted, ref } from 'vue'

import { api } from '@/api/client'
import type {
  AnyChromatogram,
  ChromatogramType,
  DatasetDetail,
  ToleranceUnit,
} from '@/api/types'
import { GROUP_BY_SAMPLE, toleranceParams } from '@/api/types'
import ChromatogramPlot from '@/components/ChromatogramPlot.vue'
import { useAsync } from '@/composables/useAsync'

const props = defineProps<{
  dataset: DatasetDetail
  datasetId: string
  selectedSpectrumId: number | null
}>()
const emit = defineEmits<{ select: [spectrumId: number] }>()

const type = ref<ChromatogramType>('tic')
const msLevel = ref(1)
const mz = ref('')
const tolerance = ref('0.01')
const toleranceUnit = ref<ToleranceUnit>('da')
const rtMin = ref('')
const rtMax = ref('')
const perSample = ref(true)
const mode = ref<'overlay' | 'facet'>('overlay')

const chromatogram = useAsync<AnyChromatogram>()

const levels = computed(() => Object.keys(props.dataset.ms_levels ?? {}))
const needsMz = computed(() => type.value === 'xic')
const canDraw = computed(() => !needsMz.value || Number.isFinite(Number(mz.value)))

/**
 * Whether this dataset has more than one sample to split by.
 *
 * Counted on `dataOrigin`, not on runs: converting several mzML files in one
 * go produces a single run holding every sample, so a run count would hide
 * the split on exactly the datasets that most need it.
 */
const splittable = computed(
  () =>
    props.dataset.variables.includes(GROUP_BY_SAMPLE) &&
    (props.dataset.n_samples ?? 0) > 1,
)

/** Da is a fraction; ppm is tens. Switching units carries the default over. */
const DEFAULT_TOLERANCE: Record<ToleranceUnit, string> = { da: '0.01', ppm: '20' }

function changeUnit(next: ToleranceUnit) {
  if (tolerance.value === DEFAULT_TOLERANCE[toleranceUnit.value]) {
    tolerance.value = DEFAULT_TOLERANCE[next]
  }
  toleranceUnit.value = next
  draw()
}

function minutesToSeconds(value: string): number | null {
  const trimmed = value.trim()
  if (!trimmed) return null
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed * 60 : null
}

function draw() {
  if (!canDraw.value) return
  chromatogram.run(() =>
    api.chromatogram(props.datasetId, {
      type: type.value,
      ms_level: msLevel.value,
      rt_min: minutesToSeconds(rtMin.value),
      rt_max: minutesToSeconds(rtMax.value),
      mz: needsMz.value ? Number(mz.value) : null,
      // Both halves go, the unused one as zero: the API widens to whichever
      // window is larger, so omitting ppm lets its 20 default back in.
      ...(needsMz.value
        ? toleranceParams(Number(tolerance.value) || 0, toleranceUnit.value)
        : {}),
      group_by: splittable.value && perSample.value ? GROUP_BY_SAMPLE : null,
    }),
  )
}

onMounted(draw)
</script>

<template>
  <div class="mz-panel">
    <div class="mz-panel__header">
      <strong>Chromatogram viewer</strong>
    </div>

    <form class="vf-u-padding--400" @submit.prevent="draw">
      <div class="vf-grid vf-grid__col-3 vf-u-grid-gap--400">
        <div class="vf-form__item">
          <label class="vf-form__label" for="chromatogram-type">
            Chromatogram
          </label>
          <select
            id="chromatogram-type"
            v-model="type"
            class="vf-form__select mz-field"
            @change="draw"
          >
            <option value="tic">Total ion current</option>
            <option value="bpc">Base peak</option>
            <option value="xic">Extracted ion (XIC)</option>
          </select>
        </div>

        <div v-if="levels.length > 1" class="vf-form__item">
          <label class="vf-form__label" for="chromatogram-ms-level">
            MS level
          </label>
          <select
            id="chromatogram-ms-level"
            v-model.number="msLevel"
            class="vf-form__select mz-field"
          >
            <option v-for="level in levels" :key="level" :value="Number(level)">
              MS{{ level }}
            </option>
          </select>
        </div>

        <div v-if="needsMz" class="vf-form__item">
          <label class="vf-form__label" for="chromatogram-mz">m/z</label>
          <input
            id="chromatogram-mz"
            v-model="mz"
            class="vf-form__input mz-field mz-tabular"
            placeholder="e.g. 226.18"
            required
          />
        </div>

        <div v-if="needsMz" class="vf-form__item">
          <label class="vf-form__label" for="chromatogram-tolerance">
            Tolerance
          </label>
          <div class="mz-range">
            <input
              id="chromatogram-tolerance"
              v-model="tolerance"
              class="vf-form__input mz-field mz-tabular"
            />
            <select
              :value="toleranceUnit"
              class="vf-form__select mz-field mz-unit"
              aria-label="Tolerance unit"
              @change="
                changeUnit(
                  ($event.target as HTMLSelectElement).value as ToleranceUnit,
                )
              "
            >
              <option value="da">Da</option>
              <option value="ppm">ppm</option>
            </select>
          </div>
        </div>

        <div class="vf-form__item">
          <span class="vf-form__label">Retention time (min)</span>
          <div class="mz-range">
            <input
              v-model="rtMin"
              class="vf-form__input mz-field mz-tabular"
              placeholder="from"
              aria-label="Retention time from, in minutes"
            />
            <span aria-hidden="true">–</span>
            <input
              v-model="rtMax"
              class="vf-form__input mz-field mz-tabular"
              placeholder="to"
              aria-label="Retention time to, in minutes"
            />
          </div>
        </div>
      </div>

      <div class="mz-cluster vf-u-margin__top--400">
        <button
          type="submit"
          class="vf-button vf-button--primary vf-button--sm"
          :disabled="!canDraw || chromatogram.loading.value"
        >
          {{ chromatogram.loading.value ? 'Extracting…' : 'Draw' }}
        </button>

        <template v-if="splittable">
          <div class="vf-form__item vf-form__item--checkbox">
            <input
              id="chromatogram-per-sample"
              v-model="perSample"
              type="checkbox"
              class="vf-form__checkbox"
              @change="draw"
            />
            <label class="vf-form__label" for="chromatogram-per-sample">
              Per sample
            </label>
          </div>

          <div
            v-if="perSample"
            class="mz-segment"
            role="group"
            aria-label="Series layout"
          >
            <button
              v-for="option in (['overlay', 'facet'] as const)"
              :key="option"
              type="button"
              class="mz-segment__option"
              :aria-pressed="mode === option"
              @click="mode = option"
            >
              {{ option === 'overlay' ? 'Overlay' : 'Facet' }}
            </button>
          </div>
        </template>

        <!--
          An XIC reads the signal, unlike TIC and BPC which come from columns
          the file already carries, and takes correspondingly longer.
        -->
        <span
          v-if="needsMz"
          class="mz-push-right vf-text-body--3 vf-u-text-color--grey"
        >
          reads the signal — faster with an mzsorted projection
        </span>
      </div>
    </form>

    <div
      v-if="chromatogram.error.value"
      class="vf-banner vf-banner--notice vf-u-background-color--red--light
        vf-u-padding--400"
    >
      <p class="vf-banner__text">{{ chromatogram.error.value }}</p>
    </div>

    <!-- Already inside a panel, so the plot does not draw its own. -->
    <ChromatogramPlot
      v-else
      :chromatogram="chromatogram.data.value"
      :mode="mode"
      :selected-spectrum-id="selectedSpectrumId"
      bare
      @select="emit('select', $event)"
    />
  </div>
</template>
