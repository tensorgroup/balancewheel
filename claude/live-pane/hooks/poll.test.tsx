import { expect, test } from 'claude-code/testing'

import { agent, append, boot, CWD, doc, drawn, paneProps, resultRow, T0, useRow } from './fixture'

test('bw live gets its argv: bwPath, the session folder, and the session start in whole seconds', async ($, on) => {
  const b = await boot($, on, { out: JSON.stringify(doc()) })
  await b.clock.advance(5000)
  expect(b.argvs[0]).toEqual(['bw', 'live', '--json', '--dir', CWD, '--since', String(Math.floor(T0 / 1000))])
})

test('the bwPath option names the command', { options: { bwPath: '/opt/tools/bw' } }, async ($, on) => {
  const b = await boot($, on, { out: JSON.stringify(doc()) })
  await b.clock.advance(5000)
  expect(b.argvs[0]?.[0]).toBe('/opt/tools/bw')
})

for (const bwPath of ['bin/bw', './bw', '~/bin/bw', 'tools/bw']) {
  test(`a bwPath that is not absolute (${bwPath}) is never run, and the age line says why`, { options: { bwPath } }, async ($, on) => {
    const b = await boot($, on, { out: JSON.stringify(doc()) })
    await b.clock.advance(15_000)
    expect(b.argvs.length).toBe(0)
    const ages = (await drawn($)).filter(t => t.startsWith('seat data:'))
    expect(ages).toEqual(['seat data: none yet (bw: bwPath must be an absolute path or bw; not run)'])
  })
}

test('it polls every 5 s while the pane is open', async ($, on) => {
  const open = await boot($, on, { out: JSON.stringify(doc()) })
  await open.clock.advance(15_000)
  expect(open.argvs.length).toBe(3)
})

test('no poll while the pane is closed', async ($, on) => {
  const closed = await boot($, on, { out: JSON.stringify(doc()), isPaneOpen: false })
  await closed.clock.advance(15_000)
  expect(closed.argvs.length).toBe(0)
})

test('no poll while the pane waits undrawn', async ($, on) => {
  const waiting = await boot($, on, { out: JSON.stringify(doc()), isPanePlaced: false })
  await waiting.clock.advance(15_000)
  expect(waiting.argvs.length).toBe(0)
})

test('a config-error document is a failed poll: the last good data stays and the age line says why', async ($, on) => {
  const bad = JSON.stringify({ ...doc({ local: { seats: [], logs: [], families: {}, run_max_s: 2700 } }), complete: false, error: 'config error: bad key' })
  const b = await boot($, on, { out: n => (n === 1 ? JSON.stringify(doc()) : bad) })
  await b.clock.advance(10_000)
  expect(b.argvs.length).toBe(2)
  const ui = await $.ui.mount({ ...paneProps(90, 40), surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /seat data/ }))?.text).toMatch(/s old \(bw: config error: bad key\)/)
  await ui.unmount()
  await append($, useRow('toolu_1', './my-seat.sh go')) // the seats of the last good document still match
  await append($, resultRow('toolu_1'))
  await b.clock.settle()
  const ui2 = await $.ui.mount({ ...paneProps(90, 40), surface: 'terminal' })
  expect(await ui2.find({ type: 'Text', text: /local seat · ok/ })).toBeDefined()
  await ui2.unmount()
})

test('a slow bw live is never called twice at once, and the 10 s limit ends it', async ($, on) => {
  const b = await boot($, on) // its bw live never ends by itself
  await b.clock.advance(1000) // the first tick starts the first call
  expect(b.argvs.length).toBe(1)
  await b.clock.advance(9000) // 9 s into the call: nine more ticks have passed
  expect(b.argvs.length).toBe(1)
  expect(b.active()).toBe(1)
  await b.clock.advance(2000) // past 10 s: the pane cancels it
  expect(b.active()).toBe(0)
  expect(b.argvs.length).toBe(1)
  await b.clock.advance(4000) // 5 s after the call ended, the next one starts
  expect(b.argvs.length).toBe(2)
  expect(b.maxActive()).toBe(1)
  const ui = await $.ui.mount({ ...paneProps(90, 40), surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /seat data/ }))?.text).toMatch(/timed out/)
  await ui.unmount()
})

test('the end of the session cancels a call in flight and stops the timer', async ($, on) => {
  const b = await boot($, on)
  await b.clock.advance(1000)
  expect(b.active()).toBe(1)
  await $.session.end({ reason: 'other', sessionId: 's1', resume: { id: 's1' } })
  await b.clock.settle()
  expect(b.active()).toBe(0)
  await b.clock.advance(20_000)
  expect(b.argvs.length).toBe(1)
})

test('after /clear the timer and the polls go on, the session state starts over, and --since moves', async ($, on) => {
  const b = await boot($, on, {
    agents: [agent({ id: 'a2', type: 'Plan', status: 'completed', description: 'outline the steps' })],
    out: JSON.stringify(doc()),
  })
  await b.clock.advance(10_000)
  expect((await drawn($)).some(t => t.includes('outline the steps'))).toBe(true)
  // the old session's finished agent may still be listed: it does not come back into the history
  b.usage.startedAt = T0 + 10_000 // the new session counts from the clear
  await $.session.end({ reason: 'clear', sessionId: 's1', resume: { id: 's1' } })
  const before = b.argvs.length
  await b.clock.advance(10_000)
  expect(b.argvs.length).toBeGreaterThan(before)
  expect(b.argvs[b.argvs.length - 1]?.[6]).toBe(String(Math.floor((T0 + 10_000) / 1000)))
  const texts = await drawn($)
  expect(texts.some(t => t.includes('outline the steps'))).toBe(false)
  expect(texts.some(t => t.includes('no finished runs yet'))).toBe(true)
  expect(texts.some(t => /^◷ 1\ds/.test(t))).toBe(true) // about 10 s since the clear, not 20 s
})

test('a v: 2 document is ignored', async ($, on) => {
  const b = await boot($, on, { out: JSON.stringify({ ...doc(), v: 2 }) })
  await b.clock.advance(5000)
  await append($, useRow('toolu_1', './my-seat.sh go')) // the v2 seats are not read
  await b.clock.settle()
  expect((await drawn($)).some(t => t.includes('my-seat'))).toBe(false)
  const ui = await $.ui.mount({ ...paneProps(90, 40), surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /seat data/ }))?.text).toMatch(/v 2/)
  await ui.unmount()
})

test('output that is not JSON is ignored, and the last good data stays', async ($, on) => {
  const b = await boot($, on, { out: n => (n === 1 ? JSON.stringify(doc()) : 'Traceback: oops') })
  await b.clock.advance(10_000)
  expect(b.argvs.length).toBe(2)
  const ui = await $.ui.mount({ ...paneProps(90, 40), surface: 'terminal' })
  expect((await ui.find({ type: 'Text', text: /seat data/ }))?.text).toMatch(/not JSON/)
  await ui.unmount()
})
