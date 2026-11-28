import { expect, test } from 'claude-code/testing'
import type { Engine } from 'claude-code/testing'

import type { Lane, OpenRun, RecentRun, UsageRow } from '../types'
import { bashUses, dropWrapped, expire, matches, pair, toolResults, wrapSeat } from './fallback'
import { append, boot, doc, drawn, paneProps, resultRow, SEAT, T0, useRow } from './fixture'

function lane(over: Partial<Lane>): Lane {
  return {
    id: 'l1', seat: SEAT.name, metric: 'my-seat-m', model: null, icon: null, color: null, words: null,
    wrapSeat: null, startMs: T0, background: true, endMs: null, end: null, ...over,
  }
}

function row(t: number, over: Partial<UsageRow> = {}): UsageRow {
  return { seat: 'my-seat-m', t, dir: '/work/app', ok: true, source: 'local', ...over }
}

function openRun(seat: string, start: number | null, over: Partial<OpenRun> = {}): OpenRun {
  return {
    run_id: 'r000000000001', seat, model: null, label: null, dir: '/work/app', start, source: 'local',
    panel: null, ask: null, state: 'live', icon: null, color: null, ctx: null, window: null, cost_usd: null, ...over,
  }
}

function recentRun(seat: string, start: number | null, over: Partial<RecentRun> = {}): RecentRun {
  return {
    run_id: 'r000000000002', seat, model: null, label: null, start, end: start === null ? 0 : start + 5, ok: true,
    reason: null, source: 'local', stand_in_for: null, tokens_in: null, tokens_out: null, cost_usd: null,
    ctx_peak: null, ...over,
  }
}

