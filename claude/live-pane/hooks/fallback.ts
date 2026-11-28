// The Bash fallback (spec §5), pure: a stored tool_use row of a Bash call whose command holds a local seat's
// `match` opens a lane; its tool_result row closes a foreground lane; `bw live` documents close, pair, drop,
// or expire lanes. No `$` here. Times are milliseconds.

import type { Lane, LaneState, LiveDoc, LocalSeat, OpenRun, RecentRun, UsageRow } from '../types'

/** The pane reads at most this much of a command. */
export const MATCH_CHARS = 4096
/** A `run` start this close to a lane's start means `bw run` also wraps the call. */
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

export function openLane(seat: LocalSeat, id: string, startMs: number, words: string | null, background: boolean): Lane {
  return {
    id, seat: seat.name, metric: seat.metric ?? seat.name, model: seat.model, icon: seat.icon, color: seat.color,
    words, startMs, background, endMs: null, end: null,
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

function nearStart(lane: Lane, seat: string | null, startS: number | null): boolean {
  return seat === lane.seat && startS !== null && Math.abs(startS * 1000 - lane.startMs) <= WRAP_MS
}

/** Drops a lane when an open run of the same seat started within 2 s of it (the call is also wrapped). */
export function dropWrapped(lanes: readonly Lane[], open: readonly OpenRun[]): Lane[] {
  return lanes.filter(l => !open.some(r => nearStart(l, r.seat, r.start)))
}

/** The same for runs that already ended: a wrapped run that finished between two polls. */
export function reconcile(lanes: readonly Lane[], recent: readonly RecentRun[]): Lane[] {
  return lanes.filter(l => !recent.some(r => nearStart(l, r.seat, r.start)))
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
  const lanes = reconcile(dropWrapped(state.lanes, doc.open), doc.recent)
  const taken = new Set(state.taken)
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
