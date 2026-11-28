// The pane's drawing: lanes, session figures, the context chart, the agents-at-work timeline, the history.
// `buildView` is pure; `drawPane` takes the surface's element table, never `$`.

import type { BoxProps, ElementConstructor, TextProps } from 'claude-code'

import type { Chart, Finished, Lane, LaneState, SeatData, Snap } from '../types'
import { ageLine, cost, cut, duration, fromFinished, fromRecent, modelLabel, pad, percent, rateLimits, tokens } from './format'
import type { HistoryItem } from './format'
import {
  bars, beadAt, columns, CONTEXT_BAR_ROWS, familyColor, glyph, seatColor, split, TIMELINE_ROWS,
} from './layout'
import type { LaneKind } from './layout'

export type Els = { Box: ElementConstructor<BoxProps>; Text: ElementConstructor<TextProps> }

/** Seat data older than this is drawn as old: its lanes stop moving. */
export const STALE_MS = 15_000
export const HISTORY_TITLE = 'this worktree, since this session started'
const RUNNING = new Set(['pending', 'running', 'waiting', 'idle'])

export type LaneRow = {
  key: string
  kind: LaneKind
  name: string
  model: string
  icon: string | null
  color: string | null
  startMs: number
  /** False for a run found by its beat alone: its run time is not known. */
  isStartKnown: boolean
  fill: number | null
  right: string
  isLive: boolean
  note: string | null
}

export type View = {
  nowMs: number
  hasSnap: boolean
  lanes: LaneRow[]
  figures: string
  age: string
  mainColor: string | null
  chart: Chart
  history: HistoryItem[]
}

/** A finished fallback lane as a history entry. */
export function laneFinished(l: Lane): Finished {
  return {
    key: `l:${l.id}`, kind: 'local', name: l.seat, model: l.model, startMs: l.startMs, endMs: l.endMs ?? l.startMs,
    ctxPeakPct: null, tokens: null, costUsd: null, outcome: l.end === 'unknown' || l.end === null ? 'end unknown' : l.end,
    words: l.words, color: seatColor(l.seat, l.color),
  }
}

export function buildView(snap: Snap | null, seats: SeatData, laneState: LaneState, finished: readonly Finished[], chart: Chart): View {
  const nowMs = snap?.at ?? seats.okAt ?? 0
  const doc = seats.doc
  const families = doc?.local.families ?? {}
  const isFresh = seats.okAt !== null && nowMs - seats.okAt <= STALE_MS
  const rows: LaneRow[] = []
  if (snap !== null) {
    for (const a of snap.agents.filter(x => RUNNING.has(x.status))) {
      const fill = a.window === null || a.window <= 0 ? null : a.tokens / a.window
      rows.push({
        key: `a:${a.id}`, kind: 'subagent', name: a.name, model: modelLabel(a.model, a.effort), icon: null,
        color: familyColor(a.model, families), startMs: a.startMs, isStartKnown: true, fill,
        right: fill === null ? tokens(a.tokens) : percent(fill * 100), isLive: true, note: null,
      })
    }
  }
  for (const r of doc?.open ?? []) {
    const name = r.seat ?? 'seat'
    const fill = r.ctx !== null && r.window !== null && r.window > 0 ? r.ctx / r.window : null
    const isLive = isFresh && r.state === 'live'
    rows.push({
      key: `s:${r.run_id ?? `${r.panel ?? ''}:${name}`}`, kind: r.source === 'local' ? 'local' : 'bw', name,
      model: modelLabel(r.model), icon: r.icon, color: seatColor(name, r.color),
      startMs: r.start === null ? seats.okAt ?? nowMs : r.start * 1000, isStartKnown: r.start !== null, fill,
      right: isLive ? (fill !== null ? percent(fill * 100) : r.ctx !== null ? tokens(r.ctx) : '') : isFresh ? r.state : 'old',
      isLive, note: isLive ? null : isFresh ? r.state : 'old',
    })
  }
  for (const l of laneState.lanes.filter(x => x.endMs === null)) {
    rows.push({
      key: `l:${l.id}`, kind: 'local', name: l.seat, model: modelLabel(l.model), icon: l.icon,
      color: seatColor(l.seat, l.color), startMs: l.startMs, isStartKnown: true, fill: null, right: '', isLive: true,
      note: null,
    })
  }
  rows.sort((a, b) => a.startMs - b.startMs)
  if (snap !== null) {
    rows.unshift({
      key: 'main', kind: 'main', name: 'main', model: modelLabel(snap.model), icon: null,
      color: familyColor(snap.model, families), startMs: snap.startedAt, isStartKnown: true,
      fill: snap.ctxPct === null ? null : snap.ctxPct / 100, right: snap.ctxPct === null ? '' : percent(snap.ctxPct),
      isLive: true, note: null,
    })
  }
  const items = new Map<string, HistoryItem>()
  for (const r of doc?.recent ?? []) {
    const item = fromRecent(r, seatColor(r.seat ?? 'seat', null))
    items.set(item.key, item)
  }
  for (const f of finished) items.set(f.key, fromFinished(f))
  for (const l of laneState.lanes.filter(x => x.endMs !== null)) {
    const item = fromFinished(laneFinished(l))
    items.set(item.key, item)
  }
  const history = [...items.values()].sort((a, b) => b.endMs - a.endMs)
  const live = Math.max(0, rows.length - (snap === null ? 0 : 1))
  const figures = snap === null ? '' : [
    `◷ ${duration(nowMs - snap.startedAt)}`,
    snap.costUsd === null ? '' : cost(snap.costUsd),
    `runs ${history.length}`,
    `live ${live}`,
    rateLimits(snap.rateLimits),
  ].filter(p => p !== '').join('  ')
  return {
    nowMs, hasSnap: snap !== null, lanes: rows, figures, age: ageLine(nowMs, seats.okAt, seats.error),
    mainColor: snap === null ? null : familyColor(snap.model, families), chart, history,
  }
}

