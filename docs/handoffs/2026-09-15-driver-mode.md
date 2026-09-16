# Handoff — driver mode: the working agent is not a seat

*Written 2026-09-15. Covers the two commits that introduced driver mode to this repo's
design, and the findings behind them.*

**Scope note.** The driver tooling itself is part of the private implementation and
deliberately did **not** land here — the privacy firewall forbids migrating it, and nothing
about it has met the extraction criteria in [roadmap.md](../roadmap.md). What landed is the
clean-room concept and the traps that generalize. This handoff is written to the same
standard: no paths, no host details.

## 1. Overview

The panel's seats are enforced read-only, and that guarantee is load-bearing. But the
moderator's capacity is a single point of failure: when its budget runs out, the work stops
even though two capable, already-repo-aware peers are sitting idle. Running one of those
peers as the *working agent* is useful precisely on the day the panel is least available.

That mode is writable, which puts it in apparent conflict with "read-only seats are
load-bearing" and with the non-goal "not a harness replacement". The resolution is to name
it as a separate mode rather than weaken either statement: **the read-only guarantee is
scoped to seats**, and vendor failover is a fallback, not a promotion.

## 2. What was changed and why

**`architecture.md` — new "Seat mode and driver mode" section.** A table contrasting the two
across purpose, writes, containment, target, and metrics; the single-point-of-failure
rationale; and the rule that a driver must never review its own output, since that collapses
the blind-round rule the whole protocol rests on. Containment is the key asymmetry: a seat is
safe because it *cannot* write, a driver because the only thing it can write is a scratch
worktree.

**`roadmap.md` — non-goal amended, not dropped.** "Not a harness replacement: the user's
primary agent stays primary" now continues: driver mode is failover, not promotion. Dropping
the non-goal would have been the wrong fix; the statement is still true, it was just
ambiguous about a case that now exists.

**`AGENTS.md` — new, alongside `CLAUDE.md`.** Agent CLIs that discover `AGENTS.md` found
nothing here and worked the repo with no house rules at all — including the privacy firewall,
where a silent violation is expensive rather than merely untidy. The two files are
complementary: `AGENTS.md` carries structure, commands and conventions; `CLAUDE.md` carries
rationale and constraints. Every command listed in it was run before committing.

## 3. Issues encountered

Each of these was found by attacking the mechanism, not by reading vendor docs — which is the
same lesson the seat design already records, arriving again from a different direction.

**Issue 1: a sandbox may refuse a writable root containing a symlinked path component.**
Not "writes through the link fail" — *every* command in the session fails, including ones
that never touch the link. If the scratch worktree is created under a state directory that is
itself a symlink, the driver is dead on arrival with an error that reads like a permissions
bug. Resolve the worktree to its physical path when creating it.

**Issue 2: `pipefail` plus a `grep` that selects no lines is a silent killer.** `grep` exits 1
when it filters everything out; under `set -o pipefail` that becomes the pipeline's status,
and under `errexit` the script dies mid-function with no message and a bare exit 1. This bit
three separate code paths in one sitting. Any wrapper written in shell wants every
grep/curl/log pipeline explicitly guarded.

**Issue 3: the base-branch rule cuts both ways.** "Base off the remote integration branch,
never local HEAD" exists because a local branch with unpushed commits once swept unrelated
work into a squash-merge. The opposite failure is quieter and was live here: when the *local*
branch is far ahead of its remote, basing on the remote hands the driver a stale tree and
reports nothing. The driver then works confidently against code that does not exist any more.
Detect the divergence and refuse, offering both exits — push, or base on the local ref
explicitly.

**Issue 4: a worktree contains only committed files.** `git worktree add` checks out the
tree, not the working directory, so an uncommitted instruction file never reaches the driver.
The new `AGENTS.md` demonstrated this on its first test: written, present locally, and
entirely invisible to the agent it was written for.

**Also worth knowing:** worktree removal refuses over the scaffolding the setup itself
created (symlinks, copied config). Forcing it is correct *only* after the wrapper's own
guards confirm no uncommitted work and no unpushed commits — the force must come after the
check, never instead of it.

## 4. Files changed

| File | Change |
|---|---|
| `docs/architecture.md` | New "Seat mode and driver mode" section; preamble dated |
| `docs/roadmap.md` | "Not a harness replacement" non-goal amended |
| `AGENTS.md` | New — structure, commands, conventions, firewall |

Commits: `0e7b454` (driver mode), `ee64473` (AGENTS.md).

## 5. What was not changed

- **The seat design.** Seats remain enforced read-only; no wrapper, guard, or sandbox setting
  was touched. Driver mode is deliberately a separate path, not a flag on the seats.
- **The panel protocol and the metrics loop.** Driver runs are not logged and not scored — a
  driver is not making review claims, so it has no win rate to earn.
- **`reference-implementation.md`.** It owns "the settings that took debugging and the traps",
  which is arguably where §3 belongs in rebuild form. Left alone to avoid duplicating the
  same content in two docs before someone decides which owns it.
- **The driver implementation.** Private, per the firewall. Nothing was copied in.

## 6. Known loose ends

- **"A driver never reviews its own output" is stated, not enforced.** Nothing in the config
  or the logger prevents a panel from seating the same vendor that wrote the code. If that
  matters, it wants a check at panel time, not a line in a doc.
- **No data on whether driver-written code fares differently under review.** Driver runs are
  unscored by design, so this is currently unmeasurable. Worth deciding whether a minimal
  record type would be worth the complexity before the question gets asked seriously.
- **Driver mode is new, so the "design stable ~a month" extraction criterion restarts for
  anything that would carry it into code.** The concept landing here does not shorten that.
- **Instruction files only reach a driver once committed** (Issue 4), and only once pushed if
  the worktree is based on a remote ref. Both this repo's `CLAUDE.md` and its new `AGENTS.md`
  are still local-only.

## 7. How to pick up from here

If driver mode ever gets extracted: it is a **mode of the existing seat abstraction**, not a
new abstraction. The same wrapper interface applies; what changes is the containment
mechanism (worktree instead of tool allowlist) and whether the run is scored. Modelling it as
a second, parallel concept would duplicate the seat plumbing for no gain.

Verify it the way the seats are verified — by attack, in **both** directions: confirm that
writes inside the scratch worktree succeed, and that writes aimed at the real checkout,
including through any symlink the setup created, are refused. A driver that cannot write is
broken; a driver that can write to the checkout is dangerous. Only testing both catches both.

Before trusting a first run, check the cheap failure modes from §3 in this order: physical
path (Issue 1), base freshness (Issue 3), instruction files actually present in the worktree
(Issue 4).
