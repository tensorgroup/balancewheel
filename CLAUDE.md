# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Balancewheel is a **private design repo, pre-extraction**. It holds the architecture,
decision log, and extraction roadmap for a moderated multi-model review panel — code
is being extracted here per the amended roadmap gate. The working reference
implementation runs as the author's personal user-level tooling *outside* this repo
and is being dogfooded; code is extracted here only once the roadmap's criteria hold.

There is now code (extraction started 2026-09-03; the roadmap records the amended gate).
Python 3.9+ standard library only; no packages to install. Run the tests from the repo root:

    python3 -m unittest discover -s tests -v

`metrics/` holds the config loader, the JSONL store (the only reader/appender), the roster
watches, the logger CLI, and the dashboard generator. `tests/fixtures/` are synthetic — keep
them that way (privacy firewall). Commits use a lowercase summary with a `feat:`/`fix:`/
`test:`/`docs:` prefix.

Sibling project: [OpenEscapement](https://github.com/tensorgroup/openescapement)
(esc). Division of labor: balancewheel is the collaboration pattern that *generates and
justifies* seat policy; esc *ships* policy into a repo as a signed rule pack. The two
live at different levels and never write the same file: balancewheel's setup writes only
under the user's home directory (`setup/paths.txt`, plus its state dir and backups); esc
writes agent-readable files only in a repo and keeps just a pack cache and portal data
under the home directory. A `model-seats` pack (this repo's seat policy in esc's pack
shape, the planned `packs/`) is not on esc's main; a stale draft sits on its
`feat/model-seats-pack` branch. Do not describe it as shipped. The README's "Working with
OpenEscapement" section owns the install order and the reconcile rule.

## The docs and what each one owns

- `README.md` — the pitch, the six core ideas, an outline of how it works, the plan,
  a paste-in setup prompt for a new user's own agent, and the use/maintain outline. It
  is the entry point a second person builds from; details stay in `docs/`.
- `docs/architecture.md` — the design write-up: problem, three-peers/one-moderator
  topology, the panel protocol, the metrics loop, the knowledge layer, lessons learned.
- `docs/reference-implementation.md` — the working example with tools, versions, and
  models named. It is a *rebuild guide* (the settings that took debugging, the traps,
  a verification checklist), deliberately **not** a dump of the private setup.
- `docs/roadmap.md` — dogfooding goals, the four extraction criteria, the planned
  directory shape (`seats/`, `panel/`, `metrics/`, `packs/`), and non-goals.
- `docs/handoffs/YYYY-MM-DD-topic.md` — a session record: what changed and why, the
  issues that cost time, what was deliberately left alone, and where to pick up. Point-in-time
  by nature, so it is never the source of truth — when a handoff and an owning doc disagree,
  the owning doc wins.

Docs are dated in their preamble ("Written 2026-08-26 …") and reference specific
tool versions; when updating one, update the date and note what changed since.

## The design in one paragraph

A primary agent (Claude Code) acts as **moderator**; other vendors' own repo-aware CLI
agents (Codex CLI, pi) run as **enforced read-only peer seats**, each behind a
~60-line shell wrapper with a uniform `seat.sh [-r] [-d dir] "prompt"` interface and
per-directory resumable sessions. Protocol: one shared brief → blind parallel round →
targeted cross-examination of *disputed claims only* (specific claim relayed, never the
whole transcript; rounds capped) → moderator applies a substance bar to challenges →
moderator verifies every surviving finding against the code → a structured `panel`
record is appended to an append-only JSONL log. A dashboard renders per-seat win share
and confirmed-finding rates, and seat-policy changes must cite those numbers and state
re-promotion criteria.

## Constraints that shape every change here

- **Privacy firewall.** No personal paths, secrets layout, private data sources, or
  employer/client/project references may land in this repo. Migrating files from the
  private implementation is forbidden; anything that lands here is a clean-room
  rewrite. Existing docs describe the reference setup generically for this reason —
  keep it that way.
- **No preview/stealth model ids in interfaces.** Models are a one-line config detail
  expected to change (the original stealth seat model was retired within weeks). Pin
  them in config; never bake them into interfaces, schemas, or names.
- **Read-only seats are load-bearing.** Any seat design must be verified by attack
  (ask it to write a file, on fresh *and* resumed sessions), not by reading vendor docs.
- **Blind first rounds and claim-only relays are non-negotiable** — the two protocol
  rules whose violation produces an echo chamber or rhetoric instead of signal.
- **Markdown in git is the only canonical store**; search indexes are derived,
  disposable caches. The knowledge layer is out of scope for v1.
- **Non-goals:** not a router/proxy (no request interception or prompt translation),
  not a harness replacement (the user's primary agent stays primary), not a hosted
  service.
- **Verification verdicts come from the moderator's code check, never model
  self-report** — that is the ground-truth signal the whole metrics loop rests on.

## Before code lands here

All four extraction criteria in `docs/roadmap.md` must hold: design stable ~a month,
no preview model id in any interface, clean-room rewrite, and a second user has run the
setup from the README alone. Until then, contributions are to the docs.
