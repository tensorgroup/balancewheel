# Live pane, run events, and the local layer — design

Date: 2026-11-27. Status: approved direction (the author chose approach 1 below; this doc
records the rest as the builder's starting point).

## Intent

A balancewheel user who works in Claude Code should see, beside the chat, what their agents
and seats are doing *now* and what they did this session: which subagents and seats run,
on which model, how full their context is, how long they took, what they worked on, and
what the session has spent. Every user gets this out of the box. A user who runs their own
seat wrappers or mods (their **local layer**) gets the same view, and balancewheel tracks
those local pieces as first-class, labeled "local", without any one person's setup in the
repo.

The author already runs a private pane like this. The point of this work is that any user
of balancewheel can do what the author does, with no customization for the common case.

## Goals

1. **Run events.** Every seat run writes a start row and an end row to bw's metrics log.
   `bw seat` and `bw panel` do it themselves. Any other runner (a local wrapper) does it
   with one call each: `bw run start` and `bw run end`.
2. **`bw live --json`.** One read-only command that answers "what is running now, and
   what ran recently" from the metrics log, open panel state, and the configured local
   sources. The pane, the cockpit, and any lead runtime can read it.
3. **The local layer.** A `local` section of the user's config declares their own seat
   wrappers and extra read-only metrics logs. bw reads them, marks what comes from them as
   `local`, and reports them in `bw registry` and `bw live`.
4. **The pane.** A Claude Code plugin in this repo, `claude/live-pane/`, that draws live
   lanes for the main agent, subagents, and seats; a context chart; an agents-at-work
   timeline; session figures; and a history of finished runs.
5. **Loads on every Claude start.** `bw setup claude-pane` installs the plugin into Claude
   Code, so plain `claude` loads it. `bw start` makes sure it is installed before it opens a
   Claude Code lead.

## Non-goals

- A pane for leads that are not Claude Code (Codex, pi, agy). `bw live --json` makes that
  possible later, in the cockpit page; this work does not build it.
- Tokens and cost for seats that do not report them.
- A native (Rust or other) helper. The pane runs inside Claude Code's own engine; `bw live`
  is one short Python call every few seconds. Nothing here needs a resident process.
- Changing how seats run, are contained, or are verified.

## Anti-goals (a failure even if it works)

- Any one person's paths, wrapper names, account details, or data in the repo (the privacy
  firewall in CLAUDE.md). The repo ships mechanisms and generic examples only.
- Model ids baked into interfaces, schemas, or names. Model names come from rows and config.
- A copy of a known game's look. The pane's look is original (lanes, below).
- A pane that slows Claude Code, writes to the user's files, or breaks a Bash call it
  watches. The pane only reads; a failure in it must leave the tool call untouched.
- A second appender to the metrics log. `metrics/store.py` stays the only appender.

## Constraints

- Python 3.9+, standard library only (`bw`). The plugin is TypeScript/TSX on Claude Code's
  plugin API, with no npm dependencies at run time.
- The metrics log is a contract: new row types are additive; existing `usage` rows keep
  their fields. `CHANGELOG.md [Unreleased]` records the new rows and config keys.
- Config is strict (`metrics/config.py` `TOP_KEYS`): the `local` key is added there, with
  validation and a test.
- Docs that must move in the same commit as behavior: `docs/leads.md` (the `bw` reference),
  `docs/usage-map/data.generated.js` (regenerate for new commands/modules), `CHANGELOG.md`,
  and `setup/paths.txt` if a new installed path appears.
- Tests: `tests/test_<area>.py` with synthetic fixtures only. The plugin ships its own
  `*.test.tsx` for `claude plugin test`; CI runs the Python suite (the plugin tests run
  where the `claude` binary exists, and are skipped with a clear line where it does not).

## Approaches considered

1. **Run-event contract (chosen).** Runners write `run` start/end rows through bw; bw and
   the pane read one log. Works for foreground and background runs, for every lead, with no
   name lists. Cost: a local wrapper needs one line at start and one at end — or it can be
   tracked by approach 2 meanwhile.
