<script setup lang="ts">
/**
 * What a background job is doing.
 *
 * The library reports work as it finishes files, not as a fraction, so there
 * is no percentage. This shows the log it prints.
 */
import { computed } from 'vue'

import type { Job } from '@/api/types'

const props = defineProps<{ job: Job | null; compact?: boolean }>()

const running = computed(
  () => props.job?.status === 'queued' || props.job?.status === 'running',
)

/** Status as a vf background tint; the border follows it in CSS. */
const tone = computed(() => {
  switch (props.job?.status) {
    case 'succeeded':
      return 'vf-u-background-color--green--lightest'
    case 'failed':
      return 'vf-u-background-color--red--light'
    default:
      return 'vf-u-background-color-ui--grey--light'
  }
})

const emptyResult = computed(
  () => props.job?.status === 'succeeded' && props.job.result?.n_spectra === 0,
)

function elapsed(job: Job): string {
  const start = new Date(job.started_at ?? job.created_at).getTime()
  const end = job.finished_at ? new Date(job.finished_at).getTime() : Date.now()
  const seconds = Math.max(0, Math.round((end - start) / 1000))
  if (seconds < 60) return `${seconds}s`
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
}
</script>

<template>
  <div v-if="job" class="mz-job vf-text-body--3" :class="tone">
    <div class="mz-cluster mz-cluster--tight">
      <span v-if="running" class="mz-job__pulse" aria-hidden="true"></span>
      <strong style="text-transform: capitalize">{{ job.kind }}</strong>
      <span aria-hidden="true">·</span>
      <span>{{ job.status }}</span>
      <span class="mz-push-right mz-tabular vf-text-body--4">
        {{ elapsed(job) }}
      </span>
    </div>

    <p v-if="job.error" class="mz-mono vf-text-body--4 vf-u-text--break">
      {{ job.error }}
    </p>

    <p v-else-if="emptyResult" class="vf-text-body--4">
      Finished, but no spectra were read. Check that the files you picked are
      what you expected.
    </p>

    <p
      v-else-if="job.status === 'succeeded' && job.result?.dataset_id"
      class="vf-text-body--4"
    >
      Created
      <RouterLink :to="`/datasets/${job.result.dataset_id}`">
        {{ job.result.dataset_id }}
      </RouterLink>
      <span v-if="job.result.n_spectra !== undefined">
        with {{ job.result.n_spectra }} spectra.
      </span>
    </p>

    <pre
      v-if="!compact && job.log.length"
      class="mz-job__log mz-mono"
    >{{ job.log.slice(-40).join('\n') }}</pre>
  </div>
</template>
