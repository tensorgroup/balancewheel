# live-pane

*Written 2026-11-27. Tested on Claude Code 2.1.332.*

A Claude Code plugin that shows, beside the chat, what your agents and balancewheel seats do now and what
they did in this session. It only reads. It is part of balancewheel and loads from bw's own checkout.

## What it shows

```text
◆  main         Opus 5.5       ├───────────●──────────────────┤ 23%  38m
◇  Explore      Sonnet 5.5·med ├──────────────────●───────────┤ 25%  12s
▣  seat-a       model-c        ├───────────────────────●──────┤ 41k   2m
🦉 my-seat      model label    ├──────────────────────────●───┤ 45%   2m
◷ 38m  ≈$1.24  runs 2  live 3  5h 23%
seat data: 4s old
```

- **Lanes.** One lane for each agent or seat that runs now, with the main session first. The bead swings
  from end to end of its lane. It moves slower as the context fills
  (slower = fuller context). A seat with no known window moves at a steady middle pace. The right columns show the context fill (or the tokens, when no window is known)
  and the run time. A seat lane from seat data older than 15 s stops moving and says `old`.
- **Marks.** `◆` main, `◇` subagent, `▣` bw seat, `▢` local seat. A seat's `icon` replaces its mark; an
  emoji takes two columns.
- **Colors.** A Claude model takes the color of a family word in its model string (opus, sonnet, haiku,
  fable). `local.families` in the bw config adds or replaces words. A seat takes its `color`, or a color
  that comes from its name. The colors are mid tones, so that they read on dark and light terminals.
- **Figures.** The session's run time, its cost (an estimate, marked `≈`), the finished runs, the live
  lanes, and the rate-limit windows. The second line gives the age of the seat data and the reason when
  `bw live` failed.
- **Context chart.** The main session's context fill on a fixed 0-100% scale, with the whole session folded
  into the width.
- **Agents at work.** One colored block for each agent or seat in each time column.
- **History.** "this worktree, since this session started": two lines for each finished run, newest first.
  Line 1: name, model, run time, peak context, cost (`≈`) or `plan`. Line 2: subagent, `bw seat`, or
  `local seat` with its outcome, the tokens, and a few words of what it did.

The lanes take the rows that they need. The two charts take 11 rows when there is room. The history takes
the rest. A pane narrower than 40 columns drops the charts first. A pane narrower than 60 columns drops the
model column.

## Where the data comes from

- **Each second:** `$.agent.list()`, `$.session.usage()`, and `$.session.model()` (Claude Code's own
  subagents, context, cost, and rate limits).
- **Every 5 s while the pane is open:** `bw live --json --dir <session folder> --since <session start>`
  (document `v: 1`). One call at a time, with a 10 s limit. The call stops when the pane closes or the
  session ends. A document with another `v` is ignored. When `bw` is missing or fails, the pane still
  draws Claude's own agents and shows the age of its seat data.
- **Bash fallback, for local seats:** the pane hooks no tool call, because a hook that may match a tool
  turns off that tool's move to the background (ctrl+b). It observes the rows that the conversation
  stores (`session.append`) and keeps each row unchanged. When the first 4 KB of a Bash call's command
  holds a `local.seats[].match` (a plain substring, never a pattern), a lane opens at once. The call's
  result row closes a foreground lane (`failed` when the result is an error). A `run_in_background` lane
  closes when `bw live` lists a `usage` row of the seat's `metric` at or after the lane's start (less
  1 s), or as `end unknown` after `local.run_max_s`. A lane is dropped when `bw run` also wraps the
  call: a `bw run` start of the same seat (the `--seat` that the command shows, else the lane's seat) at
  or after the lane's start, less 2 s. The call may wait at a permission prompt for any time first.
  One run drops one lane. A `bw run` of that seat that starts in the same worktree outside the session
  can drop a lane that is not its own.
- **The `usage` row of a local wrapper.** A wrapper that does not run under `bw run` closes its
  background lane with one row per run, appended to a log in `local.logs` (or to the metrics log):

  ```json
  {"type": "usage", "seat": "my-seat", "ok": true, "t": 1800000000.5, "dir": "/abs/path/to/worktree"}
  ```

  `seat` is the seat's `metric` (default: its `name`). `t` is epoch seconds (a float is fine); or give
  `ts` in exactly `YYYY-MM-DDTHH:MM:SSZ` (UTC). A row with neither has no time and does not count. `dir`
  is the worktree root (`git rev-parse --show-toplevel`) or an absolute path inside it; bw compares real
  paths. A relative or `~` path, a path outside the worktree, and a path inside a nested repo or worktree
  do not count. `ok` gives `ok` or `failed`; a missing `ok` gives `end unknown`. Leave out `run_id`: a row
  with one belongs to a `bw seat` run.
- **Text.** Every text that the pane draws loses terminal control characters first: escape sequences
  (CSI, OSC such as links and window titles, and the other string sequences) go whole, and other C0 and C1
  characters become spaces.
- **Known limit:** a foreground call that the person moves to the background closes its lane at the
  move, because its result row comes then and does not say that the call goes on. Wrap the seat with
  `bw run` to track such a call.
- **After `/clear` or a resume** the timer and the polls go on, and the lanes, the history, the chart, and
  the session start begin again.

State lives in `$.state` with shape tags, so a reload keeps the history. Nothing is kept from one session to
the next.

## Install

Run `bw setup claude-pane` from bw's own checkout. It registers the `balancewheel` marketplace (the
`.claude-plugin/marketplace.json` at the repo root) and installs `live-pane@balancewheel`. A lead that bw
starts on the `claude-code` runtime checks the plugin each time. Every lead launch, on any runtime, checks
that the checkout is still outside the lead's reach, and uninstalls the pane when it is not. The plugin's `live-pane` slash command
opens the pane.

The plugin option `bwPath` (default `bw`) names the `bw` command. Set it when `bw` is not on the `PATH`
that Claude Code sees. It must be an absolute path (or the default `bw`): a relative path would be found
from the session's folder, which a lead can write. The pane never runs a relative `bwPath`, and its age line
says `bwPath must be an absolute path or bw; not run`.

## The version rule

`version` in `.claude-plugin/plugin.json` and in the marketplace entry is equal to `VERSION` in
`lead/version.py` (a test checks this). Bump the prerelease number in all three in the same commit as any
change under `claude/live-pane/`. The bump changes the version that bw compares (bw runs `claude plugin
update` when the installed version differs) and the version that Claude Code shows. It does not control
which files load: Claude Code reads this folder in place from bw's checkout at each session start, with or
without a bump. So the checkout must stay outside every lead's reach: `bw setup claude-pane` refuses, and
every `bw lead` launch uninstalls the pane, when a lead could change the checkout.

## Tests

```sh
command claude plugin validate claude/live-pane
command claude plugin test claude/live-pane
BW_PLUGIN_TESTS=1 python3 -m unittest tests.test_live_pane_plugin -v
```

Use the real `claude` binary (`command claude`). A shell alias that adds flags before `plugin` stops
the command. The default Python suite skips the plugin's own tests, because the real binary reads your
Claude config. `hooks/fixture.ts` answers the engine's calls with synthetic data.

## Files

- `hooks/register.tsx`: the entry: the pane, the row observer, the one-second timer, and the poll.
- `hooks/poll.tsx`: the `bw live` call and its strict reader.
- `hooks/fallback.ts`, `hooks/layout.ts`, `hooks/format.ts`: pure logic, with no `$`.
- `hooks/view.tsx`: the drawing.
- `types/index.d.ts`: the `bw live` v1 contract and the pane's state.
