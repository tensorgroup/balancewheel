// Pure text formatting for the pane: durations, tokens, estimated cost, model labels, history lines.
// No `$` here.

import type { Finished, RecentRun } from '../types'

/** 12s, 38m, 1h04 */
export function duration(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m`
  const rest = m % 60
  return `${Math.floor(m / 60)}h${rest < 10 ? '0' : ''}${rest}`
}

/** 950, 41k, 1.2M */
export function tokens(n: number): string {
  if (n < 1000) return `${Math.round(n)}`
  if (n < 1_000_000) return `${Math.round(n / 1000)}k`
  return `${(n / 1_000_000).toFixed(1)}M`
}

/** An estimate, always marked `≈`; null is a run on a plan (no cost recorded). */
export function cost(usd: number | null): string {
  if (usd === null || !Number.isFinite(usd)) return 'plan'
  return `≈$${usd < 10 ? usd.toFixed(2) : usd.toFixed(0)}`
}

export function percent(p: number): string {
  return `${Math.round(Math.max(0, Math.min(100, p)))}%`
}

/**
 * A short model label from a model string, with no table of ids: a leading `claude-` goes, the first word is
 * capitalized, and the version numbers after it are joined with dots ("<vendor>-<word>-5-5" reads "Word 5.5").
 * Any other string is kept as it is.
 */
export function modelLabel(model: string | null, effort?: string | null): string {
  if (model === null || model === '') return ''
  let label = model
  const m = /^(?:claude-)?([a-z]+)-(\d+(?:-\d+)*)(?:-\d{8})?(\[[^\]]*\])?$/.exec(model)
  if (m !== null && m[1] !== undefined && m[2] !== undefined) {
    label = `${m[1].charAt(0).toUpperCase()}${m[1].slice(1)} ${m[2].split('-').join('.')}${m[3] ?? ''}`
  }
  const e = effort === undefined || effort === null || effort === '' ? '' : `·${effort.slice(0, 3)}`
  return `${label}${e}`
}

const LIMIT_NAMES: Record<string, string> = { five_hour: '5h', seven_day: '7d', spend_limit: 'spend' }

export function rateLimits(list: readonly { kind: string; percentUsed: number }[]): string {
  return list.map(r => `${LIMIT_NAMES[r.kind] ?? r.kind} ${percent(r.percentUsed)}`).join(' ')
}

/** Cuts to `n` columns with an ellipsis. Counts code points, which is right for the text the pane cuts. */
export function cut(text: string, n: number): string {
  const chars = [...text.replace(/\s+/g, ' ').trim()]
  if (n <= 0) return ''
  return chars.length <= n ? chars.join('') : `${chars.slice(0, Math.max(0, n - 1)).join('')}…`
}

export function pad(text: string, n: number): string {
  const c = cut(text, n)
  return c + ' '.repeat(Math.max(0, n - [...c].length))
}

/** One finished run, as the history draws it: two lines. */
export type HistoryItem = {
  key: string
  name: string
  color: string | null
  endMs: number
  first: string
  second: string
}

function outcomeOf(ok: boolean | null, reason: string | null): string {
  if (ok === true) return 'ok'
  if (ok === false) return reason === null ? 'failed' : `failed (${reason})`
  return reason === null ? 'end unknown' : reason
}

export function fromRecent(r: RecentRun, color: string | null): HistoryItem {
  const name = r.seat ?? 'seat'
  const ran = r.start === null ? '' : duration((r.end - r.start) * 1000)
  const peak = r.ctx_peak === null ? '' : `peak ${tokens(r.ctx_peak)}`
  const kind = r.source === 'local' ? 'local seat' : 'bw seat'
  const toks = r.tokens_in === null && r.tokens_out === null ? ''
    : `${tokens(r.tokens_in ?? 0)} in ${tokens(r.tokens_out ?? 0)} out`
  const stand = r.stand_in_for === null ? '' : `for ${r.stand_in_for}`
  return {
    key: `r:${r.run_id}`,
    name,
    color,
    endMs: r.end * 1000,
    first: join([name, modelLabel(r.model), ran, peak, cost(r.cost_usd)]),
    second: join([`${kind} · ${outcomeOf(r.ok, r.reason)}`, stand, toks, r.label ?? '']),
  }
}

export function fromFinished(f: Finished): HistoryItem {
  const peak = f.ctxPeakPct === null ? '' : `peak ${percent(f.ctxPeakPct)}`
  const kind = f.kind === 'subagent' ? 'subagent' : 'local seat'
  return {
    key: f.key,
    name: f.name,
    color: f.color,
    endMs: f.endMs,
    first: join([f.name, modelLabel(f.model), duration(f.endMs - f.startMs), peak, cost(f.costUsd)]),
    second: join([`${kind} · ${f.outcome}`, f.tokens === null ? '' : `${tokens(f.tokens)} tokens`, f.words ?? '']),
  }
}

function join(parts: readonly string[]): string {
  return parts.filter(p => p !== '').join('  ')
}

/** How old the seat data is, and why it is not newer. */
export function ageLine(nowMs: number, okAt: number | null, error: string | null): string {
  const why = error === null ? '' : ` (bw: ${error})`
  if (okAt === null) return `seat data: none yet${why}`
  return `seat data: ${duration(nowMs - okAt)} old${why}`
}
