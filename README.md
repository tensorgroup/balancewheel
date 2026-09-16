# Balancewheel

**Moderated multi-model agent collaboration, with the receipts to tune it.**

In a mechanical watch, the escapement regulates — and the *balance wheel* is where
opposing forces oscillate into a steady beat. Balancewheel applies that idea to AI
coding agents: multiple frontier models from different vendors review, plan, and
debate the same work as **peer seats**, a moderator holds them to evidence, and every
dispute is **logged and scored** so seat assignments are tuned by win rates instead of
vibes.

Sibling project to [OpenEscapement](https://github.com/tensorgroup/openescapement):
esc *ships* policy into a repo as a signed rule pack; balancewheel is the collaboration
pattern that *generates and justifies* the policy. The two install at different levels
and never touch the same file; see [Working with OpenEscapement](#working-with-openescapement).

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

## What gets installed, and where

Nothing is installed system-wide and nothing runs as a service. A setup edits or creates
a handful of files under your home directory, and `setup/paths.txt` is the single list of
them — the backup and restore scripts read the same file. Credentials are on purpose not
on it: the setup reads them and never writes them, and a backup that copies tokens around
is a liability. In the reference setup the paths are:

| Path | What lives there | Setup does |
|---|---|---|
| `~/.config/balancewheel/config.json` | seats, state dir, watches | creates from `balancewheel.example.json` (a setup built before `metrics/` was extracted may still have these paths built into its own scripts instead) |
| `~/.claude/CLAUDE.md`, `~/.claude/settings.json` | moderator rules, permissions | appends / edits |
| `~/.claude/commands/`, `~/.claude/skills/` | the panel and seat slash commands | creates files |
| `~/.claude/scripts/` | seat wrappers, guards, driver launcher | creates files |
| `~/.claude/state/` | metrics JSONL, session tables, seat agent dirs, dashboard | creates; grows; not backed up (it is the setup's own output) |
| `~/.codex/config.toml`, `~/.codex/review.config.toml` | default profile, read-only review profile | edits / creates |
| `~/.pi/agent/settings.json`, `~/.pi/agent/models.json` | pi defaults and model pins | edits — *only if pi runs from its default agent dir; the reference setup gives each pi seat its own agent dir under the state dir, so these stay untouched there* |
| `~/.config/git/ignore` | tool-litter patterns | appends |
| `~/.codex/auth.json`, `~/.pi/agent/auth.json`, your secrets file | credentials | reads only; never written, never backed up |

Everything on that list is under your home directory. A repo's own instruction files
(`CLAUDE.md`, `AGENTS.md`, `.claude/skills/`) are not on it: the setup never writes them,
and that is the layer [OpenEscapement](#working-with-openescapement) governs.

**Dependencies** (install and log in yourself; the setup does not do it for you):

- A primary agent: the reference setup uses [Claude Code](https://code.claude.com) with the
  superpowers plugin.
- One or more seat harnesses: [Codex CLI](https://github.com/openai/codex) on a ChatGPT
  subscription, and/or [pi](https://www.npmjs.com/package/@earendil-works/pi-coding-agent)
  on Node 22.19 or newer (pi can also use the ChatGPT subscription; a Claude subscription
  through pi bills per token — see the rebuild guide).
- An OpenRouter key if you want a cheap metered seat.
- Python 3.9+ (standard library only) for `metrics/` and the tests; git and `gh` for the
  branch-and-PR flow; `sandbox-exec` (ships with macOS) if pi is used as a driver;
  shellcheck optional, for the wrappers.

**Back up first, restore if you want out:**

```sh
setup/backup.sh              # copies every existing listed path to ~/.balancewheel/backups/<UTC stamp>/
setup/backup.sh --dry-run    # shows what would be copied
setup/restore.sh --list      # backups on disk and what each holds
setup/restore.sh             # puts the latest backup back (after backing up the current state first)
setup/restore.sh <stamp>     # a specific one; --dry-run shows the plan
```

The backup reports listed paths that do not exist as "absent"; that is normal and means
the setup either has not created them yet or, as above, keeps that state elsewhere.

A restore is reversible because it snapshots the current state before touching anything.
It only restores paths that existed when the backup was taken; a file the setup *created*
is reported and left for you to delete, so nothing disappears that the script did not
first copy. Set `BALANCEWHEEL_BACKUP_ROOT` to keep backups elsewhere.

## Getting started

You need: a primary coding agent you already use, at least one other vendor's CLI agent
on a subscription or API key you are willing to spend, and a repo to review. Read
[docs/reference-implementation.md](docs/reference-implementation.md) once, then let your
primary agent do the assembly. Paste this into it, from the root of this repo:

```text
Set up a balancewheel review panel for me.

0. Run setup/backup.sh first and show me the backup directory it printed; it copies
   every file in setup/paths.txt that exists before you change anything, so
   setup/restore.sh can put my originals back.
1. A backup is not a merge. Before writing anything, read every file on
   setup/paths.txt that already exists (my user-level instruction file, settings, the
   Codex and pi configs, the git ignore) and give me a merge plan, section by section:
   keep as is, merge into the new rules, or drop, with a reason for each. Call out
   anything that contradicts the panel rules (a seat allowed to write, a review that
   skips verification, a model pinned by a preview id) and recommend against keeping
   it. Wait for my answer. Then add the balancewheel rules as one clearly delimited
   block appended to my instruction file, never by rewriting what was there, and apply
   only the merges I approved. Same for the Codex and pi configs: existing profiles
   stay, the read-only review profile is added beside them.
2. If my primary agent is Claude Code and the superpowers plugin is not installed,
   install it (`/plugin install superpowers@claude-plugins-official`) — the panel checkpoints attach to its
   brainstorming, writing-plans, requesting-code-review and systematic-debugging skills,
   and its worktree-plus-PR flow is what drivers assume.
3. Read README.md, docs/architecture.md, docs/reference-implementation.md and
   docs/roadmap.md in full. The two rules that are never relaxed: seats are enforced
   read-only and verified by attack; first rounds are blind and only disputed claims
   are relayed.
4. Ask me which vendor CLIs I have installed and logged in (for example Codex CLI, pi,
   Gemini CLI), which subscription or key each one uses, and which model each should run.
   Warn me if a route bills per token behind a subscription login — the rebuild guide's
   driver-mode section explains which ones do.
5. For each seat, write a wrapper that implements the interface in the guide's "Seat
   wrappers" section: new or resumed session, per-directory session persistence,
   read-only enforcement in every layer the guide lists for that CLI, a private scratch
   TMPDIR, a wall-clock cap, and a `usage` record appended via metrics/log.py. Pin the
   model in exactly one config line per seat.
6. Copy balancewheel.example.json to my config location, fill in the seats, and run
   `python3 metrics/log.py config` to validate it.
7. Run the verification checklist in the guide against every seat, fresh and resumed,
   and show me the evidence (the refusal text and the unchanged directory), not the
   model's own description of what it did.
8. Write me a moderator instruction file for the panel protocol (shared brief, blind
   round, dispute relay, substance bar, verification, the logging step) and run one
   panel on a file with two planted bugs so I can see the whole loop, including the
   record it appends and `python3 metrics/dashboard.py` rendering it.
9. If a repo I work in has an `.escapement/` directory, it is governed by OpenEscapement:
   read the managed block in its instruction files and show me every subject where a
   pack rule overlaps a moderator rule you wrote (which model reviews, what a review must
   cover). `esc status` tells you the block is in sync, not what it says. The pack's
   rule wins in that repo; do not edit the managed block to make them agree.
10. Keep a list of everything these docs did not tell you. I will send it upstream.
```

Nothing in this repo assumes a particular vendor. The reference setup names its own
choices so you can see a working combination, and its history shows why the model
names live in config: the first seat model was retired by its provider within a week.

### Starting a new project

The setup above is once per machine. Each new repo then needs three things: its own
instruction files, its OpenEscapement governance, and a check that the seats work in
that directory. Paste this into your primary agent from the root of the new project
(an empty directory is fine; fill in the two paths on the first line):

```text
Set up this directory as a new project that uses balancewheel and OpenEscapement.
Balancewheel is checked out at <path>, OpenEscapement at <path>; my rule-pack repo is
<git url or "none yet">. The project: <what it is, the language and stack, how it
builds and tests, one paragraph>.

0. Confirm the machine setup exists before touching this directory: the balancewheel
   config validates (`python3 <balancewheel path>/metrics/log.py config`), the seat
   wrappers my user-level instruction file names are on disk, and `esc` is installed.
   If any is missing, stop and tell me; the once-per-machine setup comes first.
1. Initialize git if needed, with `main` as the default branch and one empty initial
   commit, and add a `.gitignore` for the stack above. Ask me whether to create the
   GitHub repo now (`gh repo create`, private by default); if not, step 7 ends at a
   local branch.
2. Write `AGENTS.md` (what the project is, how to build and test, branch and PR
   conventions, commit prefix) and a `CLAUDE.md` that imports it with `@AGENTS.md`,
   so every agent reads one source. Keep the panel and seat rules out of these files:
   they are user-level, already in place, and the same for every repo. If this
   directory already has instruction files, do not overwrite them: read them, give me
   a section-by-section merge plan (keep, merge, drop, with a reason each, and a
   recommendation against anything that contradicts the rules above), and wait for my
   answer before writing. esc handles the rest in step 3: everything outside its
   managed block is preserved and reported as a local amendment.
3. Run `esc init --yes`. It detects the two files from step 2 and pre-fills `targets`
   with exactly those, so edit `.escapement/config.yaml` to `targets: [agents, skills,
   mcp]`: one managed block, in the file the other imports, and the pack's skills and
   MCP servers still render. (The marker offer never appears here: the files are
   uncommitted and the run is non-interactive, so the block lands at the top at first
   sync, which is what we want.) Point the config at my pack repo, a git source with a
   `ref` and a trusted signer. If I have none yet, copy `examples/packs/acme-org` from
   the esc checkout into a pack repo of my own next to this one, and before the first
   sync strip it to my rules: its example rules, paved-path pointers, skill, and MCP
   server entry belong to a fictional org, and a sync would install them as policy.
   Point the config at it as a local path with `trust: unsigned` and no ref, as esc's
   `examples/governed-service` config does; it serves until it has a signed tag or an
   org pack exists. Then `esc sync` and `esc status --check`. Add the esc GitHub
   Action only once the pack is a git source: the Action installs esc and runs
   `esc status --check` in a bare checkout, where a local sibling path does not exist.
4. Compare the managed block with the moderator rules in my user-level instruction
   file and list every subject both speak to. The pack wins in this repo; if that is
   not what I want, the fix is a pack change, not an edit to the block.
5. Attack one seat in this directory with a prompt that asks it to create a file here,
   fresh (`seat.sh -d . "<prompt>"`) and then resumed (`seat.sh -r -d . "<prompt>"`).
   Show me the guard's refusal and the unchanged directory, not the model's own account
   of what it did. Sessions are per directory, so a new project is a new session for
   every seat.
6. Run one panel on the project brief above, at the design checkpoint, and show me the
   record it appended and `esc status` still clean.
7. Commit the scaffold on a branch and, if there is a remote, open the PR, the same as
   every later change.
8. Keep a list of everything these docs did not tell you. I will send it upstream.
```

Once this has been run a few times, the mechanical steps (1, 3, 5) are candidates for
a `setup/new-project.sh`; the judgment steps (2, 4, 6) stay with the agent.

## Using it

- **Work the way you already do, on branches, by pull request.** The reference setup runs
  the [superpowers](https://github.com/obra/superpowers) skill lifecycle in Claude Code:
  brainstorm → worktree → written plan → execute → verify → review → finish the branch. We
  recommend it; it already puts every change in its own worktree and lands it by PR, which
  is the shape the panel and the drivers assume.
- **Review** happens at checkpoints of that lifecycle, not at the end: when a design
  converges, when a plan is written, at code review, and in debugging when the first
  hypothesis fails. Convene the panel, verify what survives, log the record.
- **Drive** when the moderator's budget is the constraint, when a second worker on a
  different subscription can take an independent, well-specified task in parallel, or
  for mechanical work. Start in your primary agent; reach for a driver from there. Match
  driver strength to how well-specified the work is: a weaker model is fine behind a
  written plan, tests, and the panel, and wrong for anything ambiguous. A driver's branch
  finishes like any other: pushed, opened as a PR, reviewed by the panel with the driver's
  vendor excluded.
- **Read the scoreboard** before changing roles. A seat is dropped or demoted on logged
  outcomes over a window, never on one bad review, and every change states what would
  earn the role back.

## Working with OpenEscapement

[OpenEscapement](https://github.com/tensorgroup/openescapement) (`esc`) and balancewheel
are two layers of the same idea: rules that agents read every session. They divide the
work by *where the rule lives*.

| | Balancewheel | OpenEscapement |
|---|---|---|
| **Governs** | how *your* agent collaborates: moderator rules, seats, drivers, the scoreboard | what an *organization* requires in a repo: policy, paved paths, skills, MCP servers |
| **Writes to** | your home directory only: the configuration in `setup/paths.txt`, plus its state directory and backups | the repo: a managed block in `CLAUDE.md` / `AGENTS.md` / `GEMINI.md`, `GOVERNANCE.md`, `.claude/skills/`, `.mcp.json`, and its own `.escapement/` |
| **Installed** | once per machine | once per repo (`esc init`, `esc sync`), from a signed pack the org maintains |
| **Changes by** | a moderator instruction edit that cites the scoreboard | a pack version bump, reviewed as a diff in a PR |

They never write the same file. esc never writes your agent's configuration: nothing
under `~/.claude/`, no wrapper, no credential. What it does keep under your home
directory is its own: a pack cache in the OS cache directory and, if you run its portal,
that data under `~/.escapement/server`. Its one look at agent configuration is a read,
when `esc status` notices a pack-provided skill whose name also exists under
`~/.claude/skills` and reports it as an informational duplicate. Balancewheel's setup
never edits a repo's instruction files.

**One, the other, or both.** Each works alone; together they close a loop.

- *Balancewheel only.* You want a review panel and a scoreboard for your own agent, and
  there is no org policy to ship. Nothing here needs esc: the moderator rules live in
  your user-level instruction file and go with you to every repo.
- *OpenEscapement only.* An org wants its rules in every repo's instruction files,
  signed and drift-checked. Nothing in esc needs a panel; its model packs carry model
  guidance whoever wrote it, and a repo can be governed before anyone has a seat.
- *Both.* You run the panel, and the policy it justifies (which model holds which seat,
  on what evidence) has to reach more than your machine. The log is the evidence; the
  pack is the delivery. esc's `examples/packs/` already carries model packs for
  Anthropic, OpenAI, and Z.ai, the vendors behind the reference setup's seats, each
  status with its reason and, where it departs from the vendor's own default, the
  condition that would reverse it. A `model-seats` pack in that shape
  is the planned `packs/` output here.

**Install balancewheel first, then esc per repo.** That is the recommended order, not a
dependency: neither tool reads the other's state. Balancewheel is the working
environment; esc is what each repo asks of whoever works in it. Your agent reads both the user-level file and the repo's file, and
where a pack rule and a moderator rule disagree on the same subject (which model reviews,
what a review must cover), the pack's rule is the org's call for that repo. Reconcile by
comparing your rule with the managed block after `esc sync`, never by editing the block:
esc reports the edit as drift, `esc status --check` fails the build, and `esc sync`
declines to overwrite the edit until someone forces it. Adjust your own rule, or open a
PR against the pack.

**The hand-off.** Balancewheel's log is the evidence a seat policy stands on, and a rule
pack is the form that policy takes once it applies to more than one person. The planned
`packs/` directory exports seat policy in esc's rule-pack shape (roles per target kind,
the substance bar, the re-promotion criteria), with the dashboard numbers cited in the
pack's changelog and the version bump reviewed like any policy change. esc's
`anthropic-models` example pack is that shape for one vendor's model guidance; a
`model-seats` pack is planned, not shipped. Once a pack carries the panel protocol as a
skill, keep the pack's copy in each repo and drop your user-level one, so the version
agents run is the signed one.

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
- **Run the tests** from the repo root: `python3 -m unittest discover -s tests -v`. They
  cover `metrics/` and exercise the backup and restore scripts against a temporary home.
