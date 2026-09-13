<script setup lang="ts">
/**
 * Finding spectra, two ways into one table.
 *
 * Filtering by field and querying with MassQL are two spellings of the same
 * question, so they share a results table and a selection: whichever asked
 * last, clicking a row emits the spectrum id behind it and the viewer above
 * this component draws it.
 *
 * They share the columns too. The API projects a MassQL result back through
 * the spectra path, so both modes answer with `spectrum_id_`, `rtime` in
 * seconds, and the rest. The table keeps its shape across a tab switch.
 */
import { computed, onMounted, ref } from 'vue'

import { api } from '@/api/client'
import type {
  DatasetDetail,
  Health,
  QueryResult,
  Row,
  SpectraFilter,
  SpectraPage,
} from '@/api/types'
import { emptyFilter } from '@/api/types'
import DataTable from '@/components/DataTable.vue'
import FilterPanel from '@/components/FilterPanel.vue'
import { useAsync } from '@/composables/useAsync'

const props = defineProps<{
  dataset: DatasetDetail
  datasetId: string
  selectedSpectrumId: number | null
}>()
const emit = defineEmits<{ select: [spectrumId: number] }>()

type Mode = 'fields' | 'massql'

const PAGE = 100

const EXAMPLES = [
  { label: 'All MS2 scans', query: 'QUERY scaninfo(MS2DATA)' },
  {
    label: 'MS2 with a given product ion',
    query: 'QUERY scaninfo(MS2DATA) WHERE MS2PROD=226.18:TOLERANCEMZ=0.01',
  },
  {
    label: 'MS2 for a precursor',
    query: 'QUERY scaninfo(MS2DATA) WHERE MS2PREC=195.0877:TOLERANCEMZ=0.01',
  },
  {
    label: 'MS1 peaks in a retention window',
    query: 'QUERY scaninfo(MS1DATA) WHERE RTMIN=1 AND RTMAX=2',
  },
]

const mode = ref<Mode>('fields')

/**
 * The offset of the page on screen, per mode.
 *
 * Kept apart so switching tabs returns to where that tab was rather than
 * carrying a page number across to a different result set.
 */
const offset = ref<Record<Mode, number>>({ fields: 0, massql: 0 })

// -- fields ----------------------------------------------------------------

const filter = ref<SpectraFilter>(emptyFilter())
const page = useAsync<SpectraPage>()

function load() {
  page.run(() =>
    api.spectra(props.datasetId, filter.value, PAGE, offset.value.fields),
  )
}

function apply(next: SpectraFilter) {
  filter.value = next
  offset.value.fields = 0
  load()
}

// -- massql ----------------------------------------------------------------

const query = ref(EXAMPLES[0].query)
const result = useAsync<QueryResult>()
const { data: health, run: loadHealth } = useAsync<Health>()

function run() {
  result.run(() =>
    api.query(props.datasetId, {
      query: query.value,
      limit: PAGE,
      offset: offset.value.massql,
    }),
  )
}

function submit() {
  offset.value.massql = 0
  run()
}

// -- one table -------------------------------------------------------------

/**
 * Whichever mode asked last. Both answers are the same shape now, so the
 * table, the counts and the pager read one object rather than branching.
 */
const answer = computed<SpectraPage | QueryResult | null>(() =>
  mode.value === 'fields' ? page.data.value : result.data.value,
)

const columns = computed(() => answer.value?.columns ?? [])
const rows = computed<Row[]>(() => answer.value?.rows ?? [])
const total = computed(() => answer.value?.total ?? 0)
const shown = computed(() => answer.value?.rows.length ?? 0)

const error = computed(() =>
  mode.value === 'fields' ? page.error.value : result.error.value,
)

const loading = computed(() =>
  mode.value === 'fields' ? page.loading.value : result.loading.value,
)

const hasRun = computed(() => !!answer.value)

/**
 * Paging, either way. A filter pages through ids on the server; a MassQL
 * result has no cursor to resume, so the query runs again for the new offset.
 */
function step(by: number) {
  const current = offset.value[mode.value]
  offset.value[mode.value] = Math.max(0, current + by * PAGE)
  if (mode.value === 'fields') load()
  else run()
}

