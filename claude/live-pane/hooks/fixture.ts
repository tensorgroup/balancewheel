// Test fixture: the engine beneath the pane, answered from memory. Synthetic data only.
import { mock } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'
import type { AgentInfo, On, OpEventResult, ProcessSpawnChunk, SessionUsage } from 'claude-code'

import type { LiveDoc, LocalSeat } from '../types'

export const PANE_ID = 'live-pane'
export const T0 = 1_800_000_000_123
export const CWD = '/work/app'

export const SEAT: LocalSeat = {
  name: 'my-seat', match: 'my-seat.sh', metric: 'my-seat-m', model: 'model-a', icon: null, color: '#2a9d8f',
}

export function paneProps(columns: number, rows: number) {
  return {
    plugin: 'live-pane',
    component: 'Pane',
    requestId: PANE_ID,
    props: {
      title: 'Live',
      isFocused: false,
      bodyColumns: columns,
      placement: 'dock',
      scroll: { offset: 0, bodyRows: rows },
      view: {},
    },
  } as const
}

export function doc(over: Partial<LiveDoc> = {}, nowS = T0 / 1000): LiveDoc {
  return {
    v: 1, now: nowS, complete: true, open: [], recent: [], usage: [],
    local: { seats: [SEAT], logs: [], families: {}, run_max_s: 2700 }, panels: [], ...over,
  }
}

export function agent(over: Partial<AgentInfo>): AgentInfo {
  return {
    id: 'a1', description: 'read the docs', type: 'Explore', status: 'running', kind: 'local_agent',
    taskStatus: 'running', label: 'read the docs', startTime: T0, cwd: CWD, model: 'model-b',
    contextWindowSize: 200_000, tokenCount: 50_000, tokenSamples: [20_000, 50_000], hasRow: true, ...over,
  }
}

export function usage(over: Partial<SessionUsage> = {}): SessionUsage {
  return {
    startedAt: T0,
    context: { tokens: 46_000, window: 200_000, percent: 23 },
    rateLimits: [{ kind: 'five_hour', percentUsed: 23 }],
    cost: { usd: 1.24 },
    ...over,
  }
}

export type Boot = {
  agents?: AgentInfo[]
  usage?: SessionUsage
  isPaneOpen?: boolean
  /** False: the pane is listed but waits undrawn (a narrow terminal, a `-p` run). */
  isPanePlaced?: boolean
  /** What `bw live` prints; a function answers per call; `null` gives a spawn that cannot start. */
  out?: string | null | ((n: number) => string)
  /** The Bash tool beneath the pane. */
  tool?: () => ToolAnswer | Promise<ToolAnswer>
}

export type ToolAnswer = {
  result: { stdout: string; stderr: string; interrupted: boolean; backgroundTaskId?: string }
}

export const QUIET: ToolAnswer = { result: { stdout: '', stderr: '', interrupted: false } }

export type Booted = {
  clock: ReturnType<typeof mock.clock>
  argvs: string[][]
  active: () => number
  maxActive: () => number
  agents: { list: AgentInfo[] }
}

/** Answers every `$` call the pane makes, then starts the session (which starts the pane's timers). */
export async function boot($: Engine, on: On, b: Boot = {}): Promise<Booted> {
  const clock = mock.clock(on, { now: T0 })
  const argvs: string[][] = []
  const agents = { list: b.agents ?? [] }
  let active = 0
  let maxActive = 0
  let calls = 0
  on('command.register', () => ({ value: { command: 'live-pane' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.panes', () => ({
    value: b.isPaneOpen === false ? [] : [{
      id: PANE_ID, title: 'Live', isShown: b.isPanePlaced !== false, isFocused: false, isPlaced: b.isPanePlaced !== false,
    }],
  }))
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('session.end', (_, e) => ({ sessionId: e.sessionId }))
  on('session.cwd', () => ({ value: CWD }))
  on('session.model', () => ({ value: 'model-a' }))
  on('session.usage', () => ({ value: b.usage ?? usage() }))
  on('agent.list', () => ({ value: agents.list }))
  on('tool.call', () => (b.tool ? b.tool() : QUIET))
  on('process.spawn', async function* (_$, e, next): AsyncGenerator<ProcessSpawnChunk, OpEventResult<'process.spawn'>> {
    argvs.push([...e.argv])
    calls += 1
    const out = typeof b.out === 'function' ? b.out(calls) : b.out
    if (out === null) {
      throw new Error('cannot start: no such file')
    }
    active += 1
    maxActive = Math.max(maxActive, active)
    try {
      if (out === undefined) {
        // a bw live that never ends: it waits until the pane cancels it
        await new Promise<void>(resolve => next.signal.addEventListener('abort', () => resolve()))
        return { value: { code: null, signal: 'SIGTERM' } }
      }
      yield { stream: 'stdout' as const, text: out }
      return { value: { code: 0, signal: null } }
    } finally {
      active -= 1
    }
  })
  await $.session.start({ cwd: CWD, surface: 'terminal', isInteractive: true })
  return { clock, argvs, active: () => active, maxActive: () => maxActive, agents }
}

/** Every text the pane draws now, in order: what a test compares before and after an act. */
export async function drawn($: Engine, columns = 90, rows = 40): Promise<string[]> {
  const ui = await $.ui.mount({ ...paneProps(columns, rows), surface: 'terminal' })
  const texts = (await ui.findAll({ type: 'Text' })).map(t => t.text)
  await ui.unmount()
  return texts
}
