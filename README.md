# Balancewheel

**Moderated multi-model agent collaboration, with the receipts to tune it.**

In a mechanical watch, the escapement regulates — and the *balance wheel* is where
opposing forces oscillate into a steady beat. Balancewheel applies that idea to AI
coding agents: multiple frontier models from different vendors review, plan, and
debate the same work as **peer seats**, a moderator holds them to evidence, and every
dispute is **logged and scored** so seat assignments are tuned by win rates instead of
vibes.

Sibling project to [OpenEscapement](https://github.com/tensorgroup/openescapement):
esc *ships* the policy; balancewheel is the collaboration pattern that *generates and
justifies* the policy (see the model-seats pack).

## The core ideas (one screen)

1. **Peer CLI seats, not API calls.** Each non-primary model runs as its vendor's own
   repo-aware CLI agent (headless, read-only, own session store). No prompt
   translation layers, no provider mixing hacks — the orchestrator shells out and the
   seat explores the repo itself.
2. **Independence before debate.** Seats review in parallel without seeing each
   other's output; only *disputed, substantive* claims get cross-examined, with the
   specific claim relayed — never the whole transcript.
3. **A moderator with a substance bar.** Challenges must state what breaks and where.
   Style and wording objections are dismissed without relay — but still logged and
   scored against the challenger.
4. **Verification is the only gate.** The moderator verifies every surviving finding
   against the actual code before anything is acted on. Seats advise; evidence decides.
5. **The scoreboard closes the loop.** Every seat invocation and every dispute
   (challenger, both proposals, winner, why) is appended to a log; a dashboard renders
   win shares and confirmed-finding rates; seat policy changes cite the numbers and
   carry re-promotion criteria.
6. **Knowledge stays markdown; indexes are derived.** All shared knowledge is
   markdown in git. Search layers (FTS, embeddings) are disposable local caches.

## How it works

Three roles, one protocol, one log.

- **Moderator.** The coding agent you already use (the reference setup uses Claude
  Code). It frames the target, runs the seats, cross-examines disputes, verifies every
  surviving finding against the code, and writes the record. It is the only thing that
  ever changes your checkout.
- **Seats.** Other vendors' own CLI agents, each behind a ~60-line shell wrapper with
  one interface — `seat.sh [-r] [-d dir] "prompt"` — that makes it repo-aware,
  resumable per directory, and **enforced read-only** (tool allowlist, sandbox, and a
  guard; verified by trying to make it write, not by reading vendor docs). Models are a
  one-line config detail, never part of an interface.
- **Drivers.** The same CLIs, run as the *working* agent in a throwaway git worktree when
  the moderator's budget is the constraint, when you want a second worker in parallel on
  another subscription, or for mechanical work. A driver is writable by design and
  contained by the worktree plus the OS sandbox; a driver's vendor never reviews its own
  output.
- **The protocol.** One shared brief → blind parallel round → cross-examination of
  disputed claims only, rounds capped, seats wall-clock capped → substance bar →
  moderator verification → one structured `panel` record appended to an append-only
  JSONL log.
- **The metrics loop.** `metrics/` reads that log: per-seat win share, confirmed-finding
  rate, usage, and roster watches that flag panel bloat, skimmed verification, and a
  seat that has stopped contributing. Seat policy changes must cite those numbers.

Details: [docs/architecture.md](docs/architecture.md) (design and rationale),
[docs/reference-implementation.md](docs/reference-implementation.md) (the rebuild guide:
tools, versions, models, the settings that took debugging, the traps, the checklist).

## Status and the plan

**Design repo, mid-extraction.** The reference implementation runs as the author's
private tooling and is dogfooded daily. What is here: the full design, the rebuild guide,
dated session handoffs, and the `metrics/` package (Python 3.9+, standard library only,
tested). What is deliberately *not* here yet: the seat wrappers, the protocol prompts,
and the driver launcher — those are rewritten clean-room once the criteria in
[docs/roadmap.md](docs/roadmap.md) hold: design stable for about a month, no preview
model id in any interface, no private paths or references, and one person other than
the author has built the setup from this README alone.

Planned shape: `seats/` (wrapper generator), `panel/` (the protocol as instruction-file
templates — the moderator stays whatever agent you already run), `metrics/` (here now),
`packs/` (seat policy exported as OpenEscapement rule packs).

## Getting started

You need: a primary coding agent you already use, at least one other vendor's CLI agent
on a subscription or API key you are willing to spend, and a repo to review. Read
[docs/reference-implementation.md](docs/reference-implementation.md) once, then let your
primary agent do the assembly. Paste this into it, from the root of this repo:

```text
Set up a balancewheel review panel for me.

1. Read README.md, docs/architecture.md, docs/reference-implementation.md and
   docs/roadmap.md in full. The two rules that are never relaxed: seats are enforced
   read-only and verified by attack; first rounds are blind and only disputed claims
   are relayed.
2. Ask me which vendor CLIs I have installed and logged in (for example Codex CLI, pi,
   Gemini CLI), which subscription or key each one uses, and which model each should run.
   Warn me if a route bills per token behind a subscription login — the rebuild guide's
   driver-mode section explains which ones do.
3. For each seat, write a wrapper that implements the interface in the guide's "Seat
   wrappers" section: new or resumed session, per-directory session persistence,
   read-only enforcement in every layer the guide lists for that CLI, a private scratch
   TMPDIR, a wall-clock cap, and a `usage` record appended via metrics/log.py. Pin the
   model in exactly one config line per seat.
4. Copy balancewheel.example.json to my config location, fill in the seats, and run
   `python3 metrics/log.py config` to validate it.
5. Run the verification checklist in the guide against every seat, fresh and resumed,
   and show me the evidence (the refusal text and the unchanged directory), not the
   model's own description of what it did.
6. Write me a moderator instruction file for the panel protocol (shared brief, blind
   round, dispute relay, substance bar, verification, the logging step) and run one
   panel on a file with two planted bugs so I can see the whole loop, including the
   record it appends and `python3 metrics/dashboard.py` rendering it.
7. Keep a list of everything these docs did not tell you. I will send it upstream.
```

Nothing in this repo assumes a particular vendor. The reference setup names its own
choices so you can see a working combination, and its history shows why the model
names live in config: the first seat model was retired by its provider within a week.

## Using it

- **Review** happens at checkpoints, not at the end: when a design converges, when a
  plan is written, at code review, and in debugging when the first hypothesis fails.
  Convene the panel, verify what survives, log the record.
- **Drive** when the moderator's budget is the constraint, when a second worker on a
  different subscription can take an independent, well-specified task in parallel, or
  for mechanical work. Start in your primary agent; reach for a driver from there. Match
  driver strength to how well-specified the work is: a weaker model is fine behind a
  written plan, tests, and the panel, and wrong for anything ambiguous.
- **Read the scoreboard** before changing roles. A seat is dropped or demoted on logged
  outcomes over a window, never on one bad review, and every change states what would
  earn the role back.

## Maintaining it

- **Re-verify by attack after any harness upgrade.** Both directions for drivers (a write
  inside must land; a write to the checkout, including through a symlink, must be
  refused) and the read-only checklist for seats. An upgrade is the one thing that can
  silently loosen a guard.
- **Log every panel**, with the findings themselves, not just counts — the watches and
  the dashboard are only as honest as the records. Verdicts come from the moderator's
  code check, never model self-report.
- **Act on the watches.** They are printed after each panel: triage in-panel when
  findings bloat, re-verify a sample when nothing was refuted, consider dropping a seat
  that has stopped contributing. Roster changes are a human decision.
- **Keep temp files out of the checkout.** Wrappers reset `TMPDIR` to a private scratch
  directory; the guide's "Temp-dir hygiene" section says why and what to ignore.
- **Date the docs.** Each doc's preamble says when it was written and what changed
  since; session handoffs under `docs/handoffs/` are point-in-time and the owning doc
  wins when they disagree.
- **Run the tests** from the repo root: `python3 -m unittest discover -s tests -v`.
