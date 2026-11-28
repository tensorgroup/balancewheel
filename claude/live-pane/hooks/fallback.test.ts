import { expect, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'

import type { Lane, OpenRun, RecentRun, UsageRow } from '../types'
import { dropWrapped, expire, matches, pair, reconcile } from './fallback'
import { boot, doc, drawn, paneProps, SEAT, T0 } from './fixture'

function lane(over: Partial<Lane>): Lane {
  return {
    id: 'l1', seat: SEAT.name, metric: 'my-seat-m', model: null, icon: null, color: null, words: null,
    startMs: T0, background: true, endMs: null, end: null, ...over,
  }
}

function row(t: number, over: Partial<UsageRow> = {}): UsageRow {
  return { seat: 'my-seat-m', t, dir: '/work/app', ok: true, source: 'local', ...over }
}

function openRun(seat: string, start: number | null): OpenRun {
  return {
    run_id: 'r000000000001', seat, model: null, label: null, dir: '/work/app', start, source: 'local',
    panel: null, ask: null, state: 'live', icon: null, color: null, ctx: null, window: null, cost_usd: null,
  }
}

function recentRun(seat: string, start: number | null): RecentRun {
  return {
    run_id: 'r000000000002', seat, model: null, label: null, start, end: start === null ? 0 : start + 5, ok: true,
    reason: null, source: 'local', stand_in_for: null, tokens_in: null, tokens_out: null, cost_usd: null,
    ctx_peak: null,
  }
}

// ---- pure ------------------------------------------------------------------------------------------

test('matches: a plain substring of the first 4 KB, never an empty match', () => {
  expect(matches('run my-seat.sh --fast', [SEAT])?.name).toBe('my-seat')
  expect(matches('ls -la', [SEAT])).toBeUndefined()
  expect(matches(`${'x'.repeat(4096)}my-seat.sh`, [SEAT])).toBeUndefined()
  expect(matches(`${'x'.repeat(4086)}my-seat.sh`, [SEAT])?.name).toBe('my-seat')
  expect(matches('anything at all', [{ ...SEAT, match: '' }])).toBeUndefined()
  expect(matches('my.seat', [{ ...SEAT, match: 'my.seat.sh' }])).toBeUndefined() // no pattern meaning
})

test('pair gives the older lane the shared row', () => {
  const older = lane({ id: 'old', startMs: T0 })
  const newer = lane({ id: 'new', startMs: T0 + 2000 })
  const rows = [row(T0 / 1000 + 5)]
  const taken = new Set<string>()
  const got = pair([newer, older], rows, taken)
  expect(got.get('old')).toBeDefined()
  expect(got.get('new')).toBeUndefined()
  expect(taken.size).toBe(1)
  // the row is now taken: the newer lane cannot have it in a later poll
  expect(pair([newer], rows, taken).size).toBe(0)
})

test('pair takes the oldest row at or after the start, less 1 s, of the same metric', () => {
  const l = lane({ startMs: T0 + 800 }) // 0.923 s after the whole second of the row: inside the 1 s precision
  const rows = [row(Math.floor(T0 / 1000) + 9), row(Math.floor(T0 / 1000)), row(Math.floor(T0 / 1000) - 5),
    row(Math.floor(T0 / 1000) + 1, { seat: 'other' })]
  const got = pair([l], rows, new Set())
  expect(got.get('l1')).toBe(`my-seat-m|/work/app|${Math.floor(T0 / 1000)}|0`)
})

test('a lane is dropped when a run start for its seat is within 2 s', () => {
  const l = lane({ startMs: T0 })
  expect(dropWrapped([l], [openRun('my-seat', T0 / 1000 + 1.5)])).toEqual([])
  expect(dropWrapped([l], [openRun('my-seat', T0 / 1000 - 1.9)])).toEqual([])
  expect(dropWrapped([l], [openRun('my-seat', T0 / 1000 + 2.5)])).toEqual([l])
  expect(dropWrapped([l], [openRun('another', T0 / 1000)])).toEqual([l])
  expect(dropWrapped([l], [openRun('my-seat', null)])).toEqual([l])
})

test('a lane is dropped when a recent run for its seat started within 2 s (it ended between two polls)', () => {
  const l = lane({ startMs: T0 })
  expect(reconcile([l], [recentRun('my-seat', T0 / 1000 + 0.4)])).toEqual([])
  expect(reconcile([l], [recentRun('my-seat', T0 / 1000 + 30)])).toEqual([l])
})

test('expire closes an unpaired lane as end unknown after run_max_s', () => {
  const l = lane({ startMs: T0 })
  expect(expire([l], T0 + 59_000, 60)).toEqual([l])
  const [closed] = expire([l], T0 + 61_000, 60)
  expect(closed?.end).toBe('unknown')
  expect(closed?.endMs).toBe(T0 + 61_000)
})

// ---- through the engine ----------------------------------------------------------------------------

const BACKGROUND = { result: { stdout: '', stderr: '', interrupted: false, backgroundTaskId: 'b1' } }

async function laneTexts($: Engine): Promise<string[]> {
  const ui = await $.ui.mount({ ...paneProps(90, 40), surface: 'terminal' })
  const texts = (await ui.findAll({ type: 'Text', text: /├/ })).map(t => t.text)
  await ui.unmount()
  return texts
}

test('a Bash call that matches a local seat opens a lane, and the call\'s end closes it', async ($, on) => {
  let during: string[] = []
  const b = await boot($, on, {
    out: JSON.stringify(doc()),
    tool: async () => {
      during = await laneTexts($)
      return { result: { stdout: 'done', stderr: '', interrupted: false } }
    },
  })
  await b.clock.advance(5000) // the first bw live poll gives the pane its local seats
  await $.tool.call({ tool: 'Bash', command: './my-seat.sh review', description: 'review the diff' })
  expect(during.some(t => t.includes('my-seat'))).toBe(true)
  const ui = await $.ui.mount({ ...paneProps(90, 40), surface: 'terminal' })
  const lanes = (await ui.findAll({ type: 'Text', text: /├/ })).map(t => t.text)
  expect(lanes.some(t => t.includes('my-seat'))).toBe(false)
  expect((await ui.find({ type: 'Text', text: /local seat · ok/ }))?.text).toMatch(/review the diff/)
  await ui.unmount()
})

test('a Bash call that does not match changes nothing', async ($, on) => {
  const b = await boot($, on, { out: JSON.stringify(doc()) })
  await b.clock.advance(5000)
  const before = await drawn($)
  await $.tool.call({ tool: 'Bash', command: 'git status' })
  await $.tool.call({ tool: 'Bash', command: `${'y'.repeat(4096)} ./my-seat.sh` }) // the match is past the first 4 KB
  expect(await drawn($)).toEqual(before)
  expect(before.some(t => t.includes('my-seat'))).toBe(false)
})

test('a background lane stays open after the call, then pairs with its usage row', async ($, on) => {
  let usageRows: UsageRow[] = []
  const b = await boot($, on, { out: () => JSON.stringify(doc({ usage: usageRows })), tool: () => BACKGROUND })
  await b.clock.advance(5000)
  await $.tool.call({ tool: 'Bash', command: './my-seat.sh go', run_in_background: true })
  expect((await laneTexts($)).some(t => t.includes('my-seat'))).toBe(true)
  usageRows = [row(Math.floor(b.clock.now() / 1000) + 3, { ok: false })]
  await b.clock.advance(10_000)
  expect((await laneTexts($)).some(t => t.includes('my-seat'))).toBe(false)
  const ui = await $.ui.mount({ ...paneProps(90, 40), surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /local seat · failed/ })).toBeDefined()
  await ui.unmount()
})

