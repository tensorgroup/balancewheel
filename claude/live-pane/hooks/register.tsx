// The live pane (spec §5): one lane per running agent or seat, session figures, a context chart, the
// agents-at-work timeline, and the runs that ended in this worktree. It only reads: each second it reads
// Claude Code's own agents and session figures; every 5 s while the pane is shown it runs `bw live --json`;
// a stored Bash tool_use row that names a local seat opens a lane at once (the fallback). It hooks no tool
// call: a hook that may match a tool turns off that tool's move to the background.

import { atom, read, update } from 'claude-code'
import type { AgentInfo, EngineInterface, Register, SessionUsage, Timer } from 'claude-code'

import type { AgentRow, Finished, Sample, Snap } from '../types'
import { applyDoc, bashUses, closeLane, expire, matches, openLane, prune, toolResults, wrapSeat } from './fallback'
import type { BashUse } from './fallback'
import { familyColor, pushSample, seatColor } from './layout'
import { argvFor, CANCELLED, cancelPoll, isPollDue, pollOnce } from './poll'
import { buildView, drawPane, STALE_MS } from './view'

const PANE = 'live-pane'
const TITLE = 'Live'
const TICK_MS = 1000
const SAMPLE_MS = 5000
const DEFAULT_RUN_MAX_S = 2700
const KEEP_FINISHED = 100
const KEEP_NAMES = 200
const FINISHED: Readonly<Record<string, string>> = { completed: 'done', failed: 'failed', killed: 'stopped' }
const RUNNING = new Set(['pending', 'running', 'waiting', 'idle'])
const AGENT_COLOR = '#64748b'

// Each tag names the idea of the value it keeps: change the tag when the value's fields change.
const snapAtom = atom({ plugin: 'live-pane', key: 'snap' } as const, null, { shape: 'snap-1' })
const seatsAtom = atom({ plugin: 'live-pane', key: 'seats' } as const, { doc: null, okAt: null, error: null }, { shape: 'seats-1' })
const lanesAtom = atom({ plugin: 'live-pane', key: 'lanes' } as const, { lanes: [], taken: [] }, { shape: 'lanes-2' })
const historyAtom = atom({ plugin: 'live-pane', key: 'history' } as const, [], { shape: 'history-1' })
const chartAtom = atom({ plugin: 'live-pane', key: 'chart' } as const, { periodMs: SAMPLE_MS, samples: [], names: {} }, { shape: 'chart-1' })
const closedAtom = atom({ plugin: 'live-pane', key: 'isClosedByPerson' } as const, false)

let ticker: Timer | null = null
// Rows are observed one after another, so a tool_result never runs ahead of its tool_use.
let rows: Promise<void> = Promise.resolve()

function agentRow(a: AgentInfo): AgentRow {
  return {
    id: a.id, name: a.name ?? a.type, model: a.model ?? null, effort: a.effort === undefined ? null : String(a.effort),
    startMs: a.startTime, tokens: a.tokenCount, window: a.contextWindowSize ?? null, costUsd: a.cost?.usd ?? null,
    status: a.status, words: a.description,
  }
}

function finishedOf(a: AgentInfo, nowMs: number, families: Readonly<Record<string, string>>): Finished {
  const window = a.contextWindowSize ?? null
  const peak = Math.max(a.tokenCount, ...a.tokenSamples)
  return {
    key: `a:${a.id}`, kind: 'subagent', name: a.name ?? a.type, model: a.model ?? null, startMs: a.startTime,
    endMs: nowMs, ctxPeakPct: window === null || window <= 0 ? null : (100 * peak) / window, tokens: a.tokenCount,
    costUsd: a.cost?.usd ?? null, outcome: FINISHED[a.status] ?? a.status, words: a.description,
    color: familyColor(a.model ?? null, families),
  }
}

function ctxPct(u: SessionUsage | null): number | null {
  if (u === null) return null
  if (typeof u.context.percent === 'number') return u.context.percent
  return u.context.tokens === undefined || u.context.window <= 0 ? null : (100 * u.context.tokens) / u.context.window
}

/** Open and drawn: a pane that waits undrawn (a narrow terminal, a `-p` run) runs no `bw live`. */
async function isPaneShown($: EngineInterface): Promise<boolean> {
  return (await $.ui.panes()).some(p => p.id === PANE && p.isPlaced)
}

