# Balancewheel and Orca

*Written 2026-10-25 against Orca 1.4.240 (macOS, Homebrew cask `stablyai/orca/orca`), read
from its docs, its source (main at `a4e6e1f0`), and a hands-on spike on the author's machine.
A deep four-seat design review of the adoption followed (43 raw findings, 17 after dedupe,
none refuted); its conclusions are folded in below. Orca ships near-daily, so re-check the
version-specific facts here before relying on them. Revised 2026-10-25: how the `bw guard` agy hook composes with Orca's.
Revised 2026-10-25 (re-checked on 1.4.240): no account auto-switch setting exists;
orchestration is a skill, not an Experimental toggle (install step 3 rewritten); Orca's pi
extensions join the list of files it edits; orcad's WebSocket port, cc-switch's proxy port
and the cockpit port are now denied to every sandbox. Revised 2026-10-25 (`bw start`): the
launch argv and the settings check below are written as built. Revised 2026-10-25 (quota
and the inbox): the Orca quota adapter's rules below are as built, with `orca_windows`, the
mid-session re-read, and the fallback to a Terminal tab when `orca terminal create` fails.
Earlier dates in this document were corrected to 2026-10-25, the day the work was done.*

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
| Same-vendor account switching | Yes, manual (1.4.240 has no auto-switch setting) | No — and Orca must manage no accounts for bw-managed work (below) |
| Multi-agent orchestration (runs, tasks, workers, decision gates) | Yes, as an installable skill | No; the panel protocol is a different thing |
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
   It also writes three pi extensions, `~/.pi/agent/extensions/orca-agent-status.ts`,
   `orca-prefill.ts` and `orca-titlebar-spinner.ts`, gated on `ORCA_PANE_KEY`. A pi lead in
   bypass mode loads them; a guarded pi lead runs `--no-extensions` and does not.
