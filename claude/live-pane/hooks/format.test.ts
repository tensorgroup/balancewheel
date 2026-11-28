import { expect, test } from 'claude-code/testing'

import { clean, cut, pad } from './format'
import { glyph } from './layout'

test('cut removes CSI, OSC, BEL, and C1 controls and keeps the printable text', () => {
  expect(cut('\u001b[31mred\u001b[0m', 20)).toBe('red')
  expect(cut('\u001b]8;;https://example.invalid/x\u0007link\u001b]8;;\u0007 text', 20)).toBe('link text')
  expect(cut('\u001b]8;;https://example.invalid/x\u001b\\link\u001b]8;;\u001b\\', 20)).toBe('link') // ST ends it
  expect(cut('\u001b]0;a new title\u0007kept', 20)).toBe('kept')
  expect(cut('ring\u0007bell', 20)).toBe('ring bell')
  expect(cut('a\u0085b', 20)).toBe('a b') // C1 NEL
  expect(cut('a\u009b2Jb', 20)).toBe('ab') // a C1 CSI is a whole sequence
  expect(cut('a\u009d0;t\u009cb', 20)).toBe('ab') // a C1 OSC up to a C1 ST
  expect(cut('\u001bcreset', 20)).toBe('reset') // a two-character escape
  expect(cut('x\u0000y\u007fz', 20)).toBe('x y z')
})

test('an OSC with no end keeps the text after it', () => {
  expect(cut('\u001b]8;;rest of it', 30)).toBe('8;;rest of it')
})

test('the width counts only what is drawn', () => {
  expect(cut('\u001b[1mabcdef\u001b[0m', 4)).toBe('abc…')
  expect(pad('\u001b[31mab\u001b[0m', 4)).toBe('ab  ')
  expect(clean('plain text, 中文 ok')).toBe('plain text, 中文 ok')
})

test('hostile control text is cleaned in linear time', () => {
  for (const text of ['\u009d'.repeat(200_000), '\u009dab'.repeat(70_000), '\u001b]'.repeat(100_000),
    `\u001b]${'x'.repeat(200_000)}`, '\u0090\u0098\u009e\u009f'.repeat(50_000)]) {
    const t0 = performance.now()
    clean(text)
    cut(text, 40)
    expect(performance.now() - t0).toBeLessThan(250)
  }
  expect(cut('\u009d0;t\u009dab\u009cok', 20)).toBe('ok') // a sequence ends at the next introducer
  expect(cut(`${'x'.repeat(5000)}tail`, 10_000)).toBe('x'.repeat(4096)) // no more than DRAW_CHARS is read
})

test('a lane icon is cleaned too', () => {
  expect(glyph('local', '\u001b[31m')).toBe('▢ ')
  expect(glyph('local', '\u0007🦉')).toBe('🦉')
})
