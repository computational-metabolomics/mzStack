<script setup lang="ts">
/**
 * The frame around one dataset: identity, tabs, and the detail every tab
 * needs, fetched once and provided to the children.
 */
import { onMounted, provide, watch } from 'vue'

import { api } from '@/api/client'
import type { DatasetDetail } from '@/api/types'
import { useAsync } from '@/composables/useAsync'
import { DATASET_KEY } from '@/views/dataset/key'

const props = defineProps<{ id: string }>()

const { data: dataset, error, loading, run } = useAsync<DatasetDetail>()

function load() {
  run(() => api.dataset(props.id))
}

provide(DATASET_KEY, { dataset, reload: load })

onMounted(load)
watch(() => props.id, load)

// Two tabs: what this dataset is, and everything you can ask it.
const tabs = [
  { name: 'dataset', label: 'Overview' },
  { name: 'dataset-data', label: 'Data' },
]
</script>

<template>
  <div>
    <nav class="vf-breadcrumbs" aria-label="Breadcrumb">
      <ul class="vf-breadcrumbs__list vf-list vf-list--inline">
        <li class="vf-breadcrumbs__item">
          <RouterLink to="/datasets" class="vf-breadcrumbs__link">
            Datasets
          </RouterLink>
        </li>
        <li class="vf-breadcrumbs__item" aria-current="page">{{ id }}</li>
      </ul>
    </nav>

    <div class="mz-cluster vf-u-margin__bottom--400">
      <h1 class="vf-text-body--1">{{ id }}</h1>
      <template v-if="dataset">
        <span class="vf-text-body--3 mz-tabular">
          {{ dataset.n_spectra.toLocaleString() }} spectra
        </span>
        <span class="vf-text-body--3">{{ dataset.kind }}</span>
        <span
          v-for="(count, level) in dataset.ms_levels"
          :key="level"
          class="vf-badge mz-tabular"
        >
          MS{{ level }}: {{ count.toLocaleString() }}
        </span>
      </template>
    </div>

    <p v-if="error" class="vf-text-body--3 vf-u-text-color--red">{{ error }}</p>
    <p v-else-if="loading && !dataset" class="vf-text-body--3">Loading…</p>

    <template v-else-if="dataset">
      <!--
        Router-driven: only vf-tabs' classes are used, not its own JavaScript,
        which swaps in-page panels.
      -->
      <nav class="vf-u-margin__bottom--600">
        <ul class="vf-tabs__list">
          <li v-for="tab in tabs" :key="tab.name" class="vf-tabs__item">
            <!-- Links, not ARIA tabs: these navigate, and RouterLink marks
                 the current one with `aria-current` on its own. -->
            <RouterLink
              :to="{ name: tab.name, params: { id } }"
              class="vf-tabs__link"
              exact-active-class="is-active"
            >
              {{ tab.label }}
            </RouterLink>
          </li>
        </ul>
      </nav>

      <RouterView />
    </template>
  </div>
</template>
