import { inject } from 'vue'
import { useRoute } from 'vue-router'

import { DATASET_KEY } from './key'

/** The dataset a tab is showing, and its id from the route. */
export function useDataset() {
  const context = inject(DATASET_KEY)
  if (!context) throw new Error('useDataset must be used inside DatasetView')
  const route = useRoute()
  return { ...context, datasetId: String(route.params.id) }
}