2. **Command patterns in config (kept as the fallback).** The pane watches Bash calls in
   Claude Code and matches the user's configured patterns. Needs no wrapper edits, but sees
   only Bash calls from Claude leads, and not `bw panel` seats (they run from bw's service).
3. **Panel state files only.** No wrapper changes, but sees only `bw panel` runs.

The author picked 1, with 2 as the fallback for wrappers nobody can edit. Data reaches the
pane through `bw live --json` (one source of truth in Python, shared with the cockpit),
not by parsing bw's files in TypeScript.

## Design

### 1. The `run` row

Appended through `metrics/store.py` `append()` (which adds `ts`):

```json
{"type": "run", "phase": "start", "run": "r<12 hex>", "seat": "<seat name>",
 "model": "<as configured>", "dir": "<abs path>", "label": "<few words>",
 "source": "bw" | "local", "panel": "<panel id, optional>"}
{"type": "run", "phase": "end", "run": "r<12 hex>", "ok": true | false,
 "reason": "<short, optional: timeout | usage-cap | error | ...>", "duration_s": 12}
```

- `bw seat` (`seats/run.py`): write `start` just before the child runs (after containment,
  ~`execute()` line 284) and `end` in `finish()` beside the existing `usage` row, which
  gains an optional `run` field so the two join. Refusals never write a start.
- `bw panel` seats get theirs through `bw seat`; the row carries `panel`.
- A start with no end older than `local.run_max_s` (default 2700 s) reads as `stale` in
  `bw live`; nothing is rewritten.

### 2. `bw run start` / `bw run end`

```text
bw run start --seat NAME [--model M] [--dir D] [--label TEXT]   # prints the run id
bw run end RUN_ID (--ok | --fail [--reason R])  [--duration S]
```

`source` is `local` for these. A wrapper writes, for example:

```sh
run=$(bw run start --seat my-seat --model "$MODEL" --label "$1" 2>/dev/null || true)
...                                   # the wrapper's own work
[ -n "$run" ] && bw run end "$run" --ok
```

Both commands exit 0 without writing when the config cannot be read, so a wrapper never
fails because of tracking.

### 3. The `local` config section

```json
"local": {
  "seats": [
    {"name": "my-seat", "model": "<model label>", "match": "my-seat\\.sh",
     "metric": "my-seat", "color": "#2dd4bf"}
  ],
  "logs": ["~/path/to/another/metrics.jsonl"],
  "run_max_s": 2700
}
```

- `seats[].match` is a regular expression for the pane's Bash fallback (approach 2).
  `metric` is the seat name the wrapper's own `usage` rows use, so `bw live` can close a
  fallback run from those rows. `color` is optional (the pane hashes the name otherwise).
- `logs` are extra JSONL files bw reads, never writes, for `usage` and `run` rows. Rows
  from them are `source: local`. A missing or unreadable file is reported, not fatal.
- `bw registry` gains `local-seat:<name>` and `local-log:<path-hash>` nodes with
  `origin: "local"` and a health (`seen` when a row from it arrived in the last 7 days,
  else `unknown`).
- Validation lives in `metrics/config.py` with the rest; unknown keys are rejected.

### 4. `bw live --json`

Read-only; no lock beyond what reads already take. Output (version `v: 1`):

```json
{"v": 1, "now": 1764280000.0,
 "open":   [{"run": "...", "seat": "...", "model": "...", "label": "...", "dir": "...",
             "start": 1764279990.0, "source": "bw", "panel": "p0123abc", "stale": false}],
 "recent": [{"run": "...", "seat": "...", "model": "...", "label": "...", "start": 0,
             "end": 0, "ok": true, "reason": null, "source": "local"}],
 "local":  {"seats": [{"name": "...", "match": "...", "metric": "...", "model": "...",
                       "color": "..."}],
            "logs": [{"path": "...", "ok": true}]},
 "panels": [{"panel": "p0123abc", "state": "running", "seats": {"astra": "running"}}]}
```

- `recent` covers the last 24 h, or `--since EPOCH`, newest first, at most `--limit N`
  (default 50). Fallback runs closed from `usage` rows appear here with `run: null`.
- Reads the tail of each log (`metrics/store.py` gains a tail reader that takes the last
  N bytes and skips a cut first line), so the call stays fast on a large log.
- `--dir PATH` limits to runs in that worktree. The pane passes its session's directory.
- Reuses `metrics/summary.py` and `cockpit/views.py` helpers where they fit; the cockpit
  may show `open` later (not in this work).

### 5. The pane (`claude/live-pane/`)

A Claude Code plugin (`.claude-plugin/plugin.json`, `hooks/hooks.json`,
`hooks/register.tsx`, `types/index.d.ts`, tests). It starts from the author's working pane
(layout, charts, history, state handling) and drops its game look and its private seat
table.

- **Data.** Each second, in process: `$.agent.list()`, `$.session.usage()`,
  `$.session.model()` (subagents, context, cost, rate limits). Every 5 s while the pane is
  open: `bw live --json --dir <cwd>` through `$.process.spawn` (seats, panels, local runs,
  local seat config). The path to `bw` comes from the plugin's `userConfig` (`bwPath`,
  default `bw` on PATH). If `bw` is missing, the pane still draws Claude's own agents and
  says once that seats need `bw`.
