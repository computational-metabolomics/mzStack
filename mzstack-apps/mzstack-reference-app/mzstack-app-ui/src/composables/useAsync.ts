/** Running a request and rendering its three states without repeating it. */

import { ref, shallowRef } from 'vue'

import { ApiError } from '@/api/client'

export function useAsync<T>(initial: T | null = null) {
  const data = shallowRef<T | null>(initial)
  const error = ref<string | null>(null)
  const loading = ref(false)

  /**
   * Runs `task`, keeping only the newest result. An earlier, slower response
   * arriving late is discarded.
   */
  let token = 0
  async function run(task: () => Promise<T>): Promise<T | null> {
    const mine = ++token
    loading.value = true
    error.value = null
    try {
      const result = await task()
      if (mine !== token) return null
      data.value = result
      return result
    } catch (exc) {
      if (mine !== token) return null
      error.value =
        exc instanceof ApiError ? exc.message : (exc as Error).message
      return null
    } finally {
      if (mine === token) loading.value = false
    }
  }

  return { data, error, loading, run }
}
