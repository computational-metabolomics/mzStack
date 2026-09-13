<script setup lang="ts">
/**
 * The spectra filter.
 *
 * The API speaks retention time in seconds, as the mzStack view stores it.
 * The inputs here are in minutes, and the conversion happens at this
 * boundary and nowhere else.
 */
import { computed, ref, watch } from 'vue'

import type { DatasetDetail, SpectraFilter, ToleranceUnit } from '@/api/types'
import { emptyFilter, toleranceParams } from '@/api/types'

const props = defineProps<{ dataset: DatasetDetail }>()
const emit = defineEmits<{ apply: [filter: SpectraFilter] }>()

const msLevel = ref<string>('')
const rtMinMinutes = ref<string>('')
const rtMaxMinutes = ref<string>('')
const polarity = ref<string>('')
const precursorMz = ref<string>('')
const containsMz = ref<string>('')
const tolerance = ref<string>('0.01')
const toleranceUnit = ref<ToleranceUnit>('da')
const dataOrigin = ref<string>('')

/** Da is a fraction; ppm is tens. Switching units carries the default over. */
const DEFAULT_TOLERANCE: Record<ToleranceUnit, string> = { da: '0.01', ppm: '20' }

function changeUnit(next: ToleranceUnit) {
  if (tolerance.value === DEFAULT_TOLERANCE[toleranceUnit.value]) {
    tolerance.value = DEFAULT_TOLERANCE[next]
  }
  toleranceUnit.value = next
}

const levels = computed(() => Object.keys(props.dataset.ms_levels))
const origins = computed(() =>
  props.dataset.variables.includes('dataOrigin') ? true : false,
)

function toNumber(value: string): number | null {
  const trimmed = value.trim()
  if (!trimmed) return null
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : null
}

function build(): SpectraFilter {
  const filter = emptyFilter()
  if (msLevel.value) filter.ms_level = [Number(msLevel.value)]
  if (polarity.value) filter.polarity = [Number(polarity.value)]

  const min = toNumber(rtMinMinutes.value)
  const max = toNumber(rtMaxMinutes.value)
  filter.rt_min = min === null ? null : min * 60
  filter.rt_max = max === null ? null : max * 60

  const precursor = toNumber(precursorMz.value)
  if (precursor !== null) filter.precursor_mz = [precursor]

  const contains = toNumber(containsMz.value)
  if (contains !== null) filter.contains_mz = [contains]

  const width = toNumber(tolerance.value)
  Object.assign(
    filter,
    toleranceParams(
      width ?? Number(DEFAULT_TOLERANCE[toleranceUnit.value]),
      toleranceUnit.value,
    ),
  )
  if (dataOrigin.value.trim()) filter.data_origin = [dataOrigin.value.trim()]
  return filter
}

function apply() {
  emit('apply', build())
}

function reset() {
  msLevel.value = ''
  rtMinMinutes.value = ''
  rtMaxMinutes.value = ''
  polarity.value = ''
  precursorMz.value = ''
  containsMz.value = ''
  dataOrigin.value = ''
  apply()
}

// A dataset with only one MS level needs no MS-level control.
watch(
  () => props.dataset.id,
  () => reset(),
)
</script>

<template>
  <form @submit.prevent="apply">
    <div class="vf-grid vf-grid__col-4 vf-u-grid-gap--400">
      <div v-if="levels.length > 1" class="vf-form__item">
        <label class="vf-form__label" for="filter-ms-level">MS level</label>
        <select
          id="filter-ms-level"
          v-model="msLevel"
          class="vf-form__select mz-field"
        >
          <option value="">any</option>
          <option v-for="level in levels" :key="level" :value="level">
            MS{{ level }}
          </option>
        </select>
      </div>

      <div class="vf-form__item">
        <span class="vf-form__label">Retention time (min)</span>
        <div class="mz-range">
          <input
            v-model="rtMinMinutes"
            class="vf-form__input mz-field mz-tabular"
            placeholder="from"
            aria-label="Retention time from, in minutes"
          />
          <span aria-hidden="true">–</span>
          <input
            v-model="rtMaxMinutes"
            class="vf-form__input mz-field mz-tabular"
            placeholder="to"
            aria-label="Retention time to, in minutes"
          />
        </div>
      </div>

      <div v-if="dataset.polarities.length > 1" class="vf-form__item">
        <label class="vf-form__label" for="filter-polarity">Polarity</label>
        <select
          id="filter-polarity"
          v-model="polarity"
          class="vf-form__select mz-field"
        >
          <option value="">any</option>
          <option value="1">positive</option>
          <option value="0">negative</option>
        </select>
      </div>

      <div class="vf-form__item">
        <label class="vf-form__label" for="filter-precursor">Precursor m/z</label>
        <input
          id="filter-precursor"
          v-model="precursorMz"
          class="vf-form__input mz-field mz-tabular"
          placeholder="e.g. 195.0877"
        />
      </div>

      <div class="vf-form__item">
        <label class="vf-form__label" for="filter-contains">
          Contains peak at m/z
        </label>
        <input
          id="filter-contains"
          v-model="containsMz"
          class="vf-form__input mz-field mz-tabular"
          placeholder="e.g. 226.18"
        />
      </div>

      <!--
        One window, given either way. The two units are mutually exclusive,
        not additive: the library widens to whichever window is larger.
      -->
      <div class="vf-form__item">
        <label class="vf-form__label" for="filter-tolerance">Tolerance</label>
        <div class="mz-range">
          <input
            id="filter-tolerance"
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

      <div v-if="origins" class="vf-form__item">
        <label class="vf-form__label" for="filter-origin">Source file</label>
        <input
          id="filter-origin"
          v-model="dataOrigin"
          class="vf-form__input mz-field"
          placeholder="dataOrigin"
        />
      </div>
    </div>

    <div class="mz-cluster vf-u-margin__top--400">
      <button type="submit" class="vf-button vf-button--primary vf-button--sm">
        Apply
      </button>
      <button
        type="button"
        class="vf-button vf-button--secondary vf-button--sm"
        @click="reset"
      >
        Clear
      </button>
      <!--
        Scanning the signal is the one filter that reads peak data, so it is
        worth saying when it is in play.
      -->
      <span
        v-if="containsMz"
        class="mz-push-right vf-text-body--3 vf-u-text-color--grey"
      >
        searching the signal — slower without an mzsorted projection
      </span>
    </div>
  </form>
</template>
