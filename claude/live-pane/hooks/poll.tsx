// `bw live --json` for the pane: the argv, one call with a 10 s limit that the pane can cancel, and a strict
// reader for document v1. `$` comes in only as the argument of `startCall`.

import type { EngineInterface, HookStream, ProcessSpawnChunk, ProcessSpawnResult } from 'claude-code'

import type { LiveDoc, LocalSeat, OpenRun, RecentRun, UsageRow } from '../types'

export const POLL_MS = 5000
export const TIMEOUT_MS = 10_000
const MAX_OUTPUT_CHARS = 8 * 1024 * 1024
const DEFAULT_RUN_MAX_S = 2700

export type Fetched = { doc: LiveDoc } | { error: string }

export type Call = { cancel: (why: string) => void; done: Promise<Fetched> }

/** `--since` is in whole epoch seconds (F8); the session start is in milliseconds. */
export function argvFor(bwPath: string, cwd: string, startedAtMs: number): string[] {
  return [bwPath, 'live', '--json', '--dir', cwd, '--since', String(Math.floor(startedAtMs / 1000))]
}

/** Starts one `bw live` call. It ends by itself, at the 10 s limit, or when `cancel` is called. */
export function startCall($: EngineInterface, argv: readonly string[]): Call {
  let stop: (why: string) => void = () => undefined
  const stopped = new Promise<Fetched>(resolve => {
    stop = why => resolve({ error: why })
  })
  const stream = $.process.spawn({ argv })
  const timer = $.clock.after(TIMEOUT_MS, () => stop('timed out'))
  const done = (async (): Promise<Fetched> => {
    try {
      return await Promise.race([readAll(stream), stopped])
    } finally {
      timer.cancel()
      // ending the stream ends the child; after a normal end this does nothing
      void stream.return({ code: null, signal: null }).catch(() => undefined)
    }
  })()
  return { cancel: why => stop(why), done }
}

/** The reason a cancelled call gives; the pane does not show it as a failure. */
export const CANCELLED = 'cancelled'

// One call at a time per loaded module. A reload ends the old module's call with the module.
let current: Call | null = null
let lastEndedAt = Number.NEGATIVE_INFINITY

/** True when no call is in flight and the last one ended at least 5 s ago. */
export function isPollDue(nowMs: number): boolean {
  return current === null && nowMs - lastEndedAt >= POLL_MS
}

/** Runs one call when one is due; null when one is in flight or the last ended less than 5 s ago. */
export async function pollOnce($: EngineInterface, argv: readonly string[]): Promise<Fetched | null> {
  const startedAt = await $.clock.now()
  if (!isPollDue(startedAt)) return null
  const call = startCall($, argv)
  current = call
  try {
    return await call.done
  } finally {
    current = null
    lastEndedAt = await $.clock.now().catch(() => startedAt)
  }
}

/** Ends the call in flight, if any (the pane closed, or the session ended). */
export function cancelPoll(why: string): void {
  current?.cancel(why)
}

async function readAll(stream: HookStream<ProcessSpawnChunk, ProcessSpawnResult>): Promise<Fetched> {
  let out = ''
  let pulls = 0
  try {
    for (;;) {
      pulls += 1
      const step = await stream.next()
      if (step.done === true) return parseDoc(out, step.value)
      if (step.value.stream === 'stdout') {
        out += step.value.text
        if (out.length > MAX_OUTPUT_CHARS) return { error: 'output too large' }
      }
    }
  } catch {
    return { error: pulls === 1 ? 'cannot start' : 'failed' }
  }
}

