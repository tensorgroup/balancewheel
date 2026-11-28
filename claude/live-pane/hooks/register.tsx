// The live pane (spec §5): one lane per running agent or seat, session figures, a context chart, the
// agents-at-work timeline, and the runs that ended in this worktree. It only reads: each second it reads
// Claude Code's own agents and session figures; every 5 s while the pane is open it runs `bw live --json`;
// a Bash call that names a local seat opens a lane at once (the fallback). Its failures leave calls untouched.

import { atom, read, update } from 'claude-code'
import type { AgentInfo, EngineInterface, Register, SessionUsage, Timer } from 'claude-code'

import type { AgentRow, Finished, Sample, Snap } from '../types'
import { applyDoc, closeLane, expire, matches, openLane, prune, setBackground } from './fallback'
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
const lanesAtom = atom({ plugin: 'live-pane', key: 'lanes' } as const, { lanes: [], taken: [] }, { shape: 'lanes-1' })
const historyAtom = atom({ plugin: 'live-pane', key: 'history' } as const, [], { shape: 'history-1' })
const chartAtom = atom({ plugin: 'live-pane', key: 'chart' } as const, { periodMs: SAMPLE_MS, samples: [], names: {} }, { shape: 'chart-1' })
const closedAtom = atom({ plugin: 'live-pane', key: 'isClosedByPerson' } as const, false)

let ticker: Timer | null = null

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

/** The Bash result names a background task: the call went on after it returned (or the person moved it). */
function isBackgrounded(result: unknown): boolean {
  return typeof result === 'object' && result !== null && 'backgroundTaskId' in result
    && typeof result.backgroundTaskId === 'string'
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

  const done = list.filter(a => FINISHED[a.status] !== undefined)
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

  on('session.end', async (_, e, next) => {
    ticker?.cancel()
    ticker = null
    cancelPoll(CANCELLED)
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

  // The fallback: calls next(e) once and returns its result unchanged; its own errors are caught.
  on('tool.call', { tool: 'Bash' }, async ($, e, next) => {
    let laneId: string | null = null
    try {
      const seat = matches(e.command, (await read($, seatsAtom)).doc?.local.seats ?? [])
      if (seat !== undefined) {
        const startMs = await $.clock.now()
        const id = e.tool_use_id
        const lane = openLane(seat, id, startMs, e.description ?? null, e.run_in_background === true)
        await update($, lanesAtom, s => ({ ...s, lanes: prune([...s.lanes.filter(l => l.id !== id), lane]) }))
        laneId = id
      }
    } catch {
      laneId = null
    }
    const ran = await next(e)
    if (laneId !== null) {
      const id = laneId
      try {
        const endMs = await $.clock.now()
        if (ran.deny !== undefined) {
          await update($, lanesAtom, s => ({ ...s, lanes: s.lanes.filter(l => l.id !== id) })) // it never ran
        } else if (e.run_in_background === true || isBackgrounded(ran.result)) {
          await update($, lanesAtom, s => ({ ...s, lanes: setBackground(s.lanes, id) })) // a usage row closes it
        } else {
          await update($, lanesAtom, s => ({ ...s, lanes: closeLane(s.lanes, id, endMs, ran.isError === true ? 'failed' : 'ok') }))
        }
      } catch {
        // the lane closes at run_max_s
      }
    }
    return ran
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
