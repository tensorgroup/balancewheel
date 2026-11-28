import { expect, test } from 'claude-code/testing'

import type { OpenRun, RecentRun } from '../types'
import { agent, boot, doc, paneProps, T0 } from './fixture'

const SURFACES = ['terminal', 'desktop', 'vscode', 'mobile'] as const

const seatRun: OpenRun = {
  run_id: 'r0000000000aa', seat: 'seat-a', model: 'model-c', label: 'p0001 round 1', dir: '/work/app',
  start: T0 / 1000 + 2, source: 'bw', panel: 'p0001abc', ask: null, state: 'live', icon: null, color: null,
  ctx: 41_000, window: null, cost_usd: null,
}

const finishedSeat: RecentRun = {
  run_id: 'r0000000000bb', seat: 'seat-b', model: 'model-d', label: 'p0001 round 1', start: T0 / 1000 - 300,
  end: T0 / 1000 - 60, ok: true, reason: null, source: 'bw', stand_in_for: null, tokens_in: 12_000,
  tokens_out: 3_000, cost_usd: 0.02, ctx_peak: 113_000,
}

test('it draws with no data, on every surface', async $ => {
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...paneProps(90, 40), surface })
    expect((await ui.find({ type: 'Text', text: /waiting for the first reading/ }))?.text).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /this worktree, since this session started/ })).toBeDefined()
    await ui.unmount()
  }
})

test('it draws lanes, figures, both charts, and the history from samples and finished runs', async ($, on) => {
  const b = await boot($, on, {
    agents: [agent({ id: 'a1', type: 'Explore', status: 'running' }),
      agent({ id: 'a2', type: 'Plan', status: 'completed', description: 'outline the steps', startTime: T0 - 90_000 })],
    out: JSON.stringify(doc({ open: [seatRun], recent: [finishedSeat] })),
  })
  await b.clock.advance(30_000)
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...paneProps(90, 50), surface })
    const lanes = (await ui.findAll({ type: 'Text', text: /├/ })).map(t => t.text)
    expect(lanes.length).toBe(3)
    expect(lanes[0]).toMatch(/main/)
    expect(lanes[0]).toMatch(/23%/)
    expect(lanes.some(t => t.includes('Explore') && t.includes('25%'))).toBe(true)
    expect(lanes.some(t => t.includes('seat-a') && t.includes('41k'))).toBe(true)
    expect((await ui.find({ type: 'Text', text: /≈\$1\.24/ }))?.text).toMatch(/live 2/)
    expect(await ui.find({ type: 'Text', text: /^context/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^agents at work/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /this worktree, since this session started/ })).toBeDefined()
    expect((await ui.find({ type: 'Text', text: /subagent · done/ }))?.text).toMatch(/outline the steps/)
    expect((await ui.find({ type: 'Text', text: /seat-b/ }))?.text).toMatch(/≈\$0\.02/)
    expect((await ui.find({ type: 'Text', text: /bw seat · ok/ }))?.text).toMatch(/12k in/)
    expect((await ui.find({ type: 'Text', text: /seat data/ }))?.text).toMatch(/s old/)
    await ui.unmount()
  }
})

test('when bw is missing the pane still draws Claude\'s agents and the age of its seat data', async ($, on) => {
  const b = await boot($, on, { agents: [agent({ id: 'a1', type: 'Explore' })], out: null })
  await b.clock.advance(6000)
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ ...paneProps(90, 40), surface })
    const lanes = (await ui.findAll({ type: 'Text', text: /├/ })).map(t => t.text)
    expect(lanes.some(t => t.includes('Explore'))).toBe(true)
    expect((await ui.find({ type: 'Text', text: /seat data/ }))?.text).toMatch(/none yet.*cannot start/)
    await ui.unmount()
  }
})

test('seat lanes from old data stop moving and say so', async ($, on) => {
  const b = await boot($, on, { out: n => (n === 1 ? JSON.stringify(doc({ open: [seatRun] })) : 'not json') })
  await b.clock.advance(40_000)
  const ui = await $.ui.mount({ ...paneProps(90, 40), surface: 'terminal' })
  const lane = (await ui.findAll({ type: 'Text', text: /seat-a/ })).map(t => t.text).find(t => t.includes('├'))
  expect(lane).toMatch(/old/)
  await ui.unmount()
})

test('a narrow pane drops the charts first and keeps the history', async ($, on) => {
  const b = await boot($, on, { out: JSON.stringify(doc({ recent: [finishedSeat] })) })
  await b.clock.advance(10_000)
  const ui = await $.ui.mount({ ...paneProps(34, 40), surface: 'terminal' })
  expect(await ui.find({ type: 'Text', text: /^context/ })).toBeUndefined()
  expect(await ui.find({ type: 'Text', text: /seat-b/ })).toBeDefined()
  await ui.unmount()
})

test('a run found by its beat alone moves, and its run time reads ?', async ($, on) => {
  const beatOnly: OpenRun = { ...seatRun, run_id: 'r0000000000dd', seat: 'seat-c', source: 'local', start: null, ctx: null }
  const b = await boot($, on, { out: JSON.stringify(doc({ open: [beatOnly] })) })
  await b.clock.advance(4000)
  const ui = await $.ui.mount({ ...paneProps(90, 40), surface: 'terminal' })
  const lane = (await ui.findAll({ type: 'Text', text: /seat-c/ })).map(t => t.text).find(t => t.includes('├'))
  expect(lane).toMatch(/\?\s*$/)
  expect(lane).not.toMatch(/├●/) // the bead has left the start of its track
  await ui.unmount()
})