test('with local.run_max_s 60 an unpaired lane closes as end unknown after 60 s, not 2700 s', async ($, on) => {
  const b = await boot($, on, { out: JSON.stringify(doc({ local: { ...doc().local, run_max_s: 60 } })), tool: () => BACKGROUND })
  await b.clock.advance(5000)
  await $.tool.call({ tool: 'Bash', command: './my-seat.sh go', run_in_background: true })
  await b.clock.advance(30_000)
  expect((await laneTexts($)).some(t => t.includes('my-seat'))).toBe(true)
  await b.clock.advance(32_000)
  expect((await laneTexts($)).some(t => t.includes('my-seat'))).toBe(false)
  const ui = await $.ui.mount({ ...paneProps(90, 40), surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /end unknown/ })).toBeDefined()
  await ui.unmount()
})

test('the hook returns next(e)\'s result unchanged when its own code throws', async ($, on) => {
  const answer = { result: { stdout: 'kept', stderr: '', interrupted: false } }
  let calls = 0
  on('state.get', () => {
    throw new Error('the state is down')
  })
  on('tool.call', () => {
    calls += 1
    return answer
  })
  const ran = await $.tool.call({ tool: 'Bash', command: './my-seat.sh go' })
  expect(calls).toBe(1)
  expect(ran.result).toEqual(answer.result)
  expect('deny' in ran && ran.deny !== undefined).toBe(false)
})

test('the hook returns next(e)\'s result unchanged when its code throws after the call', async ($, on) => {
  const answer = { result: { stdout: 'kept too', stderr: '', interrupted: false } }
  let calls = 0
  let writesLeft = Number.POSITIVE_INFINITY
  on('state.set', (_, e, next) => {
    if (e.plugin === 'live-pane' && e.key === 'lanes') {
      writesLeft -= 1
      if (writesLeft < 0) throw new Error('the state is down')
    }
    return next(e)
  })
  const b = await boot($, on, {
    out: JSON.stringify(doc()),
    tool: () => {
      calls += 1
      return answer
    },
  })
  await b.clock.advance(5000)
  writesLeft = 1 // the lane opens; closing it throws
  const ran = await $.tool.call({ tool: 'Bash', command: './my-seat.sh go' })
  expect(writesLeft).toBe(-1)
  expect(calls).toBe(1)
  expect(ran.result).toEqual(answer.result)
})