onMounted(() => {
  load()
  loadHealth(api.health)
})
</script>

<template>
  <div class="mz-panel">
    <div class="mz-panel__header mz-cluster">
      <strong>Query</strong>

      <ul class="vf-tabs__list" role="tablist">
        <li
          v-for="option in (['fields', 'massql'] as const)"
          :key="option"
          class="vf-tabs__item"
          role="presentation"
        >
          <!-- vf paints the selected tab off `is-active`, not `aria-selected`. -->
          <button
            type="button"
            class="vf-tabs__link"
            :class="mode === option ? 'is-active' : ''"
            role="tab"
            :aria-selected="mode === option"
            @click="mode = option"
          >
            {{ option === 'fields' ? 'Fields' : 'MassQL' }}
          </button>
        </li>
      </ul>
    </div>

    <div class="vf-u-padding--400 vf-stack vf-stack--400">
      <!-- Fields -->
      <FilterPanel v-if="mode === 'fields'" :dataset="dataset" @apply="apply" />

      <!-- MassQL -->
      <template v-else>
        <div
          v-if="health && !health.extras.massql"
          class="vf-banner vf-banner--notice vf-u-background-color--yellow--light
            vf-u-padding--400"
        >
          <p class="vf-banner__text">
            The MassQL query layer is not installed. Add it with
            <code class="mz-mono">uv pip install -e '.[massql]'</code>
            in <span class="mz-mono">mzstack-app-api</span>.
          </p>
        </div>

        <form @submit.prevent="submit">
          <div class="vf-form__item">
            <label class="vf-form__label" for="massql-query">MassQL query</label>
            <textarea
              id="massql-query"
              v-model="query"
              rows="3"
              spellcheck="false"
              class="vf-form__textarea mz-field mz-mono"
              style="width: 100%"
              @keydown.ctrl.enter="submit"
              @keydown.meta.enter="submit"
            ></textarea>
          </div>

          <div class="mz-cluster vf-u-margin__top--400">
            <button
              type="submit"
              class="vf-button vf-button--primary vf-button--sm"
              :disabled="result.loading.value"
            >
              {{ result.loading.value ? 'Running…' : 'Run' }}
            </button>
            <span class="vf-text-body--3 vf-u-text-color--grey">
              ⌘/Ctrl + Enter
            </span>
            <div class="mz-cluster mz-cluster--tight mz-push-right">
              <button
                v-for="example in EXAMPLES"
                :key="example.label"
                type="button"
                class="vf-button vf-button--secondary vf-button--sm"
                @click="query = example.query"
              >
                {{ example.label }}
              </button>
            </div>
          </div>
        </form>
      </template>

      <!-- Results, whichever asked -->
      <p v-if="error" class="vf-text-body--3 vf-u-text-color--red">
        {{ error }}
      </p>

      <template v-else-if="hasRun">
        <!-- Already inside a panel, so the table does not draw its own. -->
        <DataTable
          :columns="columns"
          :rows="rows"
          id-column="spectrum_id_"
          :selected-id="selectedSpectrumId"
          :loading="loading"
          :empty-message="
            mode === 'fields'
              ? 'No spectra match this filter.'
              : 'No spectra matched this query.'
          "
          bare
          @select="emit('select', $event)"
        />

        <!-- Under the rows it counts and pages, whichever mode filled them. -->
        <div class="mz-cluster vf-text-body--3">
          <span class="mz-tabular">
            {{ total.toLocaleString() }} spectra
            <template v-if="total > shown">
              · showing {{ offset[mode] + 1 }}–{{ offset[mode] + shown }}
            </template>
          </span>
          <div class="mz-cluster mz-cluster--tight mz-push-right mz-pager">
            <button
              class="vf-button vf-button--link"
              :disabled="offset[mode] === 0"
              @click="step(-1)"
            >
              Previous
            </button>
            <button
              class="vf-button vf-button--link"
              :disabled="offset[mode] + shown >= total"
              @click="step(1)"
            >
              Next
            </button>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>
