<script setup lang="ts">
/**
 * Creating a dataset, from the three sources mzstack can ingest.
 *
 * All three answer 202 and finish in the background, so the shape of each tab
 * is the same: choose a source, name the dataset, submit, watch the job.
 */
import { onMounted, ref } from 'vue'

import { api } from '@/api/client'
import type { Health, Job, StudyFiles } from '@/api/types'
import FileTree from '@/components/FileTree.vue'
import JobProgress from '@/components/JobProgress.vue'
import { useAsync } from '@/composables/useAsync'
import { useJob } from '@/composables/useJob'

type Tab = 'mzml' | 'mzpeak' | 'metabolights'

const tab = ref<Tab>('mzml')
const name = ref('')
const submitError = ref<string | null>(null)
const { job, watch: watchJob, clear } = useJob()
const { data: health, run: loadHealth } = useAsync<Health>()

// mzML
const mzmlFiles = ref<string[]>([])
const partition = ref(false)

// mzPeak
const archives = ref<string[]>([])
const link = ref<'reference' | 'copy'>('reference')

// MetaboLights
const studyId = ref('')
const study = useAsync<StudyFiles>()
const chosenAssays = ref<string[]>([])
const include = ref('')
const exclude = ref('')

function switchTo(next: Tab) {
  tab.value = next
  submitError.value = null
  clear()
}

async function submit(action: () => Promise<Job>) {
  submitError.value = null
  clear()
  try {
    watchJob(await action())
  } catch (exc) {
    submitError.value = (exc as Error).message
  }
}

const createMzml = () =>
  submit(() =>
    api.ingestMzml({
      files: mzmlFiles.value,
      name: name.value.trim(),
      partition: partition.value,
    }),
  )

const createMzpeak = () =>
  submit(() =>
    api.ingestMzpeak({
      archives: archives.value,
      name: name.value.trim(),
      link: link.value,
    }),
  )

const createMetabolights = () =>
  submit(() =>
    api.ingestMetabolights({
      study_id: studyId.value.trim(),
      name: name.value.trim() || undefined,
      assays: chosenAssays.value,
      include: include.value.trim() ? include.value.split(',').map((s) => s.trim()) : [],
      exclude: exclude.value.trim() ? exclude.value.split(',').map((s) => s.trim()) : [],
    }),
  )

function lookupStudy() {
  chosenAssays.value = []
  study.run(() => api.studyFiles(studyId.value.trim()))
}

function toggleAssay(assay: string) {
  chosenAssays.value = chosenAssays.value.includes(assay)
    ? chosenAssays.value.filter((a) => a !== assay)
    : [...chosenAssays.value, assay]
}

onMounted(() => loadHealth(api.health))

const TABS: { id: Tab; label: string }[] = [
  { id: 'mzml', label: 'From mzML' },
  { id: 'mzpeak', label: 'From mzPeak' },
  { id: 'metabolights', label: 'From MetaboLights' },
]
</script>

