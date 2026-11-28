// The live pane's contract: the `bw live --json` document v1 (lead/live.py) and the pane's own state.
// Hand-written and committed (PD-11). The engine's generated types are in .claude-plugin/types/ (git-ignored).

// ---- bw live --json, v: 1 ----------------------------------------------------------------------------

export type LiveDoc = {
  v: number
  now: number
  complete: boolean
  open: OpenRun[]
  recent: RecentRun[]
  usage: UsageRow[]
  local: LocalSection
  panels: PanelRow[]
  /** Only on a config error (PD-12): the lists are then empty and `complete` is false. */
  error?: string
}

/** A run that has a start and no end, a panel seat or ask that runs, or a run found by its newest beat. */
export type OpenRun = {
  run_id: string | null
  seat: string | null
  model: string | null
  label: string | null
  dir: string
  /** Epoch seconds; null when only a beat is in reach. */
  start: number | null
  source: 'bw' | 'local'
  panel: string | null
  ask: string | null
  state: 'live' | 'stale' | 'unknown'
  icon: string | null
  color: string | null
  ctx: number | null
  window: number | null
  cost_usd: number | null
}

/** A run that ended in the window, newest first. */
export type RecentRun = {
  run_id: string
  seat: string | null
  model: string | null
  label: string | null
  start: number | null
  end: number
  ok: boolean | null
  reason: string | null
  source: 'bw' | 'local'
  stand_in_for: string | null
  tokens_in: number | null
  tokens_out: number | null
  cost_usd: number | null
  ctx_peak: number | null
}

/** A `usage` row with no `run_id`: the close signal of a fallback lane. `seat` is the seat's metric. */
export type UsageRow = {
  seat: string | null
  t: number
  dir: string | null
  ok: boolean | null
  source: 'bw' | 'local'
}

export type LocalSeat = {
  name: string
  match: string
  metric: string | null
  model: string | null
  icon: string | null
  color: string | null
}

export type LocalLog = { path: string; ok: boolean; error: string | null }

export type LocalSection = {
  seats: LocalSeat[]
  logs: LocalLog[]
  families: Record<string, string>
  run_max_s: number
}

export type PanelAsk = { id: string | null; seat: string | null; state: string | null }

export type PanelRow = {
  panel: string
  state: string
  seats: Record<string, string>
  asks: PanelAsk[]
}

// ---- the pane's own state ---------------------------------------------------------------------------

/** A lane the Bash fallback opened for a local seat. Times in milliseconds. */
export type Lane = {
  id: string
  /** The local seat's name: what a `run` row names it. */
  seat: string
  /** The seat's metric: what its `usage` rows name it. */
  metric: string
  model: string | null
  icon: string | null
  color: string | null
  /** A few words of what the call did (the Bash description), or null. */
  words: string | null
  startMs: number
  background: boolean
  endMs: number | null
  end: 'ok' | 'failed' | 'unknown' | null
}

export type LaneState = {
  lanes: Lane[]
  /** Keys of `usage` rows a lane has paired with (fallback.ts `withKeys`). */
  taken: string[]
}

/** The last `bw live` result. Times in milliseconds. */
export type SeatData = {
  doc: LiveDoc | null
  /** When `doc` was read. */
  okAt: number | null
  /** Why the last call failed, or null after a good one. */
  error: string | null
}

export type AgentRow = {
  id: string
  name: string
  model: string | null
  effort: string | null
  startMs: number
  tokens: number
  window: number | null
  costUsd: number | null
  status: string
  words: string
}

/** What the one-second tick read from Claude Code. Times in milliseconds. */
export type Snap = {
  at: number
  startedAt: number
  model: string
  ctxPct: number | null
  costUsd: number | null
  rateLimits: { kind: string; percentUsed: number }[]
  agents: AgentRow[]
}

/** A subagent or a local fallback lane that ended (bw runs come from `recent`). */
export type Finished = {
  key: string
  kind: 'subagent' | 'local'
  name: string
  model: string | null
  startMs: number
  endMs: number
  /** Peak context as a percent, or null when no window is known. */
  ctxPeakPct: number | null
  tokens: number | null
  costUsd: number | null
  outcome: string
  words: string | null
  color: string | null
}

/** One chart column: main context percent, and the keys of the lanes that ran. */
export type Sample = { t: number; ctx: number | null; on: string[] }

export type Chart = {
  periodMs: number
  samples: Sample[]
  names: Record<string, { name: string; color: string }>
}

declare module 'claude-code' {
  interface PluginState {
    'live-pane': {
      snap: Shaped<Snap | null>
      seats: Shaped<SeatData>
      lanes: Shaped<LaneState>
      history: Shaped<Finished[]>
      chart: Shaped<Chart>
      isClosedByPerson: boolean
    }
  }
}
