<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { api } from '@/api/client'
import type { DatasetSummary } from '@/api/types'
import FileTree from '@/components/FileTree.vue'
import { useAsync } from '@/composables/useAsync'

const { data: datasets, error, loading, run } = useAsync<DatasetSummary[]>([])

const registering = ref(false)
const chosen = ref<string[]>([])
const registerError = ref<string | null>(null)

function refresh() {
  run(api.datasets)
}

async function register() {
  registerError.value = null
  try {
    for (const path of chosen.value) await api.registerDataset(path)
    chosen.value = []
    registering.value = false
    refresh()
  } catch (exc) {
    registerError.value = (exc as Error).message
  }
}

async function unregister(id: string) {
  await api.unregisterDataset(id)
  refresh()
}

onMounted(refresh)
</script>

<template>
  <div class="vf-stack vf-stack--600">
    <div class="mz-cluster">
      <h1 class="vf-text-body--1">Datasets</h1>
      <div class="mz-cluster mz-cluster--tight mz-push-right">
        <button
          class="vf-button vf-button--secondary vf-button--sm"
          @click="registering = !registering"
        >
          Register existing
        </button>
        <RouterLink
          to="/create"
          class="vf-button vf-button--primary vf-button--sm"
        >
          New dataset
        </RouterLink>
      </div>
    </div>

    <!-- Registering points the app at a dataset that lives outside the
         workspace; nothing is copied or moved. -->
    <section v-if="registering" class="mz-panel vf-u-padding--400">
      <p class="vf-text-body--3 vf-u-margin__bottom--400">
        Pick an mzStack dataset directory. It stays where it is — only its
        path is recorded.
      </p>
      <FileTree v-model="chosen" accept="mzstack" multiple />
      <p
        v-if="registerError"
        class="vf-text-body--3 vf-u-text-color--red vf-u-margin__top--200"
      >
        {{ registerError }}
      </p>
      <div class="mz-cluster mz-cluster--tight vf-u-margin__top--400">
        <button
          class="vf-button vf-button--primary vf-button--sm"
          :disabled="!chosen.length"
          @click="register"
        >
          Register {{ chosen.length || '' }}
        </button>
        <button
          class="vf-button vf-button--secondary vf-button--sm"
          @click="registering = false"
        >
          Cancel
        </button>
      </div>
    </section>

    <p v-if="error" class="vf-text-body--3 vf-u-text-color--red">{{ error }}</p>

    <p v-else-if="loading && !datasets?.length" class="vf-text-body--3">
      Loading…
    </p>

    <div
      v-else-if="!datasets?.length"
      class="mz-panel vf-u-padding--1200"
      style="text-align: center"
    >
      <p class="vf-text-body--2">No datasets yet.</p>
      <p class="vf-text-body--3 vf-u-text-color--grey--dark">
        Create one from mzML files, mzPeak archives or a MetaboLights study —
        or register a dataset you already have.
      </p>
      <RouterLink
        to="/create"
        class="vf-button vf-button--primary vf-u-margin__top--400"
      >
        New dataset
      </RouterLink>
    </div>

    <ul
      v-else
      class="vf-grid vf-grid__col-3 vf-u-grid-gap--400"
      style="list-style: none; padding: 0"
    >
      <li v-for="dataset in datasets" :key="dataset.id" class="mz-panel vf-u-padding--400">
        <div class="mz-cluster mz-cluster--tight">
          <RouterLink :to="`/datasets/${dataset.id}`" class="vf-text-body--2">
            {{ dataset.id }}
          </RouterLink>
          <span v-if="dataset.source === 'registered'" class="vf-badge">
            linked
          </span>
        </div>

        <p
          v-if="dataset.error"
          class="vf-text-body--3 vf-u-text-color--red vf-u-margin__top--200"
        >
          {{ dataset.error }}
        </p>
        <dl v-else class="mz-cluster vf-text-body--3 vf-u-margin__top--200">
          <div>
            <dt class="vf-text-body--4 vf-u-text-color--grey">spectra</dt>
            <dd class="mz-tabular">{{ dataset.n_spectra?.toLocaleString() }}</dd>
          </div>
          <div>
            <dt class="vf-text-body--4 vf-u-text-color--grey">runs</dt>
            <dd class="mz-tabular">{{ dataset.n_runs }}</dd>
          </div>
          <div>
            <dt class="vf-text-body--4 vf-u-text-color--grey">kind</dt>
            <dd>{{ dataset.kind }}</dd>
          </div>
        </dl>

        <p
          class="mz-mono vf-text-body--4 vf-u-text-color--grey vf-u-margin__top--200
            vf-u-text--nowrap"
          style="overflow: hidden; text-overflow: ellipsis"
          :title="dataset.path"
        >
          {{ dataset.path }}
        </p>

        <button
          v-if="dataset.source === 'registered'"
          class="vf-button vf-button--link vf-text-body--4 vf-u-margin__top--200"
          @click="unregister(dataset.id)"
        >
          Remove from list
        </button>
      </li>
    </ul>
  </div>
</template>
