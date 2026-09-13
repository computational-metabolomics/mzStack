import type { InjectionKey, ShallowRef } from 'vue'

import type { DatasetDetail } from '@/api/types'

/** What `DatasetView` fetches once and its tabs read. */
export interface DatasetContext {
  dataset: ShallowRef<DatasetDetail | null>
  reload: () => void
}

export const DATASET_KEY: InjectionKey<DatasetContext> = Symbol('dataset')
