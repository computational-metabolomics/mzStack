/**
 * Plotly, loaded once and on demand.
 *
 * The largest dependency here by a wide margin (~4 MB against the ~97 kB rest
 * of the bundle), and most of the app -- listings, ingest, job progress --
 * never plots. It stays out of the initial bundle and is fetched when the
 * first chart mounts.
 */

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Plotly = any

let pending: Promise<Plotly> | null = null

export function loadPlotly(): Promise<Plotly> {
  if (!pending) pending = import('plotly.js-dist-min').then((m) => m.default ?? m)
  return pending
}

/**
 * A Visual Framework token, or its published value if the stylesheet has not
 * parsed yet. Plotly takes colours as plain strings, so the custom properties
 * have to be resolved here rather than handed over as `var(...)`.
 */
function token(name: string, fallback: string): string {
  if (typeof document === 'undefined') return fallback
  const value = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim()
  return value || fallback
}

const RULE = token('--vf-ui-color--grey', '#d8d8d8')
const GRID = token('--vf-color--neutral--100', '#f3f3f3')
const INK = token('--vf-color--grey--dark', '#54585a')
const INK_STRONG = token('--vf-color__text--secondary', '#373a36')

export const PRIMARY = token('--vf-color--blue', '#3b6fb6')

/**
 * One colour per sample, in the vf categorical palette. Ordered so adjacent
 * series stay distinguishable, and long enough that a run of samples wraps
 * rather than collides for any realistic count.
 */
export const SERIES_COLOURS = [
  token('--vf-color--blue', '#3b6fb6'),
  token('--vf-color--green', '#18974c'),
  token('--vf-color--purple', '#734595'),
  token('--vf-color--orange--dark', '#b65417'),
  token('--vf-color--red', '#d41645'),
  token('--vf-color--bright-green--dark', '#7fb428'),
  token('--vf-color--blue--dark', '#193f90'),
  token('--vf-color--yellow--dark', '#ffb81c'),
  token('--vf-color--grey--dark', '#54585a'),
  token('--vf-color--purple--light', '#cba3d8'),
]

/** The colour for series `i`, wrapping when there are more than the palette. */
export function seriesColour(i: number): string {
  return SERIES_COLOURS[i % SERIES_COLOURS.length]
}

const FONT = "'IBM Plex Sans', Helvetica, Arial, sans-serif"

export const AXIS = {
  showline: true,
  linecolor: RULE,
  zeroline: false,
  gridcolor: GRID,
  ticks: 'outside' as const,
  tickcolor: RULE,
  tickfont: { size: 11, color: INK },
  titlefont: { size: 12, color: INK_STRONG },
}

/**
 * The intensity axis.
 *
 * Intensities run to six and seven digits, so the ticks are in E notation.
 * `tozero` anchors the axis at zero, which is an intensity's real floor.
 */
export const INTENSITY_AXIS = {
  ...AXIS,
  tickformat: '.2e',
  rangemode: 'tozero' as const,
}

export const LAYOUT = {
  // The left margin holds an E-notation intensity tick (`1.00e+8`) and the
  // axis title beside it. Plotly does not reflow a fixed margin to suit its
  // labels, so a narrower one puts the title on top of the ticks.
  margin: { l: 88, r: 16, t: 16, b: 44 },
  paper_bgcolor: 'white',
  plot_bgcolor: 'white',
  showlegend: false,
  hovermode: 'closest' as const,
  font: { family: FONT },
}

/** The vertical rule marking the selected scan. */
export function marker(x: number, axisRef = 'y') {
  return {
    type: 'line' as const,
    xref: 'x' as const,
    yref: axisRef === 'y' ? ('y domain' as const) : (`${axisRef} domain` as const),
    x0: x,
    x1: x,
    y0: 0,
    y1: 1,
    line: { color: token('--vf-color--red', '#d41645'), width: 1, dash: 'dot' },
    layer: 'above' as const,
  }
}

export const CONFIG = {
  displaylogo: false,
  responsive: true,
  // Box zoom is the gesture that matters for spectra and chromatograms; the
  // rest of the mode bar is noise.
  modeBarButtonsToRemove: ['select2d', 'lasso2d', 'autoScale2d'],
  toImageButtonOptions: { format: 'png' as const, scale: 2 },
}

let webgl: boolean | null = null

/** Whether this browser can actually draw a WebGL trace. */
function hasWebGL(): boolean {
  if (webgl === null) {
    try {
      const canvas = document.createElement('canvas')
      webgl = !!(
        canvas.getContext('webgl') || canvas.getContext('experimental-webgl')
      )
    } catch {
      webgl = false
    }
  }
  return webgl
}

/**
 * `scattergl` or `scatter`, by point count and capability.
 *
 * `scattergl` is used above a few thousand points, where a profile spectrum
 * reaches tens of thousands. Where WebGL is unavailable -- a headless
 * browser, a locked-down machine, a blocklisted driver -- a gl trace renders
 * as nothing at all, so SVG is the default.
 */
export function traceType(points: number): 'scatter' | 'scattergl' {
  return points > 3000 && hasWebGL() ? 'scattergl' : 'scatter'
}

/**
 * Sticks as one line trace: each peak is drawn as a `null`-separated pair of
 * points from the baseline to its apex. One trace of 3n points, not n traces,
 * at the peak counts a centroided spectrum reaches.
 */
export function sticks(mz: number[], intensity: number[]) {
  const x: (number | null)[] = []
  const y: (number | null)[] = []
  for (let i = 0; i < mz.length; i++) {
    x.push(mz[i], mz[i], null)
    y.push(0, intensity[i], null)
  }
  return { x, y }
}
