import { createRouter, createWebHistory } from 'vue-router'

/**
 * Dataset views are nested: the id is read once, and every tab keeps the
 * dataset in the URL.
 */
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: () => import('@/views/HomeView.vue') },
    {
      path: '/datasets',
      name: 'datasets',
      component: () => import('@/views/DatasetsView.vue'),
    },
    {
      path: '/create',
      name: 'create',
      component: () => import('@/views/CreateView.vue'),
    },
    {
      path: '/jobs',
      name: 'jobs',
      component: () => import('@/views/JobsView.vue'),
    },
    {
      path: '/datasets/:id',
      component: () => import('@/views/DatasetView.vue'),
      props: true,
      children: [
        {
          path: '',
          name: 'dataset',
          component: () => import('@/views/dataset/OverviewTab.vue'),
        },
        {
          path: 'data',
          name: 'dataset-data',
          component: () => import('@/views/dataset/DataTab.vue'),
        },
        // Links to the former Spectra, Query and Chromatogram tabs resolve to
        // the Data tab. The id is carried across by hand: a redirect does not
        // inherit params.
        ...['spectra', 'query', 'chromatogram'].map((path) => ({
          path,
          redirect: (to: { params: { id?: string | string[] } }) => ({
            name: 'dataset-data',
            params: { id: to.params.id },
          }),
        })),
      ],
    },
  ],
})