/** The document, if the call exited 0 and printed a v1 document; else why not. */
export function parseDoc(stdout: string, end: ProcessSpawnResult): Fetched {
  if (end.code !== 0) return { error: end.code === null ? `ended by ${end.signal ?? 'a signal'}` : `exit ${end.code}` }
  let raw: unknown
  try {
    raw = JSON.parse(stdout)
  } catch {
    return { error: 'output is not JSON' }
  }
  if (!isObj(raw)) return { error: 'output is not a document' }
  if (raw.v !== 1) return { error: `gave v ${typeof raw.v === 'number' ? raw.v : '?'}, the pane reads v 1` }
  return { doc: normalize(raw) }
}

type Obj = Record<string, unknown>

function isObj(v: unknown): v is Obj {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

function str(v: unknown): string | null {
  return typeof v === 'string' ? v : null
}

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null
}

function bool(v: unknown): boolean | null {
  return typeof v === 'boolean' ? v : null
}

function list(v: unknown): Obj[] {
  return Array.isArray(v) ? v.filter(isObj) : []
}

function source(v: unknown): 'bw' | 'local' {
  return v === 'local' ? 'local' : 'bw'
}

/** Keeps only fields of the right type, so drawing never meets a surprise. */
function normalize(raw: Obj): LiveDoc {
  const local = isObj(raw.local) ? raw.local : {}
  const families: Record<string, string> = {}
  if (isObj(local.families)) {
    for (const [word, color] of Object.entries(local.families)) {
      if (typeof color === 'string') families[word] = color
    }
  }
  const seats: LocalSeat[] = list(local.seats).flatMap(s => {
    const name = str(s.name)
    const match = str(s.match)
    return name === null || match === null ? [] : [{
      name, match, metric: str(s.metric), model: str(s.model), icon: str(s.icon), color: str(s.color),
    }]
  })
  const open: OpenRun[] = list(raw.open).map(r => ({
    run_id: str(r.run_id), seat: str(r.seat), model: str(r.model), label: str(r.label), dir: str(r.dir) ?? '',
    start: num(r.start), source: source(r.source), panel: str(r.panel), ask: str(r.ask),
    state: r.state === 'live' || r.state === 'stale' ? r.state : 'unknown', icon: str(r.icon), color: str(r.color),
    ctx: num(r.ctx), window: num(r.window), cost_usd: num(r.cost_usd),
  }))
  const recent: RecentRun[] = list(raw.recent).flatMap(r => {
    const end = num(r.end)
    return end === null ? [] : [{
      run_id: str(r.run_id) ?? '', seat: str(r.seat), model: str(r.model), label: str(r.label), start: num(r.start),
      end, ok: bool(r.ok), reason: str(r.reason), source: source(r.source), stand_in_for: str(r.stand_in_for),
      tokens_in: num(r.tokens_in), tokens_out: num(r.tokens_out), cost_usd: num(r.cost_usd), ctx_peak: num(r.ctx_peak),
    }]
  })
  const usage: UsageRow[] = list(raw.usage).flatMap(r => {
    const t = num(r.t)
    return t === null ? [] : [{ seat: str(r.seat), t, dir: str(r.dir), ok: bool(r.ok), source: source(r.source) }]
  })
  const runMax = num(local.run_max_s)
  return {
    v: 1,
    now: num(raw.now) ?? 0,
    complete: raw.complete === true,
    open,
    recent,
    usage,
    local: {
      seats,
      logs: list(local.logs).map(l => ({ path: str(l.path) ?? '', ok: l.ok === true, error: str(l.error) })),
      families,
      run_max_s: runMax !== null && runMax > 0 ? runMax : DEFAULT_RUN_MAX_S,
    },
    panels: list(raw.panels).map(p => ({
      panel: str(p.panel) ?? '',
      state: str(p.state) ?? '',
      seats: isObj(p.seats) ? Object.fromEntries(Object.entries(p.seats).map(([k, v]) => [k, str(v) ?? ''])) : {},
      asks: list(p.asks).map(a => ({ id: str(a.id), seat: str(a.seat), state: str(a.state) })),
    })),
    ...(typeof raw.error === 'string' ? { error: raw.error } : {}),
  }
}