/** One second: Claude Code's figures, finished subagents, lane expiry, a chart sample, and a poll when due. */
async function tick($: EngineInterface, bwPath: string): Promise<void> {
  const now = await $.clock.now()
  const [list, usage, model] = await Promise.all([
    $.agent.list().catch((): AgentInfo[] => []),
    $.session.usage().catch((): SessionUsage | null => null),
    $.session.model().catch(() => ''),
  ])
  const prev = await read($, snapAtom)
  const seats = await read($, seatsAtom)
  const families = seats.doc?.local.families ?? {}
  const startedAt = usage?.startedAt ?? prev?.startedAt ?? now
  const running = list.filter(a => RUNNING.has(a.status))
  const snap: Snap = {
    at: now, startedAt, model, ctxPct: ctxPct(usage), costUsd: usage?.cost?.usd ?? null,
    rateLimits: (usage?.rateLimits ?? []).map(r => ({ kind: r.kind, percentUsed: r.percentUsed })),
    agents: running.map(agentRow),
  }
  await update($, snapAtom, () => snap)

  // only this session's agents: after /clear the engine may still list the old session's
  const done = list.filter(a => FINISHED[a.status] !== undefined && a.startTime >= startedAt - TICK_MS)
  if (done.length > 0) {
    const known = new Set((await read($, historyAtom)).map(f => f.key))
    if (done.some(a => !known.has(`a:${a.id}`))) {
      await update($, historyAtom, h => {
        const have = new Set(h.map(f => f.key))
        const added = done.filter(a => !have.has(`a:${a.id}`)).map(a => finishedOf(a, now, families))
        return [...added, ...h].slice(0, KEEP_FINISHED)
      })
    }
  }

  const runMaxS = seats.doc?.local.run_max_s ?? DEFAULT_RUN_MAX_S
  const lanes = await read($, lanesAtom)
  if (lanes.lanes.some(l => l.endMs === null && now - l.startMs > runMaxS * 1000)) {
    await update($, lanesAtom, s => ({ ...s, lanes: prune(expire(s.lanes, now, runMaxS)) }))
  }

  const chart = await read($, chartAtom)
  const last = chart.samples[chart.samples.length - 1]
  if (last === undefined || now - last.t >= chart.periodMs) {
    const isFresh = seats.okAt !== null && now - seats.okAt <= STALE_MS
    const named: Record<string, { name: string; color: string }> = {}
    for (const a of running) named[`a:${a.id}`] = { name: a.name ?? a.type, color: familyColor(a.model ?? null, families) ?? AGENT_COLOR }
    for (const r of isFresh ? seats.doc?.open ?? [] : []) {
      if (r.state !== 'live') continue
      const name = r.seat ?? 'seat'
      named[`s:${r.run_id ?? `${r.panel ?? ''}:${name}`}`] = { name, color: seatColor(name, r.color) }
    }
    for (const l of lanes.lanes) if (l.endMs === null) named[`l:${l.id}`] = { name: l.seat, color: seatColor(l.seat, l.color) }
    const sample: Sample = { t: now, ctx: snap.ctxPct, on: Object.keys(named) }
    await update($, chartAtom, c => {
      const names = Object.fromEntries(Object.entries({ ...c.names, ...named }).slice(-KEEP_NAMES))
      return pushSample({ ...c, names }, sample)
    })
  }

  if (isPollDue(now) && (await isPaneShown($))) void poll($, bwPath, startedAt)
}

/** One `bw live` call; its document replaces the seat data and settles the fallback lanes. */
async function poll($: EngineInterface, bwPath: string, startedAt: number): Promise<void> {
  try {
    const cwd = await $.session.cwd()
    const got = await pollOnce($, argvFor(bwPath, cwd, startedAt))
    if (got === null) return
    if ('doc' in got && got.doc.error === undefined) {
      const doc = got.doc
      const at = await $.clock.now()
      await update($, seatsAtom, () => ({ doc, okAt: at, error: null }))
      await update($, lanesAtom, s => applyDoc(s, doc))
    } else {
      // a config-error document (PD-12) is a failed poll too: the last good data stays
      const why = 'doc' in got ? got.doc.error ?? 'error' : got.error
      if (why !== CANCELLED) await update($, seatsAtom, s => ({ ...s, error: why }))
    }
  } catch {
    // the pane only reads: a failed poll keeps the last data
  }
}

