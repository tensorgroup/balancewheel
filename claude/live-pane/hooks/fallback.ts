// The Bash fallback (spec §5), pure: a stored tool_use row of a Bash call whose command holds a local seat's
// `match` opens a lane; its tool_result row closes a foreground lane; `bw live` documents close, pair, drop,
// or expire lanes. No `$` here. Times are milliseconds.

import type { Lane, LaneState, LiveDoc, LocalSeat, OpenRun, RecentRun, UsageRow } from '../types'

/** The pane reads at most this much of a command. */
export const MATCH_CHARS = 4096
/** A `run` start may be this much older than its lane (the row is stored around the time the call starts). */
export const WRAP_MS = 2000
/** The log keeps whole seconds (`usage` rows). */
export const LOG_PRECISION_MS = 1000
const KEEP_CLOSED = 50
const KEEP_TAKEN = 500

/** The first seat whose non-empty `match` is a plain substring of the command's first 4 KB. */
export function matches(command: string, seats: readonly LocalSeat[]): LocalSeat | undefined {
  const head = command.slice(0, MATCH_CHARS)
  return seats.find(s => typeof s.match === 'string' && s.match !== '' && head.includes(s.match))
}

// `bw run`, then flags up to its `--`, then `--seat NAME` (bw run takes only the spaced form).
const BW_RUN_SEAT = /(?:^|[\s;&|()`'"/])bw\s+run(?=\s)(?:(?!\s--\s)[^;&|\n])*?\s--seat\s+['"]?([a-z0-9-]+)/

/** The `--seat` of a `bw run` in the command's first 4 KB, or null when the command shows no `bw run`. */
export function wrapSeat(command: string): string | null {
  return BW_RUN_SEAT.exec(command.slice(0, MATCH_CHARS))?.[1] ?? null
}

export function openLane(seat: LocalSeat, id: string, startMs: number, words: string | null, background: boolean,
  wrap: string | null = null): Lane {
  return {
    id, seat: seat.name, metric: seat.metric ?? seat.name, model: seat.model, icon: seat.icon, color: seat.color,
    words, wrapSeat: wrap, startMs, background, endMs: null, end: null,
  }
}

export function closeLane(lanes: readonly Lane[], id: string, endMs: number, end: 'ok' | 'failed' | 'unknown'): Lane[] {
  return lanes.map(l => (l.id === id && l.endMs === null ? { ...l, endMs, end } : l))
}

/** A Bash call as its assistant row holds it. */
export type BashUse = { id: string; command: string; description: string | null; isBackground: boolean }

type Block = { readonly type: string; readonly [field: string]: unknown }

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

/** The Bash `tool_use` blocks of a row. */
export function bashUses(content: readonly Block[]): BashUse[] {
  return content.flatMap(b => {
    if (b.type !== 'tool_use' || b.name !== 'Bash' || typeof b.id !== 'string' || !isRecord(b.input)) return []
    const { command, description, run_in_background: bg } = b.input
    if (typeof command !== 'string') return []
    return [{ id: b.id, command, description: typeof description === 'string' ? description : null, isBackground: bg === true }]
  })
}

/** The `tool_result` blocks of a row: which call each answers, and whether it failed. */
export function toolResults(content: readonly Block[]): { id: string; isError: boolean }[] {
  return content.flatMap(b => (b.type === 'tool_result' && typeof b.tool_use_id === 'string'
    ? [{ id: b.tool_use_id, isError: b.is_error === true }] : []))
}

/** A row's key: `seat|dir|t|n`, where n counts the rows of this poll with the same seat, dir, and t. */
export function withKeys(rows: readonly UsageRow[]): { row: UsageRow; key: string }[] {
  const seen = new Map<string, number>()
  return rows.map(row => {
    const triple = `${row.seat}|${row.dir}|${row.t}`
    const n = seen.get(triple) ?? 0
    seen.set(triple, n + 1)
    return { row, key: `${triple}|${n}` }
  })
}

/**
 * Pairs lanes with `usage` rows (rows have no run_id). The older lane takes a row first; a lane takes the
 * oldest free row of its metric at or after its start, less 1 s. `bw live` lists only this worktree's rows,
 * so no dir check is made here (F7). Returns lane id -> row key, and adds the keys to `taken`.
 */
export function pair(lanes: readonly Lane[], rows: readonly UsageRow[], taken: Set<string>): Map<string, string> {
  const out = new Map<string, string>()
  const keyed = withKeys(rows)
  for (const lane of [...lanes].sort((a, b) => a.startMs - b.startMs)) {
    const hit = keyed
      .filter(k => !taken.has(k.key) && k.row.seat === lane.metric && k.row.t * 1000 >= lane.startMs - LOG_PRECISION_MS)
      .sort((a, b) => a.row.t - b.row.t)[0]
    if (hit !== undefined) {
      taken.add(hit.key)
      out.set(lane.id, hit.key)
    }
  }
  return out
}

type RunStart = Pick<OpenRun | RecentRun, 'run_id' | 'seat' | 'start' | 'source'>

/** The `taken` key of a run that has dropped a lane. */
export function runKey(runId: string): string {
  return `run:${runId}`
}

/** A run could be the one this lane's call started: its seat, and a start from the lane's start (less 2 s) on. */
function couldBe(lane: Lane, seat: string | null, startMs: number): boolean {
  return seat === (lane.wrapSeat ?? lane.seat) && startMs >= lane.startMs - WRAP_MS
    && (lane.endMs === null || startMs <= lane.endMs + LOG_PRECISION_MS)
}

/**
 * The call is also wrapped by `bw run` (the run's own lane shows it): each `bw run` start (open or ended) that
 * has not dropped a lane yet drops one lane that it could be (`couldBe`). There is no upper limit while the
 * lane is open, because the call may wait at a permission prompt for as long as the person takes. A lane whose
 * command shows that `bw run` goes first, then the oldest: the calls of one seat start in order. The run's key
 * goes into `taken`, so the run never drops a second lane at a later poll.
 */
export function dropWrapped(lanes: readonly Lane[], runs: readonly RunStart[], taken: Set<string>): Lane[] {
  let left = [...lanes]
  const fresh = runs
    .filter((r): r is RunStart & { run_id: string; start: number } =>
      r.source === 'local' && r.run_id !== null && r.start !== null)
    .sort((a, b) => a.start - b.start)
  for (const r of fresh) {
    if (taken.has(runKey(r.run_id))) continue
    const pick = left
      .filter(l => couldBe(l, r.seat, r.start * 1000))
      .sort((a, b) => Number(b.wrapSeat !== null) - Number(a.wrapSeat !== null) || a.startMs - b.startMs)[0]
    if (pick === undefined) continue
    left = left.filter(l => l !== pick)
    taken.add(runKey(r.run_id))
  }
  return left
}

/** An open lane older than `run_max_s` closes as `end unknown`. */
export function expire(lanes: readonly Lane[], nowMs: number, runMaxS: number): Lane[] {
  return lanes.map(l => (l.endMs === null && nowMs - l.startMs > runMaxS * 1000 ? { ...l, endMs: nowMs, end: 'unknown' } : l))
}

/** Keeps every open lane and the newest closed ones. */
export function prune(lanes: readonly Lane[]): Lane[] {
  const closed = lanes.filter(l => l.endMs !== null).sort((a, b) => (b.endMs ?? 0) - (a.endMs ?? 0)).slice(0, KEEP_CLOSED)
  return lanes.filter(l => l.endMs === null || closed.includes(l))
}

/** What one `bw live` document does to the lanes: drop wrapped ones, then close background lanes it pairs. */
export function applyDoc(state: LaneState, doc: LiveDoc): LaneState {
  const taken = new Set(state.taken)
  const lanes = dropWrapped(state.lanes, [...doc.open, ...doc.recent], taken)
  const waiting = lanes.filter(l => l.background && l.endMs === null)
  const paired = pair(waiting, doc.usage, taken)
  const byKey = new Map(withKeys(doc.usage).map(k => [k.key, k.row]))
  const next = lanes.map(l => {
    const key = paired.get(l.id)
    const row = key === undefined ? undefined : byKey.get(key)
    if (row === undefined) return l
    const end: 'ok' | 'failed' | 'unknown' = row.ok === true ? 'ok' : row.ok === false ? 'failed' : 'unknown'
    return { ...l, endMs: Math.max(l.startMs, row.t * 1000), end }
  })
  return { lanes: prune(next), taken: [...taken].slice(-KEEP_TAKEN) }
}
