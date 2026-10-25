# Handoff — 2026-10-25: Orca becomes the recommended canvas; the cockpit is trimmed

*Session record (point-in-time; `docs/orca.md`, the spec, and the README own the facts).*

## What changed

- **Decision (the user's):** Orca (MIT, 1.4.240) is the recommended, default canvas and never
  a requirement; nodeterm is no longer driven by `bw start`.
- **Spike** on the author's machine: Orca installed, launched with telemetry off, driven
  through its CLI, its source read. Results are in `docs/orca.md` ("What the spike found").
- **Deep panel** on the adoption: Astra, an Opus 5.5 stand-in for the Fable seat (Fable
  capped), Kimi, DeepSeek Flash; Argon absent (Google weekly cap). 43 raw findings, 17
  after dedupe, none refuted, 3 partial. Every seat put the same preconditions on a
  default (launch path, hook isolation, quota rules, keep the inbox); Astra alone raised
  host-command escape, which an attack then confirmed.
- **Docs:** new `docs/orca.md`; the 2026-10-24 spec amended in place with a named "Orca as
  the default canvas" amendment (sandbox denies, verification key, quota `orca` source
  and its rules, inbox kept, read-only cockpit, launch rules, build order); README
  (Leads and rotation, the paths table); `setup/paths.txt` + CHANGELOG (Orca's hook files);
  CLAUDE.md doc list; architecture pointer.

## Issues that cost time

- **Orca edits every vendor's user config on first launch** (Claude Code settings, Codex
  hooks and trust hashes, agy, Kimi) and overwrites existing `.bak` files. Take
  `setup/backup.sh` before installing it.
- **Critical: a `sandbox-exec`-contained process can have Orca run commands for it.** The
  token in Orca's runtime file is readable, and Orca's socket accepts `terminal create`.
  Verified escape under bw's lead profile and under an agy seat's profile; nono and Codex's
  sandbox blocked it. Mitigation verified with raw profiles: deny reads of
  `~/Library/Application Support/orca` and connections to its socket. **Fixed the same day**
  in `lead/sandbox.py` (`CANVAS_CONTROL_DIRS`, both backends) with a model-free test that
  stands up a fake token and socket and fails without the fix, plus a probe in
  `tests/attack/lead_containment.sh`; the live attack against a running Orca is now blocked
  under both backends. The model-driven attack script was not re-run (it spends quota).
- Usage is in `orca account list --json` (`result.rateLimits`) even though the CLI help says
  listing shows no usage; found by reading Orca's source after the user pushed on it.
- `BW_STATE_DIR` moved locks, handoffs, and quota but not the metrics log: the spike added
  two `session` events for a scratch project to the real log.

## Deliberately left alone

- The only code change is the sandbox fix above; `metrics/` is untouched.
- Orca's status hooks were left installed on the author's machine; whether to run
  `orca agent hooks off` is the user's call (`docs/orca.md`, "Install and first launch").
- The agy PreToolUse `ask` behaviour outside Orca is untested live (Google quota capped).

## Later the same day

- **`docs/stack.md`** (new): every tool used, planned, retired, or evaluated, and why —
  including nodeterm → Orca, cc-switch (deferred to the Usage view; sync features must stay
  off because of OpenEscapement's boundary), and herdr (evaluated, not adopted; its
  unauthenticated pane socket is denied in every sandbox anyway, added to
  `CANVAS_CONTROL_DIRS`).
- **`bw guard` design** (`docs/superpowers/specs/2026-10-25-bw-guard-design.md`): autonomy
  without approval prompts — Claude `auto` and Codex `--approve-for-me` natively; a rules
  broker in `bw lead` with a thin hook for agy and pi; a fast model judge only after a
  rules-only shadow period. Reviewed by a deep three-seat panel; awaiting the user's
  approval before a plan.

## Where to pick up

1. Make `BW_STATE_DIR` default the metrics log under it; a test that a redirected run never
   touches the configured log.
2. Then the starter (step 4) with the Orca pane launch and the Orca settings check, and the
   quota adapter (step 5) under the spec's rules.