- **Fallback.** A `tool.call` hook on Bash matches `local.seats[].match` from the last
  `bw live` answer. A match opens a lane at once; the lane closes when the Bash call ends
  (foreground) or when `bw live` shows the matching `usage` row (background). The hook
  always calls `next(e)` once and returns its result unchanged; its own failures are caught.
- **Look (original).** One horizontal *lane* per running agent or seat, main first:

  ```text
  ᐅ main        Opus 5.5·med   ├────────●──────────────┤ 23%   38m
  ◇ plan review astra          ├──●────────────────────┤  –     2m
  ○ Explore     Sonnet 5.5·med ├─────────────●─────────┤ 41%   12s
  ```

  The bead moves along its lane; its speed is the context fill (a seat with no figure moves
  at a steady middle pace). Glyph by kind (main, subagent, bw seat, local seat), color by
  model family for Claude models and by seat for seats. Below the lanes: the session
  figures (time, cost marked `≈`, runs, live, rate limits), the context chart on a fixed
  0–100% scale, the agents-at-work timeline (one colored block per agent or seat per time
  column), and the history (two lines each: name, model, run time, peak context, cost or
  outcome; then tokens or `local seat`/`bw seat`, and a few words of what it did).
- **Room.** Lanes take what they need; the charts take 11 rows when the pane has room; the
  history takes the rest. A narrow pane drops the charts first.
- **State.** `$.state` atoms with shape tags (as the author's pane does), so a reload keeps
  history; the session's history is not stored across sessions.
- **Tests.** `claude plugin test` cases: draws with no data; draws with samples and
  finished runs; a Bash call that matches a local seat opens and closes a lane; a call that
  does not match changes nothing; `bw live` missing still draws.

### 6. Install on every Claude start

- `bw setup claude-pane`: registers this checkout's plugin with Claude Code
  (`claude plugin marketplace add <checkout>` with a `.claude-plugin/marketplace.json` at
  the repo root listing `claude/live-pane`, then `claude plugin install
  live-pane@balancewheel`). Idempotent; prints what it did. `--remove` undoes it.
- `bw start` with a Claude Code lead: checks that the plugin is installed (one `claude
  plugin list` read, cached for the day in the state dir) and runs the same install when it
  is not, saying so in one line. A user can turn this off with `"claude_pane": false` in the
  lead's config.
- `setup/paths.txt` gains the plugin's install record path if Claude Code keeps one under
  `~/.claude/plugins/`.

### 7. OpenEscapement

Docs only in this work: a short "local layer" paragraph in esc's README beside its "+N
lines local" report, saying that a `model-seats` pack names seats by role, and that a
user's own seats are their local layer, which balancewheel tracks (`bw live`, `bw
registry`). No esc code changes.

## Left to the builder

- Exact glyphs and colors (original, readable on dark and light terminals).
- How `bw live` merges panel seats that have no `run` rows yet (older bw) — show them from
  panel state with `start` unknown.
- The tail size for logs (start at 256 KB; widen when `--since` asks for more).
- Whether `claude plugin` CLI calls need a timeout (they should; 20 s).

## Builder's notes from the conversation

- The author's private pane (outside this repo) proved the layout, the charts, the
  history, the `h` JSX trap (never name a variable `h` in a TSX hook), shape-tagged atoms,
  and that `claude plugin test` must run the real binary, not a shell alias that adds flags.
- The author's own wrappers become their local layer: `local.seats` entries with `match`
  and `metric`, plus `local.logs` for their existing metrics file. That setup lives in the
  author's config, not here.
