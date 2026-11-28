import { expect, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'

import type { Lane, OpenRun, RecentRun, UsageRow } from '../types'
import { bashUses, dropWrapped, expire, matches, pair, reconcile, toolResults } from './fallback'
import { append, boot, doc, drawn, paneProps, resultRow, SEAT, T0, useRow } from './fixture'

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

test('bashUses reads Bash tool_use blocks; toolResults reads tool_result blocks', () => {
  const uses = bashUses([
    { type: 'text', text: 'running it' },
    { type: 'tool_use', id: 'toolu_1', name: 'Bash', input: { command: './my-seat.sh go', description: 'review', run_in_background: true } },
    { type: 'tool_use', id: 'toolu_2', name: 'Read', input: { file_path: 'my-seat.sh' } },
    { type: 'tool_use', id: 'toolu_3', name: 'Bash', input: { command: 42 } },
  ])
  expect(uses).toEqual([{ id: 'toolu_1', command: './my-seat.sh go', description: 'review', isBackground: true }])
  expect(toolResults([{ type: 'tool_result', tool_use_id: 'toolu_1', is_error: true }, { type: 'text', text: 'x' }]))
    .toEqual([{ id: 'toolu_1', isError: true }])
})

// ---- through the engine: the pane observes stored rows, never the tool call -------------------------

async function laneTexts($: Engine): Promise<string[]> {
  const ui = await $.ui.mount({ ...paneProps(90, 40), surface: 'terminal' })
  const texts = (await ui.findAll({ type: 'Text', text: /├/ })).map(t => t.text)
  await ui.unmount()
  return texts
}

test('a tool_use row of a Bash call that matches a local seat opens a lane, and its tool_result closes it', async ($, on) => {
  const b = await boot($, on, { out: JSON.stringify(doc()) })
  await b.clock.advance(5000) // the first bw live poll gives the pane its local seats
  await append($, useRow('toolu_1', './my-seat.sh review', { description: 'review the diff' }))
  await b.clock.settle()
  expect((await laneTexts($)).some(t => t.includes('my-seat'))).toBe(true)
  await b.clock.advance(3000)
  await append($, resultRow('toolu_1'))
  await b.clock.settle()
  expect((await laneTexts($)).some(t => t.includes('my-seat'))).toBe(false)
  const ui = await $.ui.mount({ ...paneProps(90, 40), surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /local seat · ok/ }))?.text).toMatch(/review the diff/)
  expect((await ui.find({ type: 'Text', text: /^my-seat.*plan$/ }))?.text).toMatch(/\b3s\b/) // its run time
  await ui.unmount()
})

test('a failed tool_result closes the lane as failed', async ($, on) => {
  const b = await boot($, on, { out: JSON.stringify(doc()) })
  await b.clock.advance(5000)
  await append($, useRow('toolu_1', './my-seat.sh review'))
  await append($, resultRow('toolu_1', true))
  await b.clock.settle()
  const ui = await $.ui.mount({ ...paneProps(90, 40), surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /local seat · failed/ })).toBeDefined()
  await ui.unmount()
})

test('a Bash call that does not match changes nothing', async ($, on) => {
  const b = await boot($, on, { out: JSON.stringify(doc()) })
  await b.clock.advance(5000)
  const before = await drawn($)
  await append($, useRow('toolu_1', 'git status'))
  await append($, useRow('toolu_2', `${'y'.repeat(4096)} ./my-seat.sh`)) // the match is past the first 4 KB
  await append($, resultRow('toolu_1'))
  await b.clock.settle()
  expect(await drawn($)).toEqual(before)
  expect(before.some(t => t.includes('my-seat'))).toBe(false)
})

test('a background lane stays open after its tool_result, then pairs with its usage row', async ($, on) => {
  let usageRows: UsageRow[] = []
  const b = await boot($, on, { out: () => JSON.stringify(doc({ usage: usageRows })) })
  await b.clock.advance(5000)
  await append($, useRow('toolu_1', './my-seat.sh go', { isBackground: true }))
  await append($, resultRow('toolu_1')) // "running in the background"
  await b.clock.settle()
  expect((await laneTexts($)).some(t => t.includes('my-seat'))).toBe(true)
  usageRows = [row(Math.floor(b.clock.now() / 1000) + 3, { ok: false })]
  await b.clock.advance(10_000)
  expect((await laneTexts($)).some(t => t.includes('my-seat'))).toBe(false)
  const ui = await $.ui.mount({ ...paneProps(90, 40), surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /local seat · failed/ })).toBeDefined()
  await ui.unmount()
})

test('with local.run_max_s 60 an unpaired lane closes as end unknown after 60 s, not 2700 s', async ($, on) => {
  const b = await boot($, on, { out: JSON.stringify(doc({ local: { ...doc().local, run_max_s: 60 } })) })
  await b.clock.advance(5000)
  await append($, useRow('toolu_1', './my-seat.sh go', { isBackground: true }))
  await b.clock.advance(30_000)
  expect((await laneTexts($)).some(t => t.includes('my-seat'))).toBe(true)
  await b.clock.advance(32_000)
  expect((await laneTexts($)).some(t => t.includes('my-seat'))).toBe(false)
  const ui = await $.ui.mount({ ...paneProps(90, 40), surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /end unknown/ })).toBeDefined()
  await ui.unmount()
})

test('the row hook keeps the row unchanged, also when its own code throws', async ($, on) => {
  on('state.get', () => {
    throw new Error('the state is down')
  })
  const row1 = useRow('toolu_1', './my-seat.sh go')
  const kept = await append($, row1)
  expect(kept.deny).toBeUndefined()
  expect(kept.deny === undefined ? kept.message.content : null).toEqual(row1.message.content)
})