/** The lanes left after a document lists these runs, with a fresh `taken`. */
function left(lanes: readonly Lane[], runs: readonly (OpenRun | RecentRun)[]): Lane[] {
  return dropWrapped(lanes, runs, new Set())
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

test('wrapSeat reads the --seat of a bw run in the first 4 KB of the command', () => {
  expect(wrapSeat('bw run --seat my-seat -- ./my-seat.sh go')).toBe('my-seat')
  expect(wrapSeat('/opt/tools/bw run --label "a b" --seat other -- ./my-seat.sh')).toBe('other')
  expect(wrapSeat("cd sub && bw run --seat 'my-seat' -- ./my-seat.sh")).toBe('my-seat')
  expect(wrapSeat('id=$(bw run start --seat my-seat) && ./my-seat.sh')).toBe('my-seat')
  expect(wrapSeat('./my-seat.sh --seat foo')).toBeNull() // no bw run
  expect(wrapSeat('bw run -- ./my-seat.sh --seat foo')).toBeNull() // past the --
  expect(wrapSeat('mybw run --seat foo')).toBeNull()
  expect(wrapSeat('bw runner --seat foo')).toBeNull()
  expect(wrapSeat(`${'x'.repeat(4096)} bw run --seat my-seat -- ./my-seat.sh`)).toBeNull()
})

test('a run of the seat that starts after the lane drops it, however long the call waited to start', () => {
  const l = lane({ startMs: T0 })
  for (const after of [0.2, 1.5, 2.5, 10, 600]) expect(left([l], [openRun('my-seat', T0 / 1000 + after)])).toEqual([])
  expect(left([l], [openRun('my-seat', T0 / 1000 - 1.9)])).toEqual([]) // the old 2 s case
  expect(left([l], [recentRun('my-seat', T0 / 1000 + 10)])).toEqual([]) // it ended between two polls
  expect(left([l], [openRun('my-seat', T0 / 1000 - 2.5)])).toEqual([l]) // it began before the call
  expect(left([l], [openRun('another', T0 / 1000 + 1)])).toEqual([l])
  expect(left([l], [openRun('my-seat', null)])).toEqual([l])
  expect(left([l], [openRun('my-seat', T0 / 1000 + 1, { source: 'bw' })])).toEqual([l]) // bw seat runs no Bash call
  expect(left([l], [openRun('my-seat', T0 / 1000 + 1, { run_id: null })])).toEqual([l])
})

test('a closed lane is dropped only by a run that started before it closed', () => {
  const l = lane({ startMs: T0, endMs: T0 + 5000, end: 'ok', background: false })
  expect(left([l], [recentRun('my-seat', T0 / 1000 + 3)])).toEqual([])
  expect(left([l], [recentRun('my-seat', T0 / 1000 + 30)])).toEqual([l])
})

test('one run drops one lane, also at later polls', () => {
  const first = lane({ id: 'l1', startMs: T0 })
  const second = lane({ id: 'l2', startMs: T0 })
  const runs = [openRun('my-seat', T0 / 1000 + 10)]
  const taken = new Set<string>()
  const once = dropWrapped([first, second], runs, taken)
  expect(once).toEqual([second]) // the calls start in order: the first lane is the run's
  expect(dropWrapped(once, runs, taken)).toEqual([second])
  expect(dropWrapped(once, [...runs, recentRun('my-seat', T0 / 1000 + 12)], taken)).toEqual([])
})

test('a lane whose command shows the bw run is taken first, by the seat that bw run names', () => {
  const plain = lane({ id: 'plain', startMs: T0 })
  const wrapped = lane({ id: 'wrapped', startMs: T0 + 1000, wrapSeat: 'my-seat' })
  expect(left([plain, wrapped], [openRun('my-seat', T0 / 1000 + 5)])).toEqual([plain])
  const other = lane({ id: 'other', startMs: T0, wrapSeat: 'other' })
  expect(left([other], [openRun('other', T0 / 1000 + 5)])).toEqual([])
  expect(left([other], [openRun('my-seat', T0 / 1000 + 5)])).toEqual([other])
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

for (const bg of [false, true]) {
  for (const waitS of [1.5, 10]) {
    test(`a ${bg ? 'background' : 'foreground'} bw run whose Bash call waits ${waitS} s to start shows one lane, and its end row closes it`, async ($, on) => {
      let open: OpenRun[] = []
      let recent: RecentRun[] = []
      const b = await boot($, on, { out: () => JSON.stringify(doc({ open, recent, local: { ...doc().local, run_max_s: 60 } })) })
      await b.clock.advance(5000)
      const count = async () => (await laneTexts($)).filter(t => t.includes('my-seat')).length
      const t1 = b.clock.now()
      await append($, useRow('toolu_1', 'bw run --seat my-seat -- ./my-seat.sh review', { isBackground: bg }))
      await b.clock.settle()
      expect(await count()).toBe(1) // the call waits (a permission prompt): the fallback lane
      await b.clock.advance(waitS * 1000)
      const start = t1 / 1000 + waitS
      open = [openRun('my-seat', start, { run_id: 'r00000000000a' })]
      if (bg) await append($, resultRow('toolu_1')) // "running in the background"
      await b.clock.advance(5000)
      expect(await count()).toBe(1) // the run's own lane
      open = []
      recent = [recentRun('my-seat', start, { run_id: 'r00000000000a', end: start + 20 })]
      if (!bg) await append($, resultRow('toolu_1'))
      await b.clock.advance(20_000)
      expect(await count()).toBe(0)
      const texts = await drawn($)
      expect(texts.filter(t => /local seat ·/.test(t)).length).toBe(1)
      await b.clock.advance(70_000) // past run_max_s: nothing left to expire
      expect(await count()).toBe(0)
      expect((await drawn($)).filter(t => /local seat ·/.test(t)).length).toBe(1)
    })
  }
}

test('terminal control text in a Bash description is not drawn', async ($, on) => {
  const b = await boot($, on, { out: JSON.stringify(doc()) })
  await b.clock.advance(5000)
  await append($, useRow('toolu_1', './my-seat.sh review',
    { description: '\u001b]0;a title\u0007review \u001b[31mthe diff\u001b[0m\u009b2J' }))
  await append($, resultRow('toolu_1'))
  await b.clock.settle()
  const texts = await drawn($)
  expect(texts.find(t => /local seat · ok/.test(t))).toMatch(/local seat · ok review the diff$/)
  expect(texts.some(t => /[\u0000-\u001f\u007f-\u009f]/.test(t))).toBe(false)
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
