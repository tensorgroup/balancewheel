// Pure layout for the pane: how the room is split, where a lane's bead is, which glyph and color a lane gets,
// and how chart samples fold into columns. No `$` here. Times are milliseconds.

import type { Chart, Sample } from '../types'
import { clean } from './format'

export const FIGURE_ROWS = 2
export const CHART_ROWS = 11
export const CONTEXT_BAR_ROWS = 5
export const TIMELINE_ROWS = 4
/** Below this width the charts go first. */
export const CHART_MIN_COLUMNS = 40
/** The history keeps at least its title and one entry before the charts take room. */
const HISTORY_MIN_ROWS = 3

export type Room = { lanes: number; figures: number; charts: number; history: number }

/** Lanes take what they need, the charts 11 rows when there is room, the history the rest. */
export function split(rows: number, columns: number, laneCount: number): Room {
  const total = Math.max(1, Math.floor(rows))
  const lanes = Math.max(0, Math.min(laneCount, total - FIGURE_ROWS))
  const figures = Math.min(FIGURE_ROWS, total - lanes)
  const rest = Math.max(0, total - lanes - figures)
  const charts = columns >= CHART_MIN_COLUMNS && rest >= CHART_ROWS + HISTORY_MIN_ROWS ? CHART_ROWS : 0
  return { lanes, figures, charts, history: rest - charts }
}

/**
 * Cells a second the bead moves: slower as the context fills (slower = fuller context). With no known window
 * it keeps a steady middle pace.
 */
export function speed(fill: number | null): number {
  const top = 4
  if (fill === null || !Number.isFinite(fill)) return top * (0.95 - 0.85 * 0.5)
  return top * (0.95 - 0.85 * Math.max(0, Math.min(1, fill)))
}

/** The bead swings from end to end of its track, like the rim of a balance wheel. */
export function beadAt(nowMs: number, startMs: number, fill: number | null, width: number): number {
  if (width <= 1) return 0
  const steps = Math.floor((Math.max(0, nowMs - startMs) / 1000) * speed(fill))
  const period = 2 * (width - 1)
  const p = steps % period
  return p < width ? p : period - p
}

export type LaneKind = 'main' | 'subagent' | 'bw' | 'local'

const GLYPH: Record<LaneKind, string> = { main: '◆', subagent: '◇', bw: '▣', local: '▢' }

function isWide(text: string): boolean {
  const cp = text.codePointAt(0) ?? 0
  return text.includes('️') || cp >= 0x1f000 || (cp >= 0x2e80 && cp <= 0xa4cf)
    || (cp >= 0xac00 && cp <= 0xd7a3) || (cp >= 0xf900 && cp <= 0xfaff) || (cp >= 0xff00 && cp <= 0xff60)
}

/** A lane's mark, two columns wide: the seat's icon when set (an emoji fills both), else the kind's glyph. */
export function glyph(kind: LaneKind, icon: string | null): string {
  const mark = icon === null ? '' : clean(icon).trim()
  if (mark === '') return `${GLYPH[kind]} `
  const first = [...mark].slice(0, mark.includes('️') ? 2 : 1).join('')
  return isWide(first) ? first : `${first} `
}

/** Built-in family words for Claude models; `local.families` adds or overrides words. Mid tones read on both. */
export const FAMILIES: Readonly<Record<string, string>> = {
  opus: '#8b5cf6',
  sonnet: '#3b82f6',
  haiku: '#14b8a6',
  fable: '#d97706',
}

const SEAT_COLORS = ['#c2410c', '#15803d', '#1d4ed8', '#be185d', '#6d28d9', '#0e7490', '#a16207', '#4d7c0f']

/** The color of the first family word found in the model string, or null. */
export function familyColor(model: string | null, families: Readonly<Record<string, string>>): string | null {
  if (model === null) return null
  const text = model.toLowerCase()
  for (const table of [families, FAMILIES]) {
    for (const [word, color] of Object.entries(table)) {
      if (word !== '' && text.includes(word.toLowerCase())) return color
    }
  }
  return null
}

/** A seat's own color, else one picked from its name. */
export function seatColor(name: string, color: string | null): string {
  if (color !== null && color !== '') return color
  let n = 0
  for (const ch of name) n = (n * 31 + (ch.codePointAt(0) ?? 0)) >>> 0
  return SEAT_COLORS[n % SEAT_COLORS.length] ?? '#1d4ed8'
}

/** Adds a sample when a period has passed; past `cap` samples, pairs fold into one and the period doubles. */
export function pushSample(chart: Chart, sample: Sample, cap = 720): Chart {
  const last = chart.samples[chart.samples.length - 1]
  if (last !== undefined && sample.t - last.t < chart.periodMs) return chart
  let samples = [...chart.samples, sample]
  let periodMs = chart.periodMs
  if (samples.length > cap) {
    const folded: Sample[] = []
    for (let i = 0; i < samples.length; i += 2) {
      const a = samples[i]
      const b = samples[i + 1]
      if (a === undefined) continue
      folded.push(b === undefined ? a : merge(a, b))
    }
    samples = folded
    periodMs *= 2
  }
  return { ...chart, periodMs, samples }
}

function merge(a: Sample, b: Sample): Sample {
  const ctx = a.ctx === null ? b.ctx : b.ctx === null ? a.ctx : Math.max(a.ctx, b.ctx)
  return { t: a.t, ctx, on: [...new Set([...a.on, ...b.on])] }
}

/** Folds samples into at most `width` columns, so the whole session fits; each column keeps its highest fill. */
export function columns(samples: readonly Sample[], width: number): Sample[] {
  if (width <= 0) return []
  if (samples.length <= width) return [...samples]
  const out: Sample[] = []
  for (let i = 0; i < width; i += 1) {
    const from = Math.floor((i * samples.length) / width)
    const to = Math.floor(((i + 1) * samples.length) / width)
    const part = samples.slice(from, Math.max(to, from + 1))
    out.push(part.reduce(merge))
  }
  return out
}

const EIGHTHS = ' ▁▂▃▄▅▆▇█'

/** The context chart's rows, top first, on a fixed 0-100% scale. */
export function bars(values: readonly (number | null)[], rows = CONTEXT_BAR_ROWS): string[] {
  const out: string[] = []
  for (let r = 0; r < rows; r += 1) {
    const floor = (rows - 1 - r) * 8
    out.push(values.map(v => {
      if (v === null) return ' '
      const level = Math.round((Math.max(0, Math.min(100, v)) / 100) * rows * 8)
      return EIGHTHS[Math.max(0, Math.min(8, level - floor))] ?? ' '
    }).join(''))
  }
  return out
}
