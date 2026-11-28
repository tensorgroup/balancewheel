import { expect, test } from 'claude-code/testing'

import { beadAt, CHART_ROWS, speed, split } from './layout'

test('the bead moves slower as the context fills, and at a middle pace with no window', () => {
  expect(speed(0)).toBeGreaterThan(speed(0.5))
  expect(speed(0.5)).toBeGreaterThan(speed(1))
  expect(speed(null)).toBeLessThan(speed(0))
  expect(speed(null)).toBeGreaterThan(speed(1))
  // after 4 s on a 10-cell track: an empty context has made 15 steps (swung back to 3), a full one 1 step
  expect(beadAt(4000, 0, 0, 10)).toBe(3)
  expect(beadAt(4000, 0, 1, 10)).toBe(1)
})

test('the bead swings from end to end and stays on its track', () => {
  const seen = Array.from({ length: 40 }, (_, i) => beadAt(i * 1000, 0, null, 8))
  expect(Math.min(...seen)).toBe(0)
  expect(Math.max(...seen)).toBe(7)
})

test('the room: lanes first, the charts 11 rows when there is room, the history the rest', () => {
  expect(split(40, 90, 3)).toEqual({ lanes: 3, figures: 2, charts: CHART_ROWS, history: 24 })
  expect(split(15, 90, 3)).toEqual({ lanes: 3, figures: 2, charts: 0, history: 10 })
  expect(split(40, 30, 3)).toEqual({ lanes: 3, figures: 2, charts: 0, history: 35 })
})
