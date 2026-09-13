<script setup lang="ts">
/**
 * Every job this server has run since it started.
 *
 * Jobs live in memory, so this list empties on restart; the datasets they
 * produced do not.
 */
import { computed, onMounted, onUnmounted, ref } from 'vue'

import { api } from '@/api/client'
import type { Job } from '@/api/types'
import JobProgress from '@/components/JobProgress.vue'
import { useAsync } from '@/composables/useAsync'

const { data: jobs, error, run } = useAsync<Job[]>([])
const expanded = ref<string | null>(null)
let timer: number | undefined

const anyRunning = computed(() =>
  (jobs.value ?? []).some((job) => job.status === 'queued' || job.status === 'running'),
)

function refresh() {
  run(api.jobs)
}

onMounted(() => {
  refresh()
  // Only poll while something is actually in flight.
  timer = window.setInterval(() => {
    if (anyRunning.value) refresh()
  }, 2000)
})

onUnmounted(() => window.clearInterval(timer))
</script>

<template>
  <div class="vf-stack vf-stack--400">
    <div class="mz-cluster">
      <h1 class="vf-text-body--1">Jobs</h1>
      <button
        class="vf-button vf-button--secondary vf-button--sm mz-push-right"
        @click="refresh"
      >
        Refresh
      </button>
    </div>

    <p v-if="error" class="vf-text-body--3 vf-u-text-color--red">{{ error }}</p>

    <div
      v-else-if="!jobs?.length"
      class="mz-panel vf-u-padding--1200"
      style="text-align: center"
    >
      <p class="vf-text-body--2">Nothing has run yet.</p>
      <p class="vf-text-body--3 vf-u-text-color--grey--dark">
        Conversions and downloads started from
        <RouterLink to="/create">New dataset</RouterLink>
        appear here.
      </p>
    </div>

    <ul v-else class="vf-stack vf-stack--200" style="list-style: none; padding: 0">
      <li v-for="job in jobs" :key="job.id">
        <JobProgress
          :job="job"
          :compact="expanded !== job.id"
          class="mz-row--clickable"
          @click="expanded = expanded === job.id ? null : job.id"
        />
      </li>
    </ul>
  </div>
</template>