/** A row's Bash calls open lanes for local seats; its results close foreground lanes. */
async function observeRow($: EngineInterface, uses: readonly BashUse[], results: readonly { id: string; isError: boolean }[]): Promise<void> {
  if (uses.length > 0) {
    const seats = (await read($, seatsAtom)).doc?.local.seats ?? []
    const opened = uses.flatMap(u => {
      const seat = matches(u.command, seats)
      return seat === undefined ? [] : [{ u, seat }]
    })
    if (opened.length > 0) {
      const now = await $.clock.now()
      const ids = new Set(opened.map(o => o.u.id))
      const added = opened.map(o => openLane(o.seat, o.u.id, now, o.u.description, o.u.isBackground, wrapSeat(o.u.command)))
      await update($, lanesAtom, s => ({ ...s, lanes: prune([...s.lanes.filter(l => !ids.has(l.id)), ...added]) }))
    }
  }
  if (results.length > 0) {
    const open = (await read($, lanesAtom)).lanes.filter(l => l.endMs === null && !l.background)
    const closing = results.filter(r => open.some(l => l.id === r.id))
    if (closing.length > 0) {
      const now = await $.clock.now()
      await update($, lanesAtom, s => ({
        ...s,
        lanes: closing.reduce((lanes, r) => closeLane(lanes, r.id, now, r.isError ? 'failed' : 'ok'), s.lanes),
      }))
    }
  }
}

/** After /clear or a resume the process goes on with no session.start: the session's state starts over. */
async function startOver($: EngineInterface): Promise<void> {
  cancelPoll(CANCELLED)
  await update($, snapAtom, () => null)
  await update($, historyAtom, () => [])
  await update($, lanesAtom, () => ({ lanes: [], taken: [] }))
  await update($, chartAtom, () => ({ periodMs: SAMPLE_MS, samples: [], names: {} }))
  // keep the local seats until the next poll; the old window's runs go
  await update($, seatsAtom, s => ({ ...s, doc: s.doc === null ? null : { ...s.doc, open: [], recent: [], usage: [] } }))
}

export const register: Register = (on, options) => {
  const bwPath = typeof options.bwPath === 'string' && options.bwPath.trim() !== '' ? options.bwPath : 'bw'

  on('session.start', async ($, e, next) => {
    try {
      ticker?.cancel()
      ticker = $.clock.every(TICK_MS, () => {
        void tick($, bwPath).catch(() => undefined)
      })
      await $.command.register({
        name: 'live-pane',
        description: 'Open the live pane: the agents and seats at work, and the runs that ended',
      })
      if (!(await read($, closedAtom))) void $.ui.open({ id: PANE, title: TITLE }).catch(() => undefined)
    } catch {
      // without its timer the pane still draws what it has
    }
    return next(e)
  })

  on('session.end', async ($, e, next) => {
    if (e.reason === 'clear' || e.reason === 'resume') {
      try {
        await startOver($) // the timer goes on
      } catch {
        // the next tick reads the new session's figures anyway
      }
    } else {
      ticker?.cancel()
      ticker = null
      cancelPoll(CANCELLED)
    }
    return next(e)
  })

  on('command.run', { command: 'live-pane' }, async $ => {
    await update($, closedAtom, () => false)
    await $.ui.open({ id: PANE, title: TITLE })
    return { text: 'The live pane is open.' }
  })

  on('ui.close', { id: PANE }, async ($, e, next) => {
    cancelPoll(CANCELLED)
    if (e.origin.kind === 'person') {
      try {
        await update($, closedAtom, () => true)
      } catch {
        // the next session start opens it again
      }
    }
    return next(e)
  })

  // The fallback observes the rows the conversation stores and keeps each row as it is.
  on('session.append', async ($, e, next) => {
    const kept = await next(e)
    try {
      const uses = bashUses(e.message.content)
      const results = toolResults(e.message.content)
      if (uses.length + results.length > 0) {
        rows = rows.then(() => observeRow($, uses, results)).catch(() => undefined)
      }
    } catch {
      // a row the pane cannot read opens no lane
    }
    return kept
  }).catch((_, e, next) => next(e))

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const els = $.ui.resolve(e)
    const [snap, seats, lanes, finished, chart] = await Promise.all([
      read($, snapAtom), read($, seatsAtom), read($, lanesAtom), read($, historyAtom), read($, chartAtom),
    ])
    return drawPane(els, buildView(snap, seats, lanes, finished, chart), e.props.bodyColumns, e.props.scroll.bodyRows)
  }).catch(($, e) => {
    const { Text } = $.ui.resolve(e)
    return <Text dimColor>The live pane could not draw this time.</Text>
  })
}
