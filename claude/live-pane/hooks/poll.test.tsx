import { expect, test } from 'claude-code/testing'

import { boot, CWD, doc, drawn, paneProps, T0 } from './fixture'

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
  await $.tool.call({ tool: 'Bash', command: './my-seat.sh go' }) // the seats of the last good document still match
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

test('a v: 2 document is ignored', async ($, on) => {
  const b = await boot($, on, { out: JSON.stringify({ ...doc(), v: 2 }) })
  await b.clock.advance(5000)
  await $.tool.call({ tool: 'Bash', command: './my-seat.sh go' }) // the v2 seats are not read
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
