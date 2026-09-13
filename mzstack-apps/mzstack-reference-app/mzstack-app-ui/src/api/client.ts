/**
 * The only place that talks to the API.
 *
 * Every failure arrives as `{error: {type, message}}`, and the API writes
 * those messages for a person to read -- several come straight from the
 * mzstack library and name the package to install or the variable that does
 * not exist. `ApiError` keeps them intact for views to show.
 */

import type {
  AnyChromatogram,
  ChromatogramType,
  DatasetDetail,
  DatasetSummary,
  DirectoryListing,
  Health,
  Job,
  ProjectionState,
  QueryResult,
  SampleMetadata,
  SpectraFilter,
  SpectraPage,
  Spectrum,
  StudyFiles,
} from './types'

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly type: string,
    message: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      ...init,
      headers: {
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
        ...init?.headers,
      },
    })
  } catch {
    throw new ApiError(0, 'NetworkError', 'Could not reach the mzStack API.')
  }

  if (response.status === 204) return undefined as T

  const text = await response.text()
  let body: unknown = null
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    throw new ApiError(response.status, 'ParseError', text.slice(0, 300))
  }

  if (!response.ok) {
    const error = (body as { error?: { type: string; message: string } })?.error
    throw new ApiError(
      response.status,
      error?.type ?? 'Error',
      error?.message ?? `Request failed with ${response.status}`,
    )
  }
  return body as T
}

function post<T>(path: string, payload: unknown): Promise<T> {
  return request<T>(path, { method: 'POST', body: JSON.stringify(payload) })
}

/** Builds a query string, dropping empties and expanding lists. */
function params(values: Record<string, unknown>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(values)) {
    if (value === null || value === undefined || value === '') continue
    if (Array.isArray(value)) {
      if (value.length) search.set(key, value.join(','))
    } else {
      search.set(key, String(value))
    }
  }
  const query = search.toString()
  return query ? `?${query}` : ''
}

export const api = {
  health: () => request<Health>('/api/health'),

  // -- datasets ------------------------------------------------------------

  datasets: () =>
    request<{ datasets: DatasetSummary[] }>('/api/datasets').then((r) => r.datasets),

  dataset: (id: string) => request<DatasetDetail>(`/api/datasets/${id}`),

  samples: (id: string) => request<SampleMetadata>(`/api/datasets/${id}/samples`),

  registerDataset: (path: string) =>
    post<DatasetSummary>('/api/datasets/register', { path }),

  unregisterDataset: (id: string) =>
    request<void>(`/api/datasets/${id}/register`, { method: 'DELETE' }),

  projections: (id: string) =>
    request<ProjectionState>(`/api/datasets/${id}/projections`),

  buildProjection: (id: string, type: string) =>
    post<Job>(`/api/datasets/${id}/projections`, { type }),

  dropProjection: (id: string, type: string) =>
    request<void>(`/api/datasets/${id}/projections/${type}`, { method: 'DELETE' }),

  // -- spectra -------------------------------------------------------------

  spectra: (id: string, filter: SpectraFilter, limit: number, offset: number) =>
    request<SpectraPage>(
      `/api/datasets/${id}/spectra` +
        params({
          ms_level: filter.ms_level,
          rt_min: filter.rt_min,
          rt_max: filter.rt_max,
          polarity: filter.polarity,
          data_origin: filter.data_origin,
          precursor_mz: filter.precursor_mz,
          contains_mz: filter.contains_mz,
          // Only meaningful alongside an m/z, and confusing in the URL
          // otherwise.
          tolerance:
            filter.precursor_mz.length || filter.contains_mz.length
              ? filter.tolerance
              : null,
          ppm:
            filter.precursor_mz.length || filter.contains_mz.length
              ? filter.ppm
              : null,
          limit,
          offset,
        }),
    ),

  spectrum: (id: string, spectrumId: number) =>
    request<Spectrum>(`/api/datasets/${id}/spectra/${spectrumId}`),

  // -- chromatograms -------------------------------------------------------

  chromatogram: (
    id: string,
    options: {
      type: ChromatogramType
      ms_level?: number
      rt_min?: number | null
      rt_max?: number | null
      mz?: number | null
      tolerance?: number
      ppm?: number
      /** `dataOrigin` splits the trace into one series per sample. */
      group_by?: string | null
    },
  ) =>
    request<AnyChromatogram>(
      `/api/datasets/${id}/chromatogram` + params(options),
    ),

  // -- query ---------------------------------------------------------------

  query: (
    id: string,
    payload: {
      query: string
      engine?: string
      ms_level?: number | null
      limit?: number
      offset?: number
    },
  ) => post<QueryResult>(`/api/datasets/${id}/query`, payload),

  // -- files and ingest ----------------------------------------------------

  files: (path?: string | null) =>
    request<DirectoryListing>('/api/files' + params({ path })),

  ingestMzml: (payload: { files: string[]; name: string; partition: boolean }) =>
    post<Job>('/api/ingest/mzml', payload),

  ingestMzpeak: (payload: { archives: string[]; name: string; link: string }) =>
    post<Job>('/api/ingest/mzpeak', payload),

  addArchives: (payload: {
    dataset_id: string
    archives: string[]
    link: string
  }) => post<Job>('/api/ingest/mzpeak/add', payload),

  studyFiles: (studyId: string) =>
    request<StudyFiles>(`/api/metabolights/${studyId}/files`),

  ingestMetabolights: (payload: {
    study_id: string
    name?: string
    assays?: string[]
    samples?: string[]
    include?: string[]
    exclude?: string[]
  }) => post<Job>('/api/ingest/metabolights', payload),

  // -- jobs ----------------------------------------------------------------

  jobs: () => request<{ jobs: Job[] }>('/api/jobs').then((r) => r.jobs),

  job: (jobId: string) => request<Job>(`/api/jobs/${jobId}`),
}