4. **Orca's own agent launch bypasses permissions** (`claude --dangerously-skip-permissions`,
   and the equivalent for every agent) unless set to Manual.
   **Composition with `bw guard`.** Orca's agy hook and the guard's both sit under agy's
   PreToolUse, and Orca's answers `ask` before its own gate. `bw guard install agy` prints the
   other PreToolUse entries it finds beside its own; if Orca's is one of them, `orca agent
   hooks off` removes it. The command does not run yet: it refuses, as `bw guard verify agy`
   does, until agy's hook spike has observed how agy combines several hooks' answers
   (usage-capped; about 2026-10-31). Until then, give an agy lead `manual` or `bypass`
   autonomy.
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
7. **orcad listens on loopback port 6768** (its WebSocket). Before 2026-10-25 both sandboxes
   could connect to it; orcad rejects any frame that is not a paired `e2ee_hello`, and the
   pairing keys sit in the already-denied Orca directory, so nothing was reachable through
   it. It is now denied anyway. Orca's agent-hook endpoint takes a token that sits in the
   same denied directory (`agent-hooks/endpoint.env`).

## How to integrate

### Install and first launch

1. Take Balancewheel's backup first (`setup/backup.sh`); do not rely on Orca's `.bak` files.
   Orca's hook files and its pi extensions directory are listed in `setup/paths.txt`.
2. Install: `brew install --cask stablyai/orca/orca`. Start it with telemetry off:
   `open --env DO_NOT_TRACK=1 -a Orca`, and turn off Settings → Privacy → "Share anonymous
   usage data" so a Dock launch stays off too.
3. In Orca's settings: Agents → Agent Permissions → **Manual**; Accounts → add **no managed
   accounts** (1.4.240 has no automatic switching setting; an account Orca manages is one it
   can switch, so `bw start` checks there are none). Orchestration is a skill, not a setting:
   do not install it for managed worktrees.
4. Decide on Orca's status hooks. On (the default) gives Orca's working/waiting/done badges;
   `orca agent hooks off` removes them from every vendor config. Balancewheel never uses them
   as a signal, so either works — but with them on, agy-based seats and leads need their own
   agy configuration directory (below) until Orca fixes its PreToolUse gate.

### Rules `bw` and setup enforce

- **One launch path.** Orca hosts `bw lead`; it never starts a lead itself. `bw start` runs,
  once `orca status --json` says the app is running,
  `orca terminal create --worktree path:<worktree> --title "<worktree basename> lead"
  --command "<cmd>"`, where `<cmd>` is
  `env BALANCEWHEEL_CONFIG=… BW_STATE_DIR=… <real path of bin/bw> lead` (the two assignments
  only when set in bw's own environment). If Orca is running but that command fails or times
  out, `bw start` says so and opens a Terminal tab instead (see "Without Orca"). It refuses
  first when bw lies inside the target worktree or its common git dir, and opens nothing when
  the worktree's lock is held.
  Orca's `agent launch`, orchestration workers, automations, and session restore are not used
  on a worktree bw manages, because none of them take bw's worktree lock or its containment.
- **The settings check** (`lead/orca.py`, shown by `bw status` and `bw start`) reads the
  active profile from a copy in the state dir's `sealed/`. Agent permission default "Manual"
  is stored as `own` (or `ask`; 1.4.240 stores no literal "Manual"); `full-access` and
  `auto` warn, and so does any per-agent override set to one of them. Telemetry on warns;
  so do managed Claude or Codex accounts (there is no automatic-switching setting to read).
  An unreadable database or an unknown value is `unknown` with the reason, never an error;
  Orca installed but not running says so.
- **Every sandbox denies Orca's control channel**: no reads under Orca's runtime directory and
  no connections to its socket, in both the nono and `sandbox-exec` profiles, for leads and
  seats (`lead/sandbox.py`, `CANVAS_CONTROL_DIRS`, since 2026-10-25). The attack script
  tries `orca terminal create` from inside whenever `orca` is installed.
- **Every sandbox refuses connections to the canvas and cockpit ports, on any host** —
  loopback, `::1`, or the Mac's own interface address for a service bound to 0.0.0.0
  (`CANVAS_CONTROL_PORTS`, `denied_ports`, since 2026-10-25): 6768 (orcad), 15721 (cc-switch's
  proxy), 8765 (the cockpit's default) and `cockpit.port` when configured — the cockpit too,
  because cookies are not port-isolated. Both backends; each rule checked by a real connect
  in `tests/test_sandbox.py`, and the attack script probes each port. Both also deny reads of
  `~/.cc-switch` (its database holds Codex auth and copies of every vendor config) and of
  bw's `state_dir/sealed/`, where bw's copies of Orca's and cc-switch's databases live.
- **Seats load no user-level hooks** and `seat` and `bw lead` strip `ORCA_*` variables from
  what they pass to a seat; the seat verification key hashes every user-level config or hook
  file the runtime loads, so a canvas editing one forces re-verification instead of passing
  silently. agy seats get their own configuration directory.
- **Quota from Orca is advisory and bounded** (`lead/orca.py`, `lead/quota.py`):
  - *Which entry.* A plan names its provider with `orca_provider` (`claude`, `codex`, `kimi`,
    `antigravity`, … as `orca account list --json` spells them; config data, not constants in
    code). Orca's entry is taken only if its `status` is ok with no `error`; anything else is
    `unknown` with Orca's own words, escaped. `observed_at` is Orca's `updatedAt`, never the
    time bw read it, and `resetsAt` is converted from milliseconds and bounds-checked.
  - *Shape.* Each mapped provider's entry is checked on its own for the fields bw reads
    (types, windows present). A failure makes the plans mapped to that provider `unknown`
    with "Orca <version> changed shape: <path>"; other providers keep working. A change of
    Orca's version re-runs the check and the next `bw start`, `bw probe`, or poll-loop re-read
    prints what it found. The Orca version is recorded with each snapshot.
  - *Freshness.* A reading older than twice `probe_every_min`, dated more than 300 s in the
    future, or with malformed windows is `unknown`. Orca's list is its cache, so re-reading
    it is not a fresh observation.
  - *Windows.* Only account-wide windows (`session`, `weekly`) feed rotation, and only when
    the plan's source is Orca (`quota: "orca"`, or `quota: "probe"` when the probe has no
    usable reading). On a `statusline` plan with `orca_provider` set, Orca's windows are
    shown as a cross-check and never chosen. Orca's reading is never logged with a `lead`
    event's quota.
  - *Pools.* A model-pool window (Claude's Fable-only weekly window, `fableWeekly`; agy's
    buckets, `bucket:<name>`) is ignored unless `plans.*.orca_windows` maps it to lead or seat
    names, which are checked against the config. Pools come from Orca's freshest known,
    non-stale snapshot **whatever source won the plan's windows**, so a Claude plan read from
    the statusline still sees its Fable-only pool. A lead named in a full pool (100% used,
    reset in the future) is not started at `start` or at a boundary, and is skipped as a
    replacement when a cap forces a switch; a pool alone never moves a running lead, never
    takes out the plan, and seats in a pool are shown only. A malformed mapped pool is dropped
    with a note; the plan's windows still stand.
  - *Caps.* Orca readings never create or extend a cap. bw clears a cap past its `resets_at`
    only once a fresh, known reading observed at or after that time exists for the plan, or
    `cap_expiry_hours` after it; an unexpired cap is never touched.
  - *Mid-session.* In the poll loop `bw lead` re-reads Orca's local cache synchronously (10 s
    cap, no thread, no network) and rewrites the Orca snapshots for display and freshness; the
    running decision still acts on caps alone, and a failing re-read is noted once and never
    stops the lead.
  - *Closed or changed.* If Orca is closed or its JSON changed shape, the plan reads `unknown`
    with a reason, `bw status` says rotation is running on observed caps only (after a probe
    interval with no reading), and everything else keeps working.
- **Order of trust for quota** (per plan, by its `quota` setting): a `statusline` plan reads
  the Claude statusline; a `probe` plan reads its documented vendor surface (OpenRouter's
  `/api/v1/key`; agy's `/usage` once its output has been observed) and then Orca when
  `orca_provider` is set; an `orca` plan reads Orca; otherwise `unknown` plus `bw cap`. The
  first source with a known, fresh reading wins.
- **agy has no probe yet.** `probe: "agy"` reads `unknown` ("awaiting Task G") until agy's
  `/usage` output has been observed in a live run. A Google plan reads Orca's `antigravity`
  entry meanwhile.

### Without Orca

Nothing changes in behaviour: `bw start` opens a Terminal tab (a `.command` file in the
state dir, run with `open -a Terminal`) with `bw lead` (also the fallback when Orca is running
but `orca terminal create` fails), plans whose only source is Orca (Codex, Kimi) read
`unknown`, and rotation runs on observed caps and `bw cap`, as v0 does.

## Open items

- Re-test the agy PreToolUse gate once Google quota allows a live headless agy run, and
  report it upstream.
- Ask upstream for a documented usage command (or `--usage` on `account list`), which would
  let the adapter drop its shape checks.
- Where Orca places its own worktrees by default, and whether that can fall inside a lead's
  write grant.
- `BW_STATE_DIR` does not yet redirect the metrics log; isolated test runs append to the real
  scoreboard until it does.
