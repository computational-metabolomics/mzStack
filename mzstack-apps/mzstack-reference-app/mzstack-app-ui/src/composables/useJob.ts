/**
 * Watching a background job.
 *
 * Ingests answer 202 and finish later, so the page polls. The interval backs
 * off as the job runs on; a MetaboLights download can take hours.
 */

import { onUnmounted, ref } from 'vue'

import { api } from '@/api/client'
import type { Job } from '@/api/types'

const FIRST_INTERVAL = 700
const MAX_INTERVAL = 5000

export function useJob() {
  const job = ref<Job | null>(null)
  const error = ref<string | null>(null)
  let timer: number | undefined
  let interval = FIRST_INTERVAL

  function stop() {
    if (timer !== undefined) {
      window.clearTimeout(timer)
      timer = undefined
    }
  }

  function finished(status: string) {
    return status === 'succeeded' || status === 'failed'
  }

  async function poll(id: string) {
    try {
      const current = await api.job(id)
      job.value = current
      if (finished(current.status)) {
        stop()
        return
      }
    } catch (exc) {
      error.value = (exc as Error).message
      stop()
      return
    }
    interval = Math.min(interval * 1.4, MAX_INTERVAL)
    timer = window.setTimeout(() => poll(id), interval)
  }

  /** Start watching `started`, replacing whatever was being watched. */
  function watch(started: Job) {
    stop()
    interval = FIRST_INTERVAL
    error.value = null
    job.value = started
    if (!finished(started.status)) timer = window.setTimeout(() => poll(started.id), interval)
  }

  function clear() {
    stop()
    job.value = null
    error.value = null
  }

  onUnmounted(stop)

  return { job, error, watch, clear }
}
