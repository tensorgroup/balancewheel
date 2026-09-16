# Repository Guidelines

## Project Structure & Module Organization

`metrics/` holds the Python package: `config.py` (config loader), `store.py` (the JSONL
store), `watches.py` (roster watches), `log.py` (the logger CLI), and `dashboard.py` (the
dashboard generator). Tests live in `tests/`, with synthetic data in `tests/fixtures/`. Prose
lives in `README.md` and `docs/`; `docs/superpowers/` holds the specs and plans behind the
extraction. `balancewheel.example.json` is the annotated config example. `setup/` holds `paths.txt` (the
home-directory paths a setup touches) and the `backup.sh` / `restore.sh` pair around it;
`tests/test_setup_scripts.py` runs both against a temporary `HOME`.

This is a **design repo, pre-extraction**: the reference implementation runs outside this repo
and is being dogfooded, and code lands here only once the four criteria in `docs/roadmap.md`
hold. Until then, contributions are to the docs.

## Build, Test, and Development Commands

Python 3.9+, standard library only. There is nothing to install and no build step.

- `python3 -m unittest discover -s tests -v` runs the suite from the repo root.
- `python3 -m unittest tests.test_store -v` runs one module while developing.
- `python3 metrics/log.py --help` is the logger CLI (`panel`, `usage`, `watches`, `resolve`,
  `amend`, `check`, `config`).
- `python3 metrics/dashboard.py --help` renders the dashboard.

## Coding Style & Naming Conventions

Four-space indentation, `snake_case` for functions and modules, and a module docstring that
says what the module owns. Standard library only — adding a dependency is a design decision,
not an implementation detail, so raise it first. Keep modules single-purpose: the seams are
the point of the extraction.

## Testing Guidelines

`unittest` from the standard library. Name tests `test_*.py` inside `tests/`, mirroring the
module under test. Fixtures in `tests/fixtures/` are **synthetic and must stay that way** —
never paste real logs, real seat output, or real panel records into this repo. Add regression
coverage for behavioral changes, and cover the legacy/malformed shapes too: the store folds
amendments and repairs truncated appends, so its edge cases are the ones that bite.

## Commit & Pull Request Guidelines

Work on a branch in its own worktree and land it by pull request against `main`; that is
the superpowers lifecycle the maintainer runs, and driver-mode branches follow the same
path. Commit summaries are lowercase with a `feat:` / `fix:` / `test:` / `docs:` prefix. Keep
commits focused. Docs carry a dated preamble ("Written 2026-08-26 …") and name specific tool versions —
when you change a doc, update its date and note what changed since.

## Security & Configuration

**The privacy firewall is the hard constraint.** No personal paths, secrets layout, private
data sources, or employer/client/project references may land in this repo. Migrating files from
the private implementation is forbidden; anything that lands here is a clean-room rewrite. The
existing docs describe the reference setup generically for exactly this reason — keep it that
way, and check your diff before committing.

Two more that shape every change: **no preview or stealth model ids in interfaces** (models are
a one-line config detail and they get retired), and **markdown in git is the only canonical
store** — search indexes are derived, disposable caches.

## Agent-Specific Instructions

Read `CLAUDE.md` before substantive work. It and this file are complementary: this file covers
structure, commands, and conventions; `CLAUDE.md` carries the design rationale and the
constraints that shape every change. Where they ever disagree, `CLAUDE.md` wins.

Two rules worth stating twice, because violating them produces confident-looking garbage rather
than an error: **read-only seats are load-bearing** — verify a seat by attacking it (ask it to
write a file, on fresh *and* resumed sessions), never by reading vendor docs — and **blind first
rounds and claim-only relays are non-negotiable**, since violating either yields an echo chamber
instead of signal. Verification verdicts come from the moderator's own check against the code,
never from a model's self-report.
