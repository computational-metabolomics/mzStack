<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import { api } from '@/api/client'
import type { Job, SampleMetadata } from '@/api/types'
import DataTable from '@/components/DataTable.vue'
import JobProgress from '@/components/JobProgress.vue'
import { useAsync } from '@/composables/useAsync'
import { useJob } from '@/composables/useJob'

import { useDataset } from './useDataset'

const { dataset, reload, datasetId } = useDataset()
const { data: samples, run: loadSamples } = useAsync<SampleMetadata>()
const { job, watch: watchJob } = useJob()

const projectionError = ref<string | null>(null)

const rtimeMinutes = computed(() => {
  const range = dataset.value?.rtime_range
  if (!range) return null
  return [range[0] / 60, range[1] / 60] as const
})

const polarityLabel = computed(() =>
  (dataset.value?.polarities ?? [])
    .map((p) => (p === 1 ? 'positive' : p === 0 ? 'negative' : String(p)))
    .join(', '),
)

async function build(type: string) {
  projectionError.value = null
  try {
    const started: Job = await api.buildProjection(datasetId, type)
    watchJob(started)
  } catch (exc) {
    projectionError.value = (exc as Error).message
  }
}

// The projection state on screen only changes when the job lands, so refetch
// the dataset then rather than leaving a stale "not built" badge.
watch(
  () => job.value?.status,
  (status) => {
    if (status === 'succeeded' || status === 'failed') reload()
  },
)

async function drop(type: string) {
  projectionError.value = null
  try {
    await api.dropProjection(datasetId, type)
    reload()
  } catch (exc) {
    projectionError.value = (exc as Error).message
  }
}

onMounted(() => {
  if (dataset.value?.has_sample_metadata) loadSamples(() => api.samples(datasetId))
})
</script>

<template>
  <div
    v-if="dataset"
    class="vf-grid vf-grid__col-3 vf-u-grid-gap--600"
    style="align-items: start"
  >
    <!--
      Rendered even without sample metadata, so the two-column layout holds
      rather than collapsing the sidebar to full width on some datasets.
    -->
    <div class="vf-u-grid__col--span-2--md">
      <section class="mz-panel vf-u-padding--400">
        <h2 class="vf-text-body--2">Samples</h2>
        <template v-if="dataset.has_sample_metadata && samples">
          <p class="vf-text-body--4 vf-u-text-color--grey--dark vf-u-margin__bottom--400">
            Joined to spectra on <code class="mz-mono">{{ samples.key }}</code
            >.
          </p>
          <DataTable :columns="samples.columns" :rows="samples.rows" bare />
        </template>
        <p v-else class="vf-text-body--3 vf-u-text-color--grey--dark">
          No sample metadata for this dataset.
        </p>
      </section>

      <section class="mz-panel vf-u-padding--400 vf-u-margin__top--600">
        <h2 class="vf-text-body--2 vf-u-margin__bottom--400">
          Spectra variables
          <span class="vf-u-text-color--grey">({{ dataset.variables.length }})</span>
        </h2>
        <div class="mz-cluster mz-cluster--tight">
          <span
            v-for="variable in dataset.variables"
            :key="variable"
            class="vf-badge mz-mono"
          >
            {{ variable }}
          </span>
        </div>
      </section>
    </div>

    <section class="mz-panel vf-u-padding--400">
      <h2 class="vf-text-body--2 vf-u-margin__bottom--400">Summary</h2>
      <dl class="vf-list vf-list--definition vf-text-body--3">
        <dt class="vf-list--definition__term">Format</dt>
        <dd class="vf-list--definition__details">
          {{ dataset.format }} {{ dataset.version }}
        </dd>

        <dt class="vf-list--definition__term">Kind</dt>
        <dd class="vf-list--definition__details">{{ dataset.kind }}</dd>

        <dt class="vf-list--definition__term">Spectra</dt>
        <dd class="vf-list--definition__details mz-tabular">
          {{ dataset.n_spectra.toLocaleString() }}
        </dd>

        <!--
          `runs.length`: a detail response carries `runs` and no `n_runs`.
          Samples count distinct `dataOrigin` values, which can exceed the run
          count -- one conversion of many files is a single run.
        -->
        <dt class="vf-list--definition__term">Runs</dt>
        <dd class="vf-list--definition__details mz-tabular">
          {{ dataset.runs.length.toLocaleString() }}
        </dd>

        <dt class="vf-list--definition__term">Samples</dt>
        <dd class="vf-list--definition__details mz-tabular">
          {{ (dataset.n_samples ?? 0).toLocaleString() }}
        </dd>

        <template v-if="rtimeMinutes">
          <dt class="vf-list--definition__term">Retention time</dt>
          <dd class="vf-list--definition__details mz-tabular">
            {{ rtimeMinutes[0].toFixed(2) }} – {{ rtimeMinutes[1].toFixed(2) }} min
          </dd>
        </template>

        <template v-if="polarityLabel">
          <dt class="vf-list--definition__term">Polarity</dt>
          <dd class="vf-list--definition__details">{{ polarityLabel }}</dd>
        </template>

        <dt class="vf-list--definition__term">Path</dt>
        <dd
          class="vf-list--definition__details mz-mono vf-text-body--4
            vf-u-text--break"
          :title="dataset.path"
        >
          {{ dataset.path }}
        </dd>
      </dl>

      <!--
        Projections are a signal cache: they make m/z filtering and XICs much
        faster and cost roughly as much disk as the signal again. Results are
        identical either way, so this is purely a speed/space choice.
      -->
      <h3 class="vf-text-body--2 vf-u-margin__top--600">Projections</h3>
      <p class="vf-text-body--4 vf-u-text-color--grey--dark">
        Caches that speed up m/z filtering and extracted ion chromatograms.
        Results are the same with or without.
      </p>
      <ul class="vf-list vf-list--tight vf-u-margin__top--200" style="list-style: none; padding: 0">
        <li
          v-for="(state, type) in dataset.projections.current"
          :key="type"
          class="mz-cluster mz-cluster--tight vf-text-body--3"
        >
          <span>{{ type }}</span>
          <span
            class="vf-badge"
            :class="state.complete ? 'vf-badge--primary' : ''"
          >
            {{ state.complete ? 'built' : 'not built' }}
          </span>
          <button
            v-if="state.complete"
            class="vf-button vf-button--link vf-text-body--4 mz-push-right"
            @click="drop(String(type))"
          >
            drop
          </button>
          <button
            v-else
            class="vf-button vf-button--link vf-text-body--4 mz-push-right"
            @click="build(String(type))"
          >
            build
          </button>
        </li>
      </ul>
      <p
        v-if="projectionError"
        class="vf-text-body--3 vf-u-text-color--red vf-u-margin__top--200"
      >
        {{ projectionError }}
      </p>
      <JobProgress
        v-if="job"
        :job="job"
        compact
        class="vf-u-margin__top--400"
      />
    </section>
  </div>
</template>
