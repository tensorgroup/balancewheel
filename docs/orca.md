# Balancewheel and Orca

*Written 2026-10-25 against Orca 1.4.240 (macOS, Homebrew cask `stablyai/orca/orca`), read
from its docs, its source (main at `a4e6e1f0`), and a hands-on spike on the author's machine.
A deep four-seat design review of the adoption followed (43 raw findings, 17 after dedupe,
none refuted); its conclusions are folded in below. Orca ships near-daily, so re-check the
version-specific facts here before relying on them.*

[Orca](https://github.com/stablyai/orca) is an MIT-licensed desktop "agent IDE": it runs
Claude Code, Codex, agy, pi, Kimi Code and other CLI agents side by side, each in its own
git worktree and terminal, with diffs, a browser, notifications, a mobile companion, and a
scriptable `orca` CLI. Balancewheel recommends it as the **default canvas** — the window you
watch your leads in — and never requires it: everything here works from plain terminal
tabs.

## What each one is for

Orca answers *where do my agents run and how do I watch them*. Balancewheel answers *which
model should do this step, can I trust what it said, and what did that cost*. They overlap
only at the edges.

| Concern | Orca | Balancewheel |
|---|---|---|
| Panes, tabs, worktree per task, diff review, browser | Yes — its core | No (uses Orca or a terminal) |
| Notifications, "needs you" badges, mobile companion | Yes | No |
| Quota and reset times per vendor plan | Yes: Claude, Codex, agy pools, Kimi, GLM, Cursor and more | Reads Orca's snapshot; Claude's statusline and OpenRouter's key endpoint itself |
| Same-vendor account switching | Yes (manual; auto at 90% is new) | No — and it must be off for bw-managed work (below) |
| Multi-agent orchestration (runs, tasks, workers, decision gates) | Yes, experimental | No; the panel protocol is a different thing |
| **Moving the lead to another vendor when a plan is capped, with a handoff, and back** | No (open feature requests only) | Yes — `bw lead` |
| **Profiles**: a project's lead order, checkpoint rosters, panel depth | No | Yes |
| **A review panel**: blind first round, claim-only cross-examination, moderator verifies every finding against the code | No — "race three agents" leaves the judging to you | Yes — the core idea |
| **A scoreboard**: confirmed findings, unique catches, disputes, redundancy watches, lead scores, cost per finding | No measure of quality | Yes — the metrics loop |
| **Enforced read-only seats; OS-sandboxed leads verified by attack** | No — it launches agents with permission bypass flags by default; disposable worktrees are its safety model | Yes — `bw sandbox`, seat attack tests |
| Policy changes that cite evidence and state re-promotion criteria | No | Yes |

The short version of Balancewheel's value: **Orca makes many agents visible; Balancewheel
makes them accountable.** It decides which model leads and which models review, keeps the
reviewers unable to change anything, checks what they claim, and keeps the record that
tells you, with numbers, which model is worth its plan.

## What we stopped building because Orca has it

Recorded as amendments in the 2026-10-24 spec ("Orca as the default canvas"):

- **Canvas driving.** `bw start` opens the lead in an Orca pane; nodeterm drops to "another
  canvas that can host `bw lead`".
- **Per-vendor quota probes for Codex and Kimi.** Orca already polls them; bw reads its
  snapshot instead of reading those vendors' credential files itself. Claude stays on the
  statusline; agy keeps its documented `--print /usage` command; OpenRouter keeps its
  documented key endpoint.
- **The cockpit's quota bars and countdowns, its inbox view, and macOS notifications.**
  Orca shows quota, and its notifications cover "an agent is waiting". The cockpit becomes a
  read-only scoreboard (Models, Leads, Usage and cost, Graph, History).

What we kept, against the first draft of the trim, because Orca does not provide it: the
`ask`/`answer` records, `bw inbox`/`bw answer`, and rotation notices ("all leads capped")
in `bw status` — they feed handoffs and lead scoring, and Orca's inbox is agent status, not
bw's questions.

## What the spike found

1. **Usage is reachable, undocumented.** `orca account list --json` returns
   `result.rateLimits`: per provider, `status`, `error`, `updatedAt`, and windows of
   `{usedPercent, windowMinutes, resetsAt (epoch ms), resetDescription}`, plus per-model-pool
   buckets for agy and a separate Fable-only weekly window for Claude. It is Orca's cached
   poll (the CLI passes `refreshUsage: false`). The numbers matched reality, including bw's
   own statusline reading at the same moment. The CLI's help says listing "shows no usage",
   so the field is not a promised interface.
2. **How Orca gets usage.** Claude: the OAuth token from the macOS Keychain, read through
   `/usr/bin/security` (the tool Claude Code itself uses, so no Keychain prompt and no ACL
   change), sent to Anthropic's usage endpoint. The source contains no token refresh for
   Claude, and its Kimi fetcher states it never refreshes ("the CLI owns rotating refresh
   tokens"). Codex: through Codex's own app-server. agy: its CLI. This is Orca's credential
   read, not Balancewheel's — the spec's rule that bw reads no Keychain credential stands.
3. **Launching Orca edits every vendor's user-level config**, before any agent runs: hook
   entries in Claude Code's user settings, Codex's `hooks.json` plus trust hashes in its
   `config.toml` that pre-approve those hooks, agy's settings and hooks, and Kimi Code's
   config. Each gets a `.bak`, and an existing `.bak` is overwritten. The hooks are gated on
   Orca's pane variables and do nothing outside Orca, with one exception: the agy hook
   answers `{"decision":"ask"}` to PreToolUse *before* its gate, which can turn every tool
   call of a headless agy run outside Orca into an approval prompt.
4. **Orca's own agent launch bypasses permissions** (`claude --dangerously-skip-permissions`,
   and the equivalent for every agent) unless set to Manual.
5. **`bw lead` runs fine in an Orca pane.** `orca terminal create --command "bw lead"` started
   a contained lead in bw's permission mode; Orca still showed its status, because the pane
   environment reaches the child and the global hooks report it.
6. **A sandboxed process can ask Orca to run commands for it — the critical finding.** Orca's
   control channel (a unix socket and a localhost WebSocket, authenticated by a token in a
   readable file under `~/Library/Application Support/orca/`) accepts `terminal create`,
   `terminal send`, and `terminal read`. Under the `sandbox-exec` backend — the fallback for
   bw-contained leads and the backend of an agy-based seat — `orca terminal create --command
   "touch <outside>"` wrote outside the worktree while a direct write was denied. nono
   blocked it only because it happened to deny reading that directory; Codex's sandbox
   refused the connection. Denying reads of Orca's runtime directory (which hides the token)
   blocks both channels; denying connections to its socket is defense in depth. Both were
   verified. This applies whenever the Orca app is running, whether or not the agent is in
   an Orca pane.

## How to integrate

### Install and first launch

1. Take Balancewheel's backup first (`setup/backup.sh`); do not rely on Orca's `.bak` files.
   Orca's hook files are listed in `setup/paths.txt`.
2. Install: `brew install --cask stablyai/orca/orca`. Start it with telemetry off:
   `open --env DO_NOT_TRACK=1 -a Orca`, and turn off Settings → Privacy → "Share anonymous
   usage data" so a Dock launch stays off too.
3. In Orca's settings: Agents → Agent Permissions → **Manual**; Accounts → leave automatic
   account switching **off**; leave Experimental → Orchestration off for managed worktrees.
4. Decide on Orca's status hooks. On (the default) gives Orca's working/waiting/done badges;
   `orca agent hooks off` removes them from every vendor config. Balancewheel never uses them
   as a signal, so either works — but with them on, agy-based seats and leads need their own
   agy configuration directory (below) until Orca fixes its PreToolUse gate.

### Rules `bw` and setup enforce

- **One launch path.** Orca hosts `bw lead`; it never starts a lead itself. `bw start` runs
  `orca terminal create --worktree path:<worktree> --title "<project> lead" --command "bw lead"`.
  Orca's `agent launch`, orchestration workers, automations, and session restore are not used
  on a worktree bw manages, because none of them take bw's worktree lock or its containment.
- **Every sandbox denies Orca's control channel**: no reads under Orca's runtime directory and
  no connections to its socket, in both the nono and `sandbox-exec` profiles, for leads and
  seats (`lead/sandbox.py`, `CANVAS_CONTROL_DIRS`, since 2026-10-25). The attack script
  tries `orca terminal create` from inside whenever `orca` is installed.
- **Seats load no user-level hooks** and `seat` and `bw lead` strip `ORCA_*` variables from
  what they pass to a seat; the seat verification key hashes every user-level config or hook
  file the runtime loads, so a canvas editing one forces re-verification instead of passing
  silently. agy seats get their own configuration directory.
- **Quota from Orca is advisory and bounded.** A plan with `"quota": "orca"` takes Orca's
  provider entry only if `status` is ok with no `error`; `observed_at` is Orca's `updatedAt`
  (never the time bw read it); readings older than twice `probe_every_min` are `unknown`;
  `resetsAt` is converted from milliseconds; only account-wide windows feed rotation, and
  model-pool buckets (Claude's Fable-only window, agy's pools) are ignored unless config maps
  one to a lead or seat; Orca readings never create or clear a cap; the Orca version is
  recorded with each snapshot. If Orca is closed or its JSON changes shape, the plan reads
  `unknown` with a reason, `bw status` says rotation is running on observed caps only, and
  everything else keeps working.
- **Order of trust for quota**: Claude statusline → documented vendor surfaces (agy
  `/usage`, OpenRouter `/api/v1/key`) → Orca's snapshot → `unknown` plus `bw cap`.

### Without Orca

Nothing changes in behaviour: `bw start` opens a terminal tab with `bw lead`, Codex and Kimi
quota read `unknown`, and rotation runs on observed caps and `bw cap`, as v0 does.

## Open items

- Re-test the agy PreToolUse gate once Google quota allows a live headless agy run, and
  report it upstream.
- Ask upstream for a documented usage command (or `--usage` on `account list`), which would
  let the adapter drop its shape checks.
- Where Orca places its own worktrees by default, and whether that can fall inside a lead's
  write grant.
- `BW_STATE_DIR` does not yet redirect the metrics log; isolated test runs append to the real
  scoreboard until it does.
