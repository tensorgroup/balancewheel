# Handoff — 2026-10-26: the starter, quota sources, the inbox, the registry, and the cockpit

*Session record (point-in-time; the README, `CHANGELOG.md`, `docs/orca.md`, and the 2026-10-24
spec own the facts, and win where they disagree with this).*

## What changed

Plan 4 shipped as three phases on `main`, each reviewed task by task.

- **The starter (phase 4).** `bw start` updates tools in parallel under per-runtime locks,
  re-verifies each lead runtime whose containment or guard key changed, shows quota, and opens
  the worktree in an Orca pane or a Terminal tab. Why: starting a day should be one command, and
  an update must never land under a verified-but-stale binary.
- **Quota sources and the inbox (phase 5).** Each plan reads its numbers from the statusline,
  a probe (OpenRouter's key endpoint), or Orca's cache, with windows and model pools arbitrated
  apart; bw clears an expired cap itself. Leads publish asks as files and read answers from a
  bw-owned, answers-only view. Why: a contained lead cannot run `bw` or write the inbox, and a
  plan capped once and read only by Orca otherwise stayed blocked.
- **The registry and the cockpit (phase 6).** `bw registry` writes a derived snapshot of what
  config wires together, `bw cockpit` serves it and the metrics as five read-only tabs, and
  cc-switch's usage rows are read by sealed copy. Why: Orca covers live canvas and quota bars,
  so what remains for bw is explaining decisions, scoring models, and showing cost.
- Docs, spec amendments A1-A14, and the CHANGELOG were updated as each phase landed.

## Issues that cost time

- **WAL databases cannot be opened read-only in place.** Even `mode=ro` writes `-shm`, and it
  failed on Orca's database. The fix is a copy of the database and its WAL into a sandbox-denied
  directory, read `query_only`; the original is never touched (A11).
- **Lock premises.** An early ruling assumed a lock fd opened read-only could not be upgraded;
  `flock` ignores the access mode. bw now holds its own separate, non-inherited shared lock
  next to the one passed to the lead.
- **Sandbox matching.** Port denies written as `localhost:<port>` matched only the name; the
  strictly stronger `(remote ip "*:<port>")` form is used so the denial does not depend on a
  service's bind address. nono also needed an explicit read deny for the sealed directory.
- **Cookies are not port-isolated.** A dev server a lead runs on another loopback port gets the
  cockpit's cookie when the user's browser visits it. Hence the cockpit serves only on a port
  every sandbox denies, and `--port` outside that set is refused.
- **Unreported is not zero.** Several review rounds turned a silent 0 into "unavailable"
  (non-integer token counts, out-of-range timestamps, lost sessions counted as minutes).
- **Review found the same class repeatedly:** lead-written text reaching argv or a notice
  unbounded, and tests that were not hermetic (a `bw status` run against real state).

## Deliberately left alone

- **Task G (agy hook spike and agy `/usage` parser).** Skipped until the Google plan's quota allows a
  live agy run. Until it runs, `probe: "agy"` reads `unknown` ("awaiting Task G"), a Google
  plan can read Orca's `antigravity` entry, and agy's guard stays refused. A parser written
  without seeing the real output could read the wrong field as quota.
- **Jev (the decision model)** stays advisory; the acting-mode work is not built.
- **Task events and `panel.subject`.** The Leads table ships the measured columns only (A13).
  Throughput per task, defects, escapes, clean work, and moderation quality stay pending.
- **Auth state.** A tool's `auth` is `unknown`; bw reads no credential.
- **A lead can forge its own `cap-signal`.** The file sits in the lead's checkpoint
  directory and has been writable since plan 2. The effect is limited to rotating away from
  itself, so it was not fixed here.
- **A cockpit lockout by cookie tossing** (a page on another localhost port sets a duplicate
  cookie; 403, nothing disclosed) is accepted as a denial of service with no exposure.

## Open items

- **Re-verification runs** after the sandbox and guard changes, which spend a little quota:
  `bw sandbox verify pi`, `bw sandbox verify kimi`, and `bw guard verify pi`. The review left two
  live questions for the last: whether the pi extension control loads, and whether 900 s is
  enough for the guard attack.
- **Task G** once the Google plan's quota allows a live agy run.
- **Look at `bw cockpit` in a browser, in light and dark.** Every check so far was headless.
- **A live `bw start` with Orca running**, including an Orca pane launch (checked only with
  Orca quit, through the dry run and the Terminal path).
- **Orca UI settings:** Agent Permissions to Manual and telemetry off, which `bw status`
  warns about until done.
- **Deferred minors** from the build ledger worth a follow-up: `proc.run` maps a missing cwd to
  "not found"; a failed or timed-out update still starts the `min_interval_hours` window; the
  `tools.<t>.app` path is not `~`-expanded; `INBOX_LIMIT` counts characters, not bytes; the
  `build_handoff` argv size should be checked end to end against `read_checkpoint`'s cap;
  a few test classes sit after the `__main__` guard; `_guarded` and `_num` are duplicated across
  modules; `bw status` does not render the Orca cross-check; the Graph view builds the registry
  per request (version probes make it slower than the other tabs).

## Where to pick up

1. Run the three verifications above and a live `bw start` with Orca open.
2. Open `bw cockpit` and read each tab in both themes.
3. When the Google plan's quota allows it, run Task G, then switch agy's probe and guard on.
4. Decide whether `task` events and `panel.subject` are next; the Leads table's pending columns
   and the lead comparison the spec motivates both wait on them.
5. Plan the `packs/` export step only after the extraction gate in `docs/roadmap.md` holds.
