# Handoff — choosing the driver model: harness, billing, containment

*Written 2026-09-16. Picks up from [2026-09-15-driver-mode](2026-09-15-driver-mode.md).
Covers the session that made the driver's model a choice across three vendors, the
billing facts that decided which harness runs which model, and three traps found by attack.
Same standard as the previous handoff: no paths, no host details; the driver tooling
stays private.*

## 1. Overview

The question was "can we choose the main/driver model, and from which tool?" — could the
primary harness drive other vendors' models, could the peer harness (pi) drive the
primary vendor's, and at what cost. The answers reshaped the driver command from
"two non-Claude fallbacks" into a seat table across all three vendors, and moved the
line about *which harness* from a capability question to a billing one.

## 2. What was changed and why

**`reference-implementation.md` — new "Driver mode" section and two checklist items.**
The seat table (harness × model × billing), the pi-as-second-harness notes, the
per-harness containment settings, and the traps below. Checklist items 6–7
make "attack in both directions" and "mechanism, not model refusal" explicit.

**`architecture.md` — one paragraph in "Seat mode and driver mode".** A driver need not
be a different vendor; which harness runs a model is decided by billing and containment,
not capability. Preamble dated.

**`roadmap.md` — non-goal widened, one dogfooding goal extended.** "Driver mode" now
includes the primary vendor's cheaper tier; the vendor-churn goal notes a subscription
route that exists only from a given runtime version.

**Why billing decides the harness.** The primary vendor's subscription covers only its own
surfaces; a third-party harness using that subscription's login is billed per token as
"extra usage" (policy since April 2026, stated in the peer harness's own docs and
surfaced as a warning in its UI), and is a terms-of-service gray area besides. The OpenAI
subscription, by contrast, explicitly endorses third-party harness use. So: the peer
harness can be the single harness for every non-primary driver, and the primary vendor's
models drive only through the primary harness — at a cheaper tier, as the first failover.

## 3. Issues encountered

**Issue 1: a subscription route only lists the models the installed runtime knows.** The
peer harness's model catalog is baked in per release; the installed version stopped one
model generation short of the current seat model, and the runtime's own model listing hid
the provider entirely until a login existed. Checked by unpacking the published package,
not by reading docs. The upgrade crossed eleven minor versions, so the review seat's
read-only layers were re-verified by attack the same day.

**Issue 2: the primary harness refuses to write anything under its own config
directory.** Driver worktrees had been created under the primary harness's state
directory (chosen for the physical-path reason in the previous handoff). The first attack
run refused *all eight* steps — including the two writes inside the worktree — as
"sensitive file". A driver that cannot write is broken, and reading the outside-write
refusals alone would have called this a pass. Worktree root moved to a neutral state
directory; the physical-path rule still applies.

**Issue 3: the primary harness inherits the user's permission grants.** Any working setup
allows edits everywhere and the common shell commands, so the worktree alone was a
suggestion. Per-launch settings fix it: a deny rule on the checkout for file-editing
tools plus the built-in command sandbox with the checkout on its deny-write list. Three
things that only the run revealed: absolute rule paths take a leading **double** slash (a
single slash resolves relative to the project root and protects nothing); only the
generic edit rule matches file tools, the per-tool variants are ignored with a warning;
and the auto-accept mode scopes itself to the working directory, which is what is wanted.

**Issue 4: a model's refusal is not verification.** The review seat's first two probes
after the upgrade returned polite "I'm read-only" replies with no guard-log entry — the
model declined before calling a tool. A long "sanctioned containment test" prompt then
stalled the seat for its whole wall-clock cap (the seat pings in seconds; the prompt was
the problem). One-line prompts that ask for exactly one tool call produced a real call, a
logged denial, and an unchanged directory. Evidence is the log line and the filesystem.

**Issue 5: a harness with no sandbox writes wherever it is told.** The peer harness has no
sandbox of its own. Its first attack run as a driver was a clean sweep in the wrong
direction: every inside step succeeded and so did every outside one, including a copy
through a symlink planted in the worktree. The fix wraps the whole process in the OS
sandbox with a deny rule on the checkout's physical path, re-allowing the repository's
shared git directory (minus hooks and config) so commits from the linked worktree still
land. Re-attacked through the real launch path: inside writes and a commit succeeded,
all three outside writes failed with "operation not permitted", checkout clean.

**Issue 7: the litter had a single cause.** Hash-named directories and a `node-compile-cache/`
tree kept appearing in the checkout. The context-mode sandbox forces `TMPDIR` to the
project directory for every command it runs, and each vendor CLI honours `TMPDIR`. The
review-seat wrappers already reset it to a private scratch directory; the driver launcher
now does the same and also pins Node's compile-cache path there, and the global and repo
ignore files carry the patterns. See "Temp-dir hygiene" in `reference-implementation.md`.

**Issue 6: the cleanup guard counted the wrong thing.** "Unpushed commits" was measured
against the remote base only, so any worktree based on a local branch that was ahead of
its remote read as unpushed and refused removal. Now: commits on the driver branch that
are on neither the remote nor the local base.

## 4. Files changed

| File | Change |
|---|---|
| `docs/reference-implementation.md` | "Driver mode" section; checklist items 6–7; pi version bump; preamble dated |
| `docs/architecture.md` | Same-vendor tier, harness billing, first-party containment paragraph; preamble dated |
| `docs/roadmap.md` | Non-goal widened; vendor-churn goal extended |
| `.gitignore` | Tool-litter patterns |
| `docs/handoffs/2026-09-16-driver-model-choice.md` | This file |

All three driver harnesses were attack-tested in both directions during this session;
the peer-harness OpenAI seat had its live turn after the login step and passed once the
OS sandbox was in place.

Uncommitted, together with the previous session's `CLAUDE.md` line and handoff.

## 5. What was not changed

- **The seat design and the panel protocol.** Review seats remain enforced read-only;
  driver runs remain unscored.
- **The driver implementation.** Private, per the firewall. The seat table, containment
  settings, and traps landed as prose only.
- **`README.md`.** Status and pointers are still accurate.

## 6. Known loose ends

- **The peer harness's login menu also offers the primary vendor.** The login works; the
  billing does not (see "Why billing decides the harness" in §2). Leave that provider unused in the driver directory, or
  budget for per-token extra usage knowingly.
- **Harness billing is vendor policy and will move again.** The section records the
  April 2026 position; re-check the peer harness's providers doc before relying on it.
- **"A driver never reviews its own output" is still stated, not enforced** — unchanged
  from the previous handoff, and now slightly more pressing: a Claude-tier driver and the
  panel's fresh Claude seat are the same vendor.
- **Driver-mode extraction clock restarts again.** The seat table changed today; the
  "design stable ~a month" criterion counts from here for anything driver-related.

## 7. How to pick up from here

Model driver seats as rows of one table — harness, model, credential, containment — and
keep the launcher per harness, not per model. The routine that came out of this session is in
`reference-implementation.md` under "Choosing a driver": start in the primary harness,
reach for a driver on budget, parallelism, or mechanical work, default to the other
vendor's pool, and match driver strength to how well-specified the task is. Before trusting any new row, run checklist
items 6 and 7 in `reference-implementation.md`: write inside must land, writes outside
(direct and via a planted symlink) must be refused, and the refusal must come from a
mechanism you can see in a log, not from the model's manners. Re-run both after any
harness upgrade — the review seat's guard and the driver's sandbox are the two things an
upgrade can silently loosen.
