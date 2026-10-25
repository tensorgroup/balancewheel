# Handoff — 2026-10-24: v0 lead rotation shipped; next is the starter

*Session record (point-in-time; the spec, plans, and README own the facts).*

## What changed

- **Spec:** `docs/superpowers/specs/2026-10-24-profiles-lead-rotation-cockpit-design.md` —
  profiles, lead rotation, the starter, the cockpit, terse messages, Jev under evidence, lead
  scoring, `bw sandbox` (nono default, `sandbox-exec` fallback) with the attack results. Its
  "Amendments made while building step 2" section records what changed during the build.
- **Build-order step 2 is merged** (plan: `docs/superpowers/plans/2026-10-24-plan-2-v0-lead-rotation.md`):
  config schema 2; `lead/` (projects, rotation, quota, sandbox, containment, handoff, runtimes,
  runner); `bin/bw` (`lead`, `status`, `cap`, `sandbox verify`); `bin/bw-statusline`;
  `session`/`lead`/`rating` events and `panel.moderator`; `tests/attack/lead_containment.sh`.
  202 tests. Reviewed by a five-seat panel plus a fresh code reviewer; every critical and
  important finding fixed with a test that failed first (containment escapes through git
  pointer files, `.git` rename, submodule dirs, runtime config, a copied `launchctl`, worktree
  filters at handoff time; a pin interrupting a running lead; multi-window caps).
- **Live test run passed** in a scratch repo: Claude Code started as lead, the statusline
  recorded real quota, `bw cap` switched to Codex with a handoff, and the next start chose Codex
  because Claude's 5-hour window was under 10%. It found one bug (Codex could not write its
  checkpoint under its own sandbox), fixed in `73d8987` with `--add-dir`.

## Decisions to keep (the user's)

- Leads in order: Claude first, Codex (Astra) and agy-hosted Claude as fallbacks. Kimi reviews
  but does not lead. Making Astra the default lead waits for lead-scoring data.
- Leads named `contained by self` (Claude Code, Codex, agy) are not gated on an attack pass in
  v0; pi and Kimi are, via `bw sandbox verify`. Reviewers flagged this; the user has not asked
  to change it.
- Always keep every CLI on its latest version, checked **in parallel before every start**.
- nodeterm is the live canvas (optional; BUSL, never a hard dependency).

## Where to pick up

1. **The starter, wired into `bw lead` itself** (the user expects `bw lead` to update first):
   parallel update checks per tool (`tools.*.update` in config; detect the install method once
   and suggest it), serialised only per shared installer (Homebrew, npm), skipping any tool a
   live lead or seat holds; re-run `bw sandbox verify` for a bw-contained runtime whose
   verification key changed (and report self-contained runtimes whose version changed);
   then pick and launch the lead. Spec section "The starter".
2. **nodeterm:** when nodeterm is present, open the lead in a frame for the project through its
   control CLI (a terminal tab otherwise). Spec "The pieces and how they fit".
3. Then build-order step 3 (seat runtimes and the three trial seats in this repo), step 5
   (quota probes for Codex, Kimi, agy, OpenRouter), step 6 (registry and cockpit).

Deferred minors (from the final review): notices print epoch seconds; statusline caps without
`resets_at` use 5 h not `rotation.cap_expiry_hours`; the resolved model has no freshness
check; `bw sandbox verify` hides the attack script's stderr; `_verified` runs `--version`
every poll while a switch is pending; `lead` events are not logged for changes between
invocations.