const NAME_COLS = 12
const MODEL_COLS = 14
const RIGHT_COLS = 4
const AGE_COLS = 4
const LABEL_COLS = 10

export function drawPane(els: Els, view: View, width: number, height: number) {
  const { Box, Text } = els
  const cols = Math.max(20, Math.floor(width))
  const room = split(height, cols, view.hasSnap ? view.lanes.length : 1)
  return (
    <Box flexDirection="column">
      {view.hasSnap ? view.lanes.slice(0, Math.max(1, room.lanes)).map(l => laneRow(els, l, view.nowMs, cols)) : (
        <Text dimColor>waiting for the first reading</Text>
      )}
      {view.figures !== '' && <Text>{cut(view.figures, cols)}</Text>}
      <Text dimColor>{cut(view.age, cols)}</Text>
      {room.charts > 0 && charts(els, view, cols)}
      {history(els, view, cols, Math.max(3, room.history))}
    </Box>
  )
}

function laneRow(els: Els, l: LaneRow, nowMs: number, cols: number) {
  const { Text } = els
  const hasModel = cols >= 60
  const fixed = 2 + 1 + NAME_COLS + 1 + (hasModel ? MODEL_COLS + 1 : 0) + 2 + 1 + RIGHT_COLS + 1 + AGE_COLS
  const inner = Math.max(4, cols - fixed)
  const at = l.isLive ? beadAt(nowMs, l.startMs, l.fill, inner) : 0
  const left = '─'.repeat(at)
  const right = '─'.repeat(Math.max(0, inner - at - 1))
  const color = l.color ?? undefined
  return (
    <Text dimColor={!l.isLive}>
      <Text color={color}>{glyph(l.kind, l.icon)}</Text> {pad(l.name, NAME_COLS)} {hasModel ? `${pad(l.model, MODEL_COLS)} ` : ''}
      ├{left}<Text color={color}>{l.isLive ? '●' : '○'}</Text>{right}┤ {pad(l.right, RIGHT_COLS)} {pad(l.isStartKnown ? duration(nowMs - l.startMs) : '?', AGE_COLS)}
    </Text>
  )
}

function charts(els: Els, view: View, cols: number) {
  const { Box, Text } = els
  const width = Math.max(1, cols - LABEL_COLS)
  const cells = columns(view.chart.samples, width)
  const rows = bars(cells.map(c => c.ctx), CONTEXT_BAR_ROWS)
  const counts = new Map<string, number>()
  for (const c of cells) for (const k of c.on) counts.set(k, (counts.get(k) ?? 0) + 1)
  const keys = [...counts.keys()]
    .sort((a, b) => (a === 'main' ? -1 : b === 'main' ? 1 : (counts.get(b) ?? 0) - (counts.get(a) ?? 0)))
    .slice(0, TIMELINE_ROWS)
  const mainColor = view.mainColor ?? undefined
  return (
    <Box flexDirection="column">
      <Text dimColor>context (main, 0-100%)</Text>
      {rows.map((r, i) => (
        <Text>
          <Text dimColor>{pad(i === 0 ? '100%' : i === rows.length - 1 ? '0%' : '', LABEL_COLS)}</Text>
          <Text color={mainColor}>{r}</Text>
        </Text>
      ))}
      <Text dimColor>agents at work</Text>
      {Array.from({ length: TIMELINE_ROWS }, (_, i) => {
        const k = keys[i]
        if (k === undefined) return <Text> </Text>
        const who = view.chart.names[k]
        return (
          <Text>
            <Text dimColor>{pad(who?.name ?? k, LABEL_COLS)}</Text>
            {runs(cells.map(c => c.on.includes(k))).map(r => (r.on
              ? <Text color={who?.color}>{'█'.repeat(r.n)}</Text>
              : <Text dimColor>{'·'.repeat(r.n)}</Text>))}
          </Text>
        )
      })}
    </Box>
  )
}

function runs(cells: readonly boolean[]): { on: boolean; n: number }[] {
  const out: { on: boolean; n: number }[] = []
  for (const on of cells) {
    const last = out[out.length - 1]
    if (last !== undefined && last.on === on) last.n += 1
    else out.push({ on, n: 1 })
  }
  return out
}

function history(els: Els, view: View, cols: number, rows: number) {
  const { Box, Text } = els
  const shown = view.history.slice(0, Math.max(0, Math.floor((rows - 1) / 2)))
  return (
    <Box flexDirection="column">
      <Text dimColor>{cut(HISTORY_TITLE, cols)}</Text>
      {view.history.length === 0 && <Text dimColor>no finished runs yet</Text>}
      {shown.map(item => (
        <Box flexDirection="column">
          <Text><Text color={item.color ?? undefined}>{cut(item.first, cols)}</Text></Text>
          <Text dimColor>{cut(`  ${item.second}`, cols)}</Text>
        </Box>
      ))}
    </Box>
  )
}
