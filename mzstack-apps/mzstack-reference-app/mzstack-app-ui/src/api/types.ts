/**
 * The shapes the API returns.
 *
 * Two conventions carry through from the mzStack format and must not be
 * quietly changed here: retention time is in **seconds**, and polarity is
 * 1 for positive and 0 for negative. Views convert for display only.
 */

export interface ApiErrorBody {
  error: { type: string; message: string }
}

export interface Health {
  status: string
  mzstack_version: string
  format_version: string
  workspace: string
  data_roots: string[]
  extras: { massql: boolean; metabolights: boolean; mzpeak: boolean }
  limits: { max_points: number; max_rows: number }
}

export interface DatasetSummary {
  id: string
  path: string
  source: 'workspace' | 'registered'
  kind?: string
  format?: string
  version?: string
  n_spectra?: number
  n_runs?: number
  error?: string
}

export interface RunInfo {
  run_id: string
  kind: string
  path: string | null
  n_spectra: number
  uid_base: number
  partitioning: string[]
  projections: Record<string, boolean>
}

export interface ProjectionState {
  current: Record<string, { runs: string[]; complete: boolean }>
  types?: string[]
}

export interface DatasetDetail extends DatasetSummary {
  // Optional on `DatasetSummary`: the listing includes datasets it could not
  // open. Always present here.
  kind: string
  format: string
  version: string
  n_spectra: number
  generation: number
  /**
   * The runs themselves. A detail response carries no `n_runs`; reading it on
   * a `DatasetDetail` gives `undefined`.
   */
  runs: RunInfo[]
  variables: string[]
  has_sample_metadata: boolean
  ms_levels: Record<string, number>
  /** [min, max] in seconds, or null for an empty dataset. */
  rtime_range: [number, number] | null
  polarities: number[]
  /**
   * Distinct `dataOrigin` values — samples, not runs. One conversion of many
   * mzML files yields one run holding them all, so this is the count of
   * traces a per-sample chromatogram draws.
   */
  n_samples: number
  projections: ProjectionState
}

export type Row = Record<string, unknown>

export interface SpectraPage {
  columns: string[]
  rows: Row[]
  total: number
  limit: number
  offset: number
}

export interface Spectrum {
  spectrum_id: number
  metadata: Row
  mz: number[]
  intensity: number[]
  n_peaks: number
  downsampled: boolean
  base_peak: { mz: number; intensity: number } | null
}

export type ChromatogramType = 'tic' | 'bpc' | 'xic'

/** The only column a chromatogram can be split by: one series per sample. */
export const GROUP_BY_SAMPLE = 'dataOrigin'

/** One trace's parallel arrays. */
export interface ChromatogramSeries {
  /** Present only when the response is grouped. */
  name?: string
  /** Seconds. */
  rtime: number[]
  intensity: number[]
  spectrum_ids: number[]
  n_points: number
  n_matching?: number
}

interface ChromatogramMeta {
  type: ChromatogramType
  ms_level: number
  mz: number | null
  mz_window?: [number, number]
}

/**
 * Ungrouped: the arrays sit at the top level. Grouped: they move into
 * `series`, one entry per sample, and `group_by` names the column that split
 * them. `series` is the discriminant.
 */
export type Chromatogram = ChromatogramMeta &
  ChromatogramSeries & { group_by?: undefined; series?: undefined }

export type GroupedChromatogram = ChromatogramMeta & {
  group_by: string
  series: ChromatogramSeries[]
}

export type AnyChromatogram = Chromatogram | GroupedChromatogram

export function isGrouped(
  chromatogram: AnyChromatogram,
): chromatogram is GroupedChromatogram {
  return Array.isArray((chromatogram as GroupedChromatogram).series)
}

/** Either shape as a list of traces, so drawing has one path. */
export function toSeries(chromatogram: AnyChromatogram): ChromatogramSeries[] {
  return isGrouped(chromatogram) ? chromatogram.series : [chromatogram]
}

/**
 * A MassQL result, which is a page of the spectra table: the API projects the
 * scans a query selected back through the spectra path, so `columns` here is
 * the same list `SpectraPage` carries, and it pages the same way.
 *
 * `spectrum_ids` is the only addition -- every id the query selected, not
 * just this page's, which a filter has no equivalent of.
 */
export interface QueryResult extends SpectraPage {
  scan_column: string | null
  spectrum_ids: number[]
}

export interface SampleMetadata {
  columns: string[]
  rows: Row[]
  key: string
}

export type FileKind = 'directory' | 'mzml' | 'mzpeak' | 'mzstack' | 'other'

export interface FileEntry {
  name: string
  path: string
  kind: FileKind
  size: number | null
}

export interface DirectoryListing {
  path: string
  parent: string | null
  roots: string[]
  entries: FileEntry[]
}

export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed'

export interface Job {
  id: string
  kind: string
  params: Record<string, unknown>
  status: JobStatus
  created_at: string
  started_at: string | null
  finished_at: string | null
  result: { dataset_id?: string; path?: string; n_spectra?: number } | null
  error: string | null
  log: string[]
}

export interface StudyFile {
  assay: string
  remote_path: string
  name: string
  sample_name: string
  assay_name: string
  column: string
}

export interface StudyFiles {
  study_id: string
  n_files: number
  assays: string[]
  files: StudyFile[]
}

/** How a mass window is given: absolute, or relative to the m/z. */
export type ToleranceUnit = 'da' | 'ppm'

/**
 * A tolerance as the pair of parameters the API takes.
 *
 * The library takes the wider of the Da and ppm windows, and the API defaults
 * `ppm` to 20. The unit that was not chosen is therefore sent as an explicit
 * zero.
 */
export function toleranceParams(
  value: number,
  unit: ToleranceUnit,
): { tolerance: number; ppm: number } {
  return unit === 'ppm' ? { tolerance: 0, ppm: value } : { tolerance: value, ppm: 0 }
}

/** The filter a spectra request carries. Retention time in seconds. */
export interface SpectraFilter {
  ms_level: number[]
  rt_min: number | null
  rt_max: number | null
  polarity: number[]
  data_origin: string[]
  precursor_mz: number[]
  contains_mz: number[]
  tolerance: number
  ppm: number
}

export function emptyFilter(): SpectraFilter {
  return {
    ms_level: [],
    rt_min: null,
    rt_max: null,
    polarity: [],
    data_origin: [],
    precursor_mz: [],
    contains_mz: [],
    ...toleranceParams(0.01, 'da'),
  }
}
