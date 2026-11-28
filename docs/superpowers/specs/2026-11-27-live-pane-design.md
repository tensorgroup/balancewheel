# Live pane, run events, and the local layer — design

Date: 2026-11-27 (synced with the plan panel the same day: direct append first, the `beat`
phase, `run_max_s` in `bw live`'s `local`). Status: approved direction (approach 1 below), revised after a design
panel (astra, opus, kimi, flash) and a builder check. Clean-room: this design and the code
built from it are written from public requirements only (see Constraints).

## Intent

A balancewheel user who works in Claude Code should see, beside the chat, what their agents
and seats are doing *now* and what they did this session: which subagents and seats run, on
which model, how full their context is, what they cost where that is known, how long they
took, and what they worked on; and what the session has spent. Every user gets this out of
the box. A user who runs their own seat wrappers or mods (their **local layer**) gets the
same view, and balancewheel tracks those local pieces as first-class, labeled `local`,
without any one person's setup in the repo.

## Goals

1. **Run events.** Every seat run writes a start row and exactly one end row to bw's
   metrics log. `bw seat` and `bw panel` do it themselves. A local wrapper gets the same with
   no code: `bw run -- <command>` wraps it.
2. **`bw live --json`.** One read-only command that answers "what runs now, and what ran in
   this window", with live context and cost for seats where the seat's CLI records them.
3. **The local layer.** A `local` config section declares a user's own seats and extra
   read-only logs; bw marks what comes from them `local` in `bw live` and `bw registry`.
4. **The pane.** A Claude Code plugin in this repo, `claude/live-pane/`, with an original
   look: one lane per running agent or seat, session figures, a context chart, an
   agents-at-work timeline, and a history of finished runs.
5. **Loads on every Claude start.** `bw setup claude-pane` installs it for plain `claude`;
   bw makes sure it is installed and current at the point where it launches a Claude Code
   lead.

## Non-goals

- A pane for leads that are not Claude Code. `bw live --json` makes one possible later in
  the cockpit page.
- Cost for seats on a subscription plan (their CLIs record none).
- A resident helper process or a native (Rust) helper. The pane runs in Claude Code's own
  engine and calls `bw live` every 5 s; nothing needs to stay running.
- Changing how seats run, are contained, or are verified.

## Anti-goals (a failure even if it works)

- Any one person's paths, wrapper names, account details, or data in the repo; any code,
  text, or assets taken from the author's private tooling (clean-room rule, CLAUDE.md).
- Model ids in interfaces, schemas, or names (model names come from rows and config; the
  pane colors Claude models by *family words* found in the model string, with a config
  override, never by id).
- A copy of a known game's look.
- A pane that slows Claude Code, writes to the user's files, or changes a Bash call it
  watches. The pane only reads; its failures leave tool calls untouched.
- A second appender to the metrics log; a lead that can write bw's state; a plugin installed
  from a folder a lead can edit.
- A lane that shows a run as live after it ended.

## Constraints

- **Clean-room.** The builder writes the plugin and the Python from this document and from
  public Claude Code plugin documentation. The builder does not open, copy, or paraphrase
  the author's private pane or wrappers. Facts about Claude Code's public plugin API are
  public and may be used.
- Python 3.9+, standard library only. The plugin is TSX on Claude Code's plugin API with no
  run-time npm dependencies; record the Claude Code version it was tested against in
  `docs/stack.md` and the plugin README.
- The metrics log is a contract: new rows and fields are additive; existing rows keep their
  fields. `metrics/store.py` stays the only appender.
- Config is strict (`metrics/config.py`): every new key is validated and tested.
- Registry health stays in {verified, pending, capped, unknown} (tests enforce it).
- `bw start --dry-run` writes nothing and calls no tool beyond what it calls today.
- Docs that move with behavior, in the same commits: `docs/leads.md`, `CHANGELOG.md
  [Unreleased]`, `docs/usage-map/data.generated.js` and `infra.js`, `docs/stack.md`, and the
  `docs/start/` guide wherever its shown output changes. `setup/paths.txt` gains nothing.
- Tests: `tests/test_<area>.py`, synthetic fixtures only. The plugin ships `*.test.tsx` for
  `claude plugin test`; a Python test runs them (and `claude plugin validate`) when
  `BW_PLUGIN_TESTS=1` is set and a `claude` binary is on PATH, and skips with a clear message
  otherwise. The default suite stays hermetic: the real binary reads the user's Claude config
  and writes `.claude-plugin/types/` into the tree.

## Approaches considered

1. **Run-event contract (chosen).** Runners write `run` start/end rows through bw; bw and
   the pane read one log. Works for foreground and background runs and every lead, with no
   name lists. A local wrapper is tracked by running it through `bw run --`.
2. **Command matching (kept as a fallback).** The pane watches Bash calls in Claude Code for
   a configured substring. Needs no change to how a wrapper is called, but sees only Bash
   calls from Claude leads, never `bw panel` seats.
3. **Panel state files only.** Sees only `bw panel` runs.

## Design

### 1. The `run` row and its lifecycle

Appended through `store.append()` (which adds `ts`, second precision):

```json
{"type": "run", "phase": "start", "run_id": "r<12 hex>", "t": 1764280000.123,
 "seat": "<seat>", "model": "<as configured>", "dir": "<worktree root, realpath>",
 "label": "<few words>", "source": "bw" | "local", "panel": "<panel id>",
 "session_id": "<lead session, when known>", "pid": 4242, "host": "<hostname>",
 "stand_in_for": "<seat, when a stand-in>"}
{"type": "run", "phase": "end", "run_id": "r<12 hex>", "t": 1764280012.5,
 "seat": "<seat>", "dir": "<worktree root>", "ok": true, "reason": null,
 "duration_s": 12.4, "tokens_in": 0, "tokens_out": 0, "cost_usd": null, "ctx_peak": 0}
```

- **Heartbeat.** While its child runs, `bw run --` also writes
  `{"type": "run", "phase": "beat", "run_id": "...", "t": 0.0, "seat": "...", "dir": "..."}` every
  `local.run_max_s / 2`. A long run then always has a row inside `bw live`'s read window, and the
  `run_max_s` rule below counts from the newest beat. The `start`/`end` form writes no beats.
- `t` is a float epoch (the row's own high-resolution time; `ts` stays as `append` writes
  it). The end row repeats `seat` and `dir`, so an end whose start is out of reach still
  shows.
- **`bw seat` (`seats/run.py`).** The start row is written just before the child starts
  (in `execute()`, after containment, before `self.run(...)`). Everything after it runs in
  one `try/finally` that writes exactly one end row on every path: a normal finish (beside
  the `usage` row, which gains an optional `run_id`), a refusal found after the child ran
  (`parse()` → `refuse()`: end with `reason: "refused"`), a timeout, an interrupt, and any
  exception (`reason: "error"`). A failure to append the start row is logged to stderr and
  the seat still runs.
- **Stand-ins.** A stand-in run is its own run with its own `run_id` and `stand_in_for`.
- **`bw panel`.** `seat_argv()` passes an internal `--panel ID` (and the panel's
  `session_id`) to `bw seat`, for round one and for asks; the rows carry them.
- **Dead runs.** A start with no end is shown as live only while its `pid` is alive on the
  same `host` (`os.kill(pid, 0)`); otherwise it is `ended: unknown`. When `host` differs or
  `pid` is absent (older rows), it is live until `local.run_max_s` (default 2700 s) after its
  newest start or beat row, then `stale`. For panel seats, panel state that shows the seat done also closes it.

### 2. `bw run`

```text
bw run --seat NAME [--model M] [--label TEXT] [--dir D] -- COMMAND [ARGS...]
bw run start --seat NAME [--model M] [--label TEXT] [--dir D]     # stdout: the run id only
bw run end RUN_ID (--ok | --fail [--reason R])
```

- The wrap form is the default for local wrappers: it writes start, runs the command with
  stdin, stdout, and stderr passed through, forwards SIGINT/SIGTERM to it, writes the end row
  from the exit status (`--fail` with `reason: "exit <n>"` or `"signal <n>"`), and exits
  with the command's own status. If tracking fails in any way (no config, no write access),
  it says so once on stderr and still runs the command.
- `start`/`end` are for wrappers that cannot be wrapped. `start` prints only the id;
  everything else goes to stderr. Both exit 0 when tracking fails. `end` refuses to close a
  run whose start row says `source: bw`.
- `source` is always `local` for `bw run`.
- **Inside a contained lead**, bw's state folder is not writable (by design). `bw run` always
  tries a direct append to the log first (`BW_CHECKPOINT` is set for every lead bw starts, contained
  or not). Only when that append fails and `BW_CHECKPOINT` is set does it write a small request file
  to `<checkpoint dir>/runs/` instead. `bw run end` finds the start in the log or in those files, and
  an end request may omit `seat` and `dir` (the service fills them from the start); the
  panel service, which already watches checkpoint folders, validates each (bounded fields,
  `source` forced to `local`, `dir` forced to that worktree) and appends it. With no service
  running, `bw live` reads pending request files read-only, so the lane still shows.
- `bw run` and `bw live` go in `COMMANDS` and the dispatch map in `bin/bw`, and handle a
  config error themselves instead of exiting first.

### 3. The `local` config section and `claude_pane`

```json
"claude_pane": true,
"local": {
  "seats": [
    {"name": "my-seat", "model": "<model label>", "match": "my-seat.sh",
     "metric": "my-seat", "icon": "🦉", "color": "#2dd4bf"}
  ],
  "logs": ["~/path/to/another/metrics.jsonl"],
  "run_max_s": 2700,
  "families": {"<family word>": "#rrggbb"}
}
```

- `claude_pane` (top level, default true) turns the automatic plugin install off.
- `seats[].match` is a **plain substring** of the Bash command (no regular expressions: one
  meaning in Python and TypeScript, and no slow patterns in a tool hook). The pane checks at
  most the first 4 KB of a command. `metric` is the seat name the wrapper's own `usage` rows
  use. `icon` and `color` are optional.
- `logs` are extra JSONL files bw reads and never writes, for `usage` and `run` rows; rows
  from them are `local`. `improve.panel_logs` already names extra logs: it stays valid, and
  both lists are merged and de-duplicated by real path.
- `families` maps a word found in a model string to a color (built-in defaults cover the
  Claude family words; a user may add others).
- In `bw registry`, local seats join the existing `seats` collection with `origin: "local"`
  and a `last_seen` time; their health stays in the existing set. The cockpit passes
  `origin` through and shows a small "local" mark. Logs are listed in `bw live`, not the
  registry.

### 4. `bw live --json`

Read-only. Options: `--dir PATH` (resolved to its worktree root, as `resolve_project`
does), `--since EPOCH` (default: 24 h ago), `--limit N` (default 50). Output `v: 1`:

```json
{"v": 1, "now": 1764280000.0, "complete": true,
 "open":   [{"run_id": "...", "seat": "...", "model": "...", "label": "...", "dir": "...",
             "start": 1764279990.1, "source": "bw", "panel": "p0123abc", "ask": null,
             "state": "live" | "stale" | "unknown", "icon": "...", "color": "...",
             "ctx": 113000, "window": 258000, "cost_usd": null}],
 "recent": [{"run_id": "...", "seat": "...", "model": "...", "label": "...", "start": 0.0,
             "end": 0.0, "ok": true, "reason": null, "source": "local", "stand_in_for": null,
             "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.02, "ctx_peak": 113000}],
 "usage":  [{"seat": "...", "t": 0.0, "dir": "...", "ok": true, "source": "local"}],
 "local":  {"seats": [{"name": "...", "match": "...", "metric": "...", "model": "...",
                       "icon": "...", "color": "..."}],
            "logs": [{"path": "...", "ok": true, "error": null}],
            "families": {"...": "#rrggbb"}, "run_max_s": 2700},
 "panels": [{"panel": "p0123abc", "state": "running", "seats": {"<seat>": "running"},
             "asks": [{"id": "...", "seat": "...", "state": "running"}]}]}
```

- **Reading.** Each log is read backward in chunks until the read covers
  `min(since, now - run_max_s)`. A live `bw run` writes a beat row every `run_max_s / 2`, and a
  `bw seat` run is shorter than its cap, so every live run has a row in that window and no full
  scan is needed (the read also covers the panel's deep cap). A cut line at either end of a chunk is
  skipped. A per-read cap (64 MB) sets `complete: false` when it is hit. A missing or
  unreadable log is reported in `local.logs[].error`, never fatal.
- **Open runs** come from start rows without an end, plus panel seats and asks in a running
  state (read-only from bw's own panel files; never the mutating watchdog), plus pending
  request files from contained leads.
- **Live usage.** For an open `bw` run, the seat's adapter reads that run's session file in
  the seat CLI's own folder (`seats/live.py`, not hashed into the seat key, gives
  `live_usage(runtime, home, session_id, dir, start, seat_cfg=None)` per runtime: Codex `token_count` events; Claude Code assistant `usage`; pi message `usage`
  with the provider's cost; Kimi `token_counting.measured`; agy: none known). Best effort:
  a missing file or changed format gives nulls. Local seats get the same when their entry
  names a `runtime`.
- `usage` lists `usage` rows with no `run_id` in the window (the fallback's close signal,
  section 5). `recent` joins end rows with their `usage` row for tokens and cost.
- Session scope: the pane passes `--since <its session start>` and `--dir`. Runs in the
  same worktree from another session in that time window do show; the pane's history title
  says "this worktree, since this session started".

### 5. The pane (`claude/live-pane/`)

A Claude Code plugin (`.claude-plugin/plugin.json` with `version` equal to bw's version,
`hooks/hooks.json`, `hooks/register.tsx`, `types/index.d.ts`, tests), written clean-room.

- **Data.** Each second, in process: `$.agent.list()`, `$.session.usage()`,
  `$.session.model()` — subagents, context, cost, rate limits. Every 5 s while the pane is
  open: `bw live --json --dir <cwd> --since <session start>` through `$.process.spawn`, with
  at most one call in flight, a 10 s timeout, cancel on pane close, and a check of `v`. The
  path to `bw` is the plugin option `bwPath` (default `bw`). When `bw` is missing or fails,
  the pane still draws Claude's own agents and shows how old its seat data is.
- **Fallback.** The pane observes the rows the conversation stores (a `session.append`
  hook), never the tool call: a hook that may match a tool turns off that tool's move to
  the background. An assistant row's Bash `tool_use` block whose `input.command` holds a
  `local.seats[].match` (from the last `bw live`) as a substring of its first 4 KB opens a
  lane at once, keyed by the block's id, with a millisecond start time. The user row's
  `tool_result` for that id closes a foreground lane (`is_error` gives `failed`). A
  `run_in_background` call keeps its lane open past its result. A background
  lane pairs with the oldest unpaired `usage` row for the same `metric` and worktree whose
  time is at or after the lane's start, less 1 s (the log's precision). If two lanes could
  take the same row, the older lane takes it. A lane that pairs with nothing is closed as
  `end unknown` after `run_max_s`. A lane is dropped when a `run` start for the same seat
  appears within 2 s of it (the wrapper is also wrapped by `bw run`). The hook always calls
  `next(e)` once and returns the stored row unchanged; its own errors are caught.
- **`/clear` and resume.** `session.end` with reason `clear` or `resume` is followed by no
  `session.start`: the timer and the polls go on, and the session's state (lanes, history,
  chart samples, session start) starts over. Any other reason stops them.
- **Look (original).** One horizontal lane per running agent or seat, main first:

  ```text
  ᐅ main         Opus 5.5·med   ├────────●──────────────┤ 23%   38m
  🦉 my-seat      model label    ├──●────────────────────┤ 41k    2m
  ○ Explore      Sonnet 5.5·med ├─────────────●─────────┤ 41%   12s
  ```

  The bead moves along its lane; its speed grows with context fill (a seat with no known
  window moves at a steady middle pace). Glyph by kind (main, subagent, bw seat, local seat;
  a seat's `icon` when set, two columns wide for an emoji); color by family word for Claude
  models and by seat for seats. Below the lanes: session figures (time, cost marked `≈` as an
  estimate, runs, live, rate limits), the context chart on a fixed 0–100% scale, the
  agents-at-work timeline (one colored block per agent or seat per time column), and the
  history (two lines each: name, model, run time, peak context, cost or plan; then tokens or
  `bw seat`/`local seat` with its outcome, and a few words of what it did).
- **Room.** Lanes take what they need; the charts take 11 rows when there is room; history
  takes the rest; a narrow pane drops the charts first.
- **State.** `$.state` atoms with shape tags, so a reload keeps history; nothing is stored
  across sessions.
- **Tests (`claude plugin test`).** Draws with no data; draws with samples and finished
  runs; a Bash call matching a local seat opens and closes a lane; a non-matching call
  changes nothing; `bw` missing still draws; a slow `bw live` never runs twice at once.

### 6. Install on every Claude start

- **Source.** Only bw's own checkout (`ROOT`, the one CLAUDE.md says no lead edits). Setup
  refuses when `ROOT` is inside any configured project worktree. This is recorded in the
  containment notes: the plugin runs unsandboxed in every Claude session, so its source must
  be outside every lead's write grant.
- **`bw setup claude-pane`.** Registers a marketplace named `balancewheel` at `ROOT` (a
  `.claude-plugin/marketplace.json` at the repo root lists `claude/live-pane`) and installs
  `live-pane@balancewheel`. It checks `claude --version` against the minimum recorded in
  `docs/stack.md` and says so when mods cannot load. Idempotent; each step bounded by a
  20 s timeout; prints what it did. `--remove` removes only the marketplace and plugin it
  added. `bw setup`'s parser gains the `claude-pane` choice.
- **At launch.** In `lead/runner.py`, where the resolved lead's argv is built for the
  `claude-code` runtime (after rotation has chosen), bw ensures the plugin: if
  `claude_pane` is not false and the plugin is not installed, it installs it; if the
  installed version differs from bw's, it runs `claude plugin update`; if the user disabled
  it, it does nothing. Nonfatal and bounded (5 s for the check); one line when it acts.
  `--dry-run` prints "would ensure the live pane plugin" and calls nothing.

### 7. OpenEscapement

A separate, docs-only change in the esc repo (its own branch): a "local layer" paragraph
beside esc's "+N lines local" report, saying that a `model-seats` pack names seats by role,
and that a user's own seats and wrappers are their local layer, which balancewheel tracks
(`bw run`, `bw live`, `bw registry`).

## Left to the builder

- Exact glyphs and colors (original, readable on dark and light terminals); the default
  icons per runtime.
- The chunk size for backward reads (start at 256 KB).
- The minimum Claude Code version (the one the plugin is tested on).
- Message wording.

## Notes for the builder (public facts about Claude Code's plugin API)

- In a TSX hook module, JSX compiles to calls to `h`: never name a variable `h`.
- `$` may cross into another file only as the argument of a plain `function` declaration,
  and each use is spelled `$.noun.method(...)`; `claude plugin test` catches violations that
  `tsc` does not.
- `$.state` values read through `atom(ref, initial, { shape })` come back as `initial`
  unless stored under the same shape tag; tests that seed state must store the tagged form.
- Run `claude plugin test` with the real binary; a shell alias that adds flags before
  `plugin` stops it from running.