<template>
  <div style="max-width: 56rem">
    <h1 class="vf-text-body--1 vf-u-margin__bottom--400">New dataset</h1>

    <nav class="vf-u-margin__bottom--600">
      <ul class="vf-tabs__list">
        <li v-for="item in TABS" :key="item.id" class="vf-tabs__item">
          <button
            type="button"
            class="vf-tabs__link"
            :class="tab === item.id ? 'is-active' : ''"
            :aria-selected="tab === item.id"
            @click="switchTo(item.id)"
          >
            {{ item.label }}
          </button>
        </li>
      </ul>
    </nav>

    <!-- mzML -->
    <form
      v-if="tab === 'mzml'"
      class="vf-stack vf-stack--400"
      @submit.prevent="createMzml"
    >
      <p class="vf-text-body--3">
        Converts mzML files into a new dataset. Files are read from the server,
        not uploaded.
      </p>
      <FileTree v-model="mzmlFiles" accept="mzml" multiple />

      <div class="vf-grid vf-grid__col-2 vf-u-grid-gap--400">
        <div class="vf-form__item">
          <label class="vf-form__label" for="mzml-name">Dataset name</label>
          <input
            id="mzml-name"
            v-model="name"
            class="vf-form__input mz-field"
            placeholder="e.g. qc-batch-1"
            required
          />
        </div>
        <div class="vf-form__item vf-form__item--checkbox">
          <input
            id="mzml-partition"
            v-model="partition"
            type="checkbox"
            class="vf-form__checkbox"
          />
          <label class="vf-form__label" for="mzml-partition">
            Partition by source file
            <span class="vf-form__helper">
              one directory per file; helps when filtering by source
            </span>
          </label>
        </div>
      </div>

      <div>
        <button
          class="vf-button vf-button--primary vf-button--sm"
          :disabled="!mzmlFiles.length || !name.trim()"
        >
          Convert {{ mzmlFiles.length || '' }} file{{ mzmlFiles.length === 1 ? '' : 's' }}
        </button>
      </div>
    </form>

    <!-- mzPeak -->
    <form
      v-else-if="tab === 'mzpeak'"
      class="vf-stack vf-stack--400"
      @submit.prevent="createMzpeak"
    >
      <p class="vf-text-body--3">
        Indexes mzPeak archives. By default they are read where they are and
        never modified or copied — only an index is written.
      </p>
      <FileTree v-model="archives" accept="mzpeak" multiple />

      <div class="vf-grid vf-grid__col-2 vf-u-grid-gap--400">
        <div class="vf-form__item">
          <label class="vf-form__label" for="mzpeak-name">Dataset name</label>
          <input
            id="mzpeak-name"
            v-model="name"
            class="vf-form__input mz-field"
            required
          />
        </div>
        <div class="vf-form__item">
          <label class="vf-form__label" for="mzpeak-link">Archives</label>
          <select
            id="mzpeak-link"
            v-model="link"
            class="vf-form__select mz-field"
          >
            <option value="reference">Reference in place (recommended)</option>
            <option value="copy">Copy into the dataset</option>
          </select>
        </div>
      </div>

      <div>
        <button
          class="vf-button vf-button--primary vf-button--sm"
          :disabled="!archives.length || !name.trim()"
        >
          Index {{ archives.length || '' }} archive{{ archives.length === 1 ? '' : 's' }}
        </button>
      </div>
    </form>

    <!-- MetaboLights -->
    <form v-else class="vf-stack vf-stack--400" @submit.prevent="createMetabolights">
      <div
        v-if="health && !health.extras.metabolights"
        class="vf-banner vf-banner--notice vf-u-background-color--yellow--light
          vf-u-padding--400"
      >
        <p class="vf-banner__text">
          MetaboLights support is not installed. Add it with
          <code class="mz-mono">uv pip install -e '.[metabolights]'</code>
          in <span class="mz-mono">mzstack-app-api</span>.
        </p>
      </div>

      <p class="vf-text-body--3">
        Downloads a study's mzML over FTP and converts it, keeping the study's
        sample annotation alongside. Large studies take a long time.
      </p>

      <div class="mz-cluster">
        <div class="vf-form__item" style="flex: 1">
          <label class="vf-form__label" for="study-id">Study accession</label>
          <input
            id="study-id"
            v-model="studyId"
            class="vf-form__input mz-field"
            placeholder="MTBLS341"
            required
          />
        </div>
        <button
          type="button"
          style="margin: 0;"
          class="vf-button vf-button--secondary vf-button--sm"
          :disabled="!studyId.trim() || study.loading.value"
          @click="lookupStudy"
        >
          {{ study.loading.value ? 'Looking up…' : 'List files' }}
        </button>
      </div>

      <p v-if="study.error.value" class="vf-text-body--3 vf-u-text-color--red">
        {{ study.error.value }}
      </p>

      <!-- Only the ISA-Tab is fetched to build this list, so the user can see
           what a study holds before committing to the download. -->
      <div v-if="study.data.value" class="mz-panel vf-u-padding--400">
        <p class="vf-text-body--3 vf-u-margin__bottom--200">
          {{ study.data.value.n_files }} mzML files across
          {{ study.data.value.assays.length }} assay(s). Leave all unticked to
          take everything.
        </p>
        <ul class="vf-stack vf-stack--200" style="list-style: none; padding: 0">
          <li
            v-for="assay in study.data.value.assays"
            :key="assay"
            class="mz-cluster mz-cluster--tight vf-text-body--3"
          >
            <input
              type="checkbox"
              class="vf-form__checkbox"
              :checked="chosenAssays.includes(assay)"
              @change="toggleAssay(assay)"
            />
            <span class="mz-mono vf-text-body--4">{{ assay }}</span>
            <span
              class="mz-push-right mz-tabular vf-text-body--4 vf-u-text-color--grey"
            >
              {{ study.data.value.files.filter((f) => f.assay === assay).length }} files
            </span>
          </li>
        </ul>
      </div>

      <div class="vf-grid vf-grid__col-3 vf-u-grid-gap--400">
        <div class="vf-form__item">
          <label class="vf-form__label" for="ml-name">Dataset name</label>
          <input
            id="ml-name"
            v-model="name"
            class="vf-form__input mz-field"
            :placeholder="studyId || 'MTBLS341'"
          />
        </div>
        <div class="vf-form__item">
          <label class="vf-form__label" for="ml-include">Include (globs)</label>
          <input
            id="ml-include"
            v-model="include"
            class="vf-form__input mz-field"
            placeholder="*_POS_*"
          />
        </div>
        <div class="vf-form__item">
          <label class="vf-form__label" for="ml-exclude">Exclude (globs)</label>
          <input
            id="ml-exclude"
            v-model="exclude"
            class="vf-form__input mz-field"
            placeholder="*blank*"
          />
        </div>
      </div>

      <div>
        <button class="vf-button vf-button--primary vf-button--sm" :disabled="!studyId.trim()">
          Fetch and convert
        </button>
      </div>
    </form>

    <p
      v-if="submitError"
      class="vf-text-body--3 vf-u-text-color--red vf-u-margin__top--400"
    >
      {{ submitError }}
    </p>
    <JobProgress v-if="job" :job="job" class="vf-u-margin__top--400" />
  </div>
</template>
