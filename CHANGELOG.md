# Changelog

**What a version means here.** Balancewheel ships no binary and nothing to install — the
install is a clone plus the setup prompt in the README. So a version is not a download; it
is a **contract**: the `metrics/` config and log schemas, the `setup/paths.txt` map, and the
setup prompt that assembles a panel. Pin one and you can say what your log was written
against; upgrade and this file says what moved under you.

Dates are the last commit in the release, not a publication date. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project uses
[semantic versioning](https://semver.org/) against the interfaces listed under each release,
not against the prose.

## [Unreleased]

### Added

- **Plan keys for Orca and probes** (`metrics/config.py`, `balancewheel.example.json`): config
  only, no schema bump. `quota` gains `"orca"` (read the plan's usage from Orca; requires
  `orca_provider`). Plans gain `orca_provider` (an Orca provider name, refused with `quota:
  "none"`), `orca_windows` (window id `fableWeekly` or `bucket:<name>` mapped to lead or seat
  names, checked once both have loaded), `probe` (`agy` or `openrouter`; derived from `vendor`
  `google`/`openrouter` when `quota` is `"probe"`, else `null`), and `key_env` (default
  `OPENROUTER_API_KEY`, only with probe `openrouter`). They are optional additions to an
  existing object, so schema 2 and 3 files read them and files without them resolve as before.
  A probe plan with no derivable probe still loads and is reported `unknown` later.
- **`bw start [path] [--dry-run] [--no-update] [--no-verify]`** (`bin/bw`, `lead/start.py`): a
  new command. It resolves the project and refuses (exit 2) when bw itself lies inside the
  target worktree or its common git dir; updates every configured tool in parallel; re-verifies
  each lead runtime's containment and guard keys on a pool, one worker per runtime, each
  starting as its tool's update finishes (at once with `--no-update`; changed keys stay
  `pending` with `--no-verify` or when the version after the update is unreadable); prints the
  quota of every plan the profile touches and the lead it would start; then opens the worktree
  in an Orca pane (`orca terminal create`) when Orca runs, else in a Terminal tab through
  `state_dir/start/<worktree-key>.command` (0700), and prints the command to run (exit 1) when
  neither works; a running Orca whose `orca terminal create` fails falls back to the Terminal
  tab. A worktree that already has a lead is not opened again. Results go to
  `state_dir/start.json` (`ts`, `project`, `tools`, `verify`, `orca_version`), which `bw status`
  reads for its `last start:` line. `--dry-run` runs only git and `orca status` and writes
  nothing. Containment and guard passes are now recorded under an flock on `<store>.lock`, so
  concurrent re-verifies never drop one. Docs: the README, the Orca
  guide, and the 2026-10-24 spec (amendments A3–A6) describe `bw start`; the spec's cc-switch
  version reads 4.0.20.
- **`bw probe [plan...]`** (`bin/bw`, `lead/probes.py`): a new command. It reads quota now for
  the named plans (all plans without one) and prints one line per plan: the arbitrated summary
  with its source and age, plus any notice. One `orca account list` is shared by every
  Orca-mapped plan; `quota: "probe"` plans are read by their probe in parallel, each written to
  `state_dir/quota/<plan>.probe.json`, then expired caps are cleared. Probe `openrouter` reads
  `GET https://openrouter.ai/api/v1/key` with the key from the plan's `key_env` (unset → unknown
  `key env <NAME> unset`; non-200 → `HTTP <n>`; a redirect is refused, not followed; any other
  failure is named by its exception type, never described), and with `budget_usd_month > 0`
  gives a `budget` window: this UTC month's spend (`usage_monthly`) against the budget, reset on
  the 1st. Without `usage_monthly` it falls back to `usage` less the month's first reading
  (`state_dir/probes/openrouter-<plan>-<YYYY-MM>.json`), and only when that baseline was taken
  by 00:00 UTC on the 1st; otherwise no budget window and the note `monthly spend unknown before
  <time>`. A key credit limit is shown under `limits`, never a window. Probe `agy` is unknown
  (`agy /usage: awaiting Task G`) and runs nothing. `bw start` (step 4) and `bw lead` (before its
  first decision and at the session boundary, never in the poll loop) run the same refresh.
- **Per-source quota snapshots and an arbiter** (`lead/quota.py`, `lead/orca.py`,
  `lead/rotation.py`, `lead/runner.py`, `lead/status.py`). Besides the statusline's
  `quota/<plan>.json`, each source writes `quota/<plan>.<source>.json` (`probe`, `orca`); an
  unknown snapshot carries `last_known_at`. `quota.read_quota(state, config)` now arbitrates per
  plan: windows from the first known, fresh source in the plan's order (`statusline`; `probe`
  then `orca`; `orca`), where `probe` and `orca` readings older than twice `probe_every_min`,
  dated more than 300 s ahead, or malformed are unknown; pools (`orca_windows`) from the
  freshest known, non-stale Orca snapshot whichever source won; Orca's windows on a statusline
  plan only as `cross_check`, display-only. `rotation.decide` does not start a lead named in a
  full pool at `start` or `boundary` and skips it as a replacement when a cap forces a switch;
  a pool never moves a running lead. Caps past their `resets_at` are cleared by bw once a fresh
  known reading observed at or after that time exists, else `cap_expiry_hours` later. The Orca
  adapter checks each mapped provider's shape on its own and re-checks on a version change; a
  malformed mapped pool is dropped with a note. `bw lead` re-reads Orca's local cache in its
  poll loop (synchronous, 10 s cap, display only; a failure never stops the lead). **Log
  content change:** a `lead` event's `quota` is the arbitrated reading and no longer carries
  `cross_check`. Docs: the README, the Orca guide, `docs/stack.md`, the 2026-10-24 spec
  (amendments A7–A10) and the guard spec (decision 7 superseded) describe it as built.
- **Orca and cc-switch checks in `bw status`** (`bin/bw`, `lead/status.py`, `lead/orca.py`,
  `lead/sqlite_ro.py`): `bw status` adds `orca:` and `cc-switch:` lines. Orca's are whether
  it runs (`orca status --json`; installed but not running gets its own line) and its
  active profile's settings: agent permissions not Manual (the default or a per-agent
  override), telemetry on, or managed Claude/Codex accounts warn. bw reads Orca's profile
  database only from a copy in the state dir's `sealed/` (0700, denied to every sandbox),
  removed after the read. `BW_ORCA_SUPPORT` overrides Orca's support dir. `sealed/` is live
  state and stays out of `setup/paths.txt`.
- **Config `tools.*` keys, and config warnings** (`metrics/config.py`, `lead/updates.py`,
  `bin/bw`): a tool takes `update` and `version` (argv lists), `app` (an app bundle path; its
  `Info.plist` gives the version), `self_updating` (version only, never updated by bw),
  `runtime` (which runtime lock its update needs; defaults exist for claude, codex, agy, pi
  and kimi) and `timeout_s` (integer ≥ 1, default 600). A known key with a bad type is an
  error; an unknown key under `tools.<t>` is ignored and reported in the resolved config's
  new `warnings` list, which `bw` prints once on stderr. `lead/updates.py` reads versions and
  updates every installer in parallel, tools sharing one installer in turn, each under its
  runtime lock taken without blocking (a held lock skips the tool); a tool with an update
  command and no runtime is not updated. Additive and optional: no schema bump.
- **`bw setup cc-switch`, the cc-switch settings check, and `lead/proc.py`** (`bin/bw`,
  `lead/ccswitch.py`, `lead/checks.py`, `lead/proc.py`): cc-switch is adopted as the usage
  and cost source for the Usage view (docs/stack.md). `bw setup cc-switch` writes
  `~/.cc-switch/settings.json` (proxy, stack mode, failover, Codex history unification,
  launch on startup, and tool-update checks off; session auto-sync on) before the app's
  first launch, and refuses to overwrite a file that differs; it needs no config.
  `setup/paths.txt` gains `~/.cc-switch/settings.json` (the rest of `~/.cc-switch` stays
  out: it is the app's database and holds copies of credentials). `lead/proc.py` is the one
  shell-out helper later modules take as `run=`.
- **`bw guard` commands** (`bin/bw`, `lead/guard/`): `bw guard review [--profile] [--recent]
  [--sample] [--list]` (audit stops and sampled allows, marks recorded in `guard.jsonl`),
  `bw guard verify <runtime>` (the attack in `tests/attack/guard_attack.sh`; a pass is
  recorded in the state dir's `guard-verify.json`), `bw guard install|uninstall agy`, and
  `guard:` lines in `bw status`. agy's side refuses until its hook spike runs. `guard.jsonl`,
  `guard-verify.json`, and the per-worktree `guard/` directory are live state and stay out
  of `setup/paths.txt`. After upgrading, pi and Kimi leads need `bw sandbox verify` again
  (the adapter hash changed). Guarded pi leads (and the pi guard attack) run with
  `--no-extensions`, so pi's discovered extensions, including project-local ones in the
  worktree, stop loading for them; the guard client still loads through `-e`, and an
  extension you want back goes in the lead's `args` as `-e <path>`.
- **Log event `guard` and `session.autonomy`** (`lead/runner.py`, `metrics/store.py`): a `session`
  start now carries `autonomy: {requested, effective}` (`requested` is null unless the lead's config
  names it) and, for a guarded lead, `guard_version`. A guarded session adds one `guard` event
  before its `session` end: `{session_id, project, profile, lead, runtime, mode, guard_version,
  rules_version, calls, decisions: {allow, ask, block}, gray, unreachable, latency_ms: {p50, p95}}`,
  counts only, self-test call excluded. `store.load()` keeps `autonomy` and `guard_version` on
  sessions (absent: null) and returns `guards`. `bw lead` exits 7 when a guarded lead cannot start.
  Older logs load unchanged.
- **Config schema 3** (`metrics/config.py`): per-lead `autonomy` (`native`, `guard`, `bypass`,
  `manual`; checked against what each runtime supports), a `guard` block (`mode` per profile,
  `model`, `on_judge_failure` (default `allow`), `rules_on_native`), and per-profile `protected_branches` and
  `deploy_commands`. A lead without `autonomy` gets its runtime's default (claude-code `native`,
  codex `native`, agy `guard`, pi `guard`, kimi `manual`), and under schema 3 `args` may no longer
  carry permission flags (autonomy owns them). **Schema 3 moves Codex leads to
  `--approve-for-me` by default; writing `schema_version: 3` is the opt-in.** Version-1 and
  version-2 files load unchanged and keep today's argv: their leads resolve to a legacy autonomy
  (`custom` when `args` already carry a permission flag) and no flags are added. The new keys are
  rejected in a version-2 file with "(needs schema_version 3)"; `guard.mode: "acting"` and
  `guard.rules_on_native: true` are rejected until built. `balancewheel.example.json` is now
  schema 3, with the agy lead set to `manual` until its guard hook is verified.
- **Config schema 2** (`metrics/config.py`): `plans` (billing `plan`|`metered`, quota source,
  metered budget), `leads` (runtime, model, plan), `tools` (update argv), `profiles` (lead
  order, per-checkpoint seats with `required` and `min_seats`, depth, router mode),
  `projects` (path → profile), `default_profile`, `rotation`, `updates`, `cockpit`, `team`;
  seats gain `plan` and `fallback`. Version-1 files load unchanged as one implicit profile
  with no leads; the v2-only keys are rejected in a version-1 file. Leads name a `runtime`
  (the same names seats use), not the spec draft's `tool`.
- **`bin/bw` and the `lead/` package**: `bw lead` (one locked, job-controlled lead per
  worktree; switch on cap with a handoff; return after reset), `bw status`, `bw cap`,
  `bw sandbox verify`; `bin/bw-statusline` (Claude quota and resolved model from the status
  line, with chaining). `setup/paths.txt` gains the state dir's `leads.json` and
  `handoffs/` (pins and handoffs only).
- **`setup/paths.txt` covers the files Orca edits on first launch** (`~/.codex/hooks.json`,
  `~/.gemini/settings.json`, `~/.gemini/config/hooks.json`, `~/.kimi-code/config.toml`), so
  a backup taken before installing the recommended canvas can undo its hook injection.
  `docs/orca.md` is the integration guide.
- **Log events `session`, `lead`, `rating`, and `panel.moderator`** (`metrics/log.py`):
  `session --phase start|end` records a lead session (worktree key, lead, runtime, model and
  whether it was resolved, plan; `end` carries `exit|switch|lost`); `lead` records every lead
  change with from, to, reason, and the quota used to decide; `rating` is the user's
  good/bad for a session. A `panel` may carry `moderator` (lead, runtime, model, resolved,
  session). `store.load()` returns `sessions`, `leads`, `ratings`. Older logs load unchanged.

- **A decision-model pilot** (reference-implementation.md, "Decision model"): a System One
  model (Jev via OpenRouter by default; Kev, SemIf, OpenJev, Laya as self-hosted options)
  gives raise-only advice on executor tier, reasoning effort, and review depth per task, and
  scores review findings in shadow for later calibration against verified verdicts.
  Documented as a design for the harness to implement (no config schema change yet);
  decisions and agent dispatches are logged to one JSONL.
- **An optional `-b` flag on the seat wrapper interface** (`seat.sh [-b] [-r] [-d dir]
  "prompt"`), and a setup-prompt step 5 that asks for it: on a new session only, the wrapper
  prepends a pre-built, cached *seat brief* (instruction-file head, diff vs merge-base,
  untracked files, likely callers) and a reading rule, so a fresh seat reads the change
  instead of surveying the repository. Backward-compatible: a wrapper without `-b` still
  meets the interface. Rationale, including why resuming a session is not a substitute:
  `docs/architecture.md` (protocol step 1, lessons learned); build details and traps:
  the rebuild guide's "Seat wrappers" section.
- `setup/backup.sh` now covers **the metrics log**, resolved from the config's `metrics_log`
  rather than a fixed path. It is the one file a setup produces that cannot be regenerated,
  and it had no cover at all: excluded from `paths.txt` as "the setup's own output", and
  typically under a state directory a user's dotfiles repo ignores. The rest of the state
  directory stays excluded on purpose — dashboards and indexes rebuild from the log, and
  seat agent dirs hold credentials a backup must not copy.
- `~/.config/balancewheel/paths.local.txt` (or `$BALANCEWHEEL_LOCAL_PATHS`): your own paths,
  appended to the tracked list. A setup predating the config loader, or one keeping state
  somewhere unusual, can now be protected without editing a tracked file. Each backup stores
  the effective list it used, so restores inherit the additions.
- **The inbox** (`lead/inbox.py`, `lead/runner.py`, `bin/bw`): leads ask the user through
  files and bw alone owns the record. A lead publishes each question as one file in `BW_ASKS`
  (`state_dir/handoffs/<worktree-key>/asks/`: write `.tmp-<random>`, rename to `<random>.ask`;
  `lead/protocol/checkpoint.md` says how); bw ingests only `*.ask` regular files, without
  following links or blocking, at most 64 KiB each, text cut at 2000 characters, at most 50 asks
  per session (the rest deleted unread, with one notice), at session start, about once a second,
  and once more after the lead's group is gone — before a switch's handoff, which lists open
  items — always under the publishing session. Records go to `state_dir/inbox.jsonl` (0600):
  `ask` `{type, id, ts, project, worktree, from, session_id, text, status}` (id
  `sha1(name + content)[:8]`, so a re-ingest dedupes), `notice` `{type, id, ts, project, kind,
  from: "bw", text, status}` with `kind` one of `all-capped`, `pinned-capped`, `router-shadow`
  (no emitter yet), `guard-unreachable`, `inbox` (one open notice per kind and worktree), and
  `answer` `{type, id, ask_id, ts, from: "user", text}`; `ts` is epoch seconds. Answers reach
  the lead in `BW_INBOX` (`state_dir/inbox-view/<worktree-key>.md`), which only bw writes and
  which holds answers only (`@lead answer [id:<ask id>]` and the escaped answer), never ask text.
- **`bw inbox [--project] [--all]`** and **`bw answer <id> "<text>"`** (`bin/bw`): new commands.
  `bw inbox` lists open asks and notices (all worktrees; `--project`: this one; `--all`:
  answered too), lead text escaped. `bw answer` answers an ask or closes a notice (exit 1 for an
  unknown or already answered id) and rewrites that worktree's answers file. `bw status` gains
  an `inbox:` section with open items per worktree.
- **Log events `ask` and `answer`, and `lead` reason `override`** (`metrics/store.py`): `ask`
  `{type, id, project, session_id, from}` per ingested ask and `answer` `{type, id, ask_id}` per
  `bw answer`, both without text (the text lives only in `inbox.jsonl`); `store.load` returns
  them as `asks` and `answers` (empty for older logs). A `bw lead --use` that starts a different
  lead than the profile order would logs a `lead` event with reason `override` (`from` the
  order's pick, `null` when none could start).
- **`setup/paths.txt`** lists `~/.local/state/balancewheel/inbox.jsonl` (the user's asks and
  answers); `inbox-view/` is regenerated and stays out.

### Fixed

- **Runtime locks and verification output** (`lead/locks.py`, `lead/verify.py`): `bw lead`
  takes its shared runtime lock before the verification check, and bw keeps a second
  non-inherited shared lock so a lead cannot unlock or upgrade the inherited one; mid-session
  takes never block. Verification failure output now goes to stderr.

- **Lead sandboxes deny cc-switch's data, bw's sealed copies, and the canvas and cockpit
  ports; leads read their answers view** (`lead/sandbox.py`, `lead/runner.py`). Both backends
  deny reading `~/.cc-switch` (added to `SECRET_READS`) and `state_dir/sealed/`, and refuse
  connections, on any host, to 6768 (Orca's orcad), 15721 (cc-switch's proxy), 8765 (the cockpit's
  default) and `cockpit.port` when configured; `state_dir/inbox-view/` is read-granted and in
  no write grant. `python3 -m lead.sandbox run` takes `--deny-port N` (repeatable; default
  those ports). `setup/paths.txt` gains `~/.pi/agent/extensions` (Orca writes pi extensions
  there). The containment rules changed: pi and Kimi leads need `bw sandbox verify` again,
  and pi guard `bw guard verify pi` — run each once, after the next guard change lands.

- **`setup/restore.sh` could destroy the backup it was restoring from.** Backup directories
  are named by a one-second UTC stamp, and a restore takes a safety backup of the current
  state before writing. Run backup → damage → restore inside the same second and that safety
  copy landed in the *same directory* as the backup being restored, overwriting the good copy
  with the damaged state; the restore then reported success and put the damage back, with
  nothing left to recover from. A code comment had dismissed the shared-second case as
  harmless. `backup.sh` now suffixes a stamp whose second is taken (`…Z-2`) and never reuses
  a directory.
- **A second bare `restore.sh` restored the damage instead of undoing it.** "Most recent
  backup" included the safety copy the previous restore had just taken, so pressing undo
  twice cemented the damage while reporting `restored N path(s)`. Safety copies are now
  marked, skipped when choosing a default, labelled in `--list`, and still restorable by
  explicit timestamp — which is how you undo a restore.
- `log.py panel` and `log.py amend` read their record as JSON on stdin, which was
  documented nowhere — not in `--help`, not in any doc a new user reads — while the
  setup prompt's step 8 tells their agent to log a panel. Both subcommands now describe
  the stdin contract, `panel --help` carries the full record shape and a worked example,
  and the top-level help labels both. Found by cold-starting the setup from a fresh
  clone: building a valid record took four rejections, guided only by error messages.
- A regression test now validates that worked example against the shipped
  `balancewheel.example.json`. The first version of it used placeholder seat ids no
  config contains, so every copy-paste would have been rejected.

## [0.1.0] — 2026-09-16

First tagged version. Everything below arrived across 31 commits from 2026-08-25, none of it
previously released, so this is a description of what 0.1.0 *is* rather than a diff against
an earlier version.

### Interfaces this version pins

- **Config `schema_version: 1`.** `metrics/config.py` reads 1 and refuses anything else by
  design, with key-path errors that name the offending field.
- **The metrics JSONL record shapes** — `usage`, `panel`, `watch-update`, `panel-amend`.
  `metrics/store.py` is the only reader and the only appender; nothing else parses raw
  lines. It folds amendments and normalises legacy shapes on load, so logs written by a
  pre-0.1.0 dogfooding setup stay readable.
- **`setup/paths.txt`** — the single list of home-directory paths a setup touches, read by
  both the backup and restore scripts.
- **The seat wrapper interface**, `seat.sh [-r] [-d dir] "prompt"`, as specified in the
  reference implementation guide. Documented here, generated by the user's own agent.

### The pattern

- Architecture: the three-peers/one-moderator topology, the panel protocol (one shared
  brief, blind parallel round, cross-examination of disputed claims only, a substance bar,
  moderator verification against the code), the metrics loop, and the lessons that cost the
  most to learn.
- Reference implementation as a rebuild guide — tools and models named, the settings that
  took debugging, the traps, and a verification checklist.
- Seat policy: same-vendor blind seats, on-demand seats with stated re-promotion criteria,
  two-round cross-examination cap, and per-seat wall-clock caps with a deep tier. A seat that
  does not return is logged absent and the panel proceeds; a timeout never changes the roster.
- Driver mode, separated from the read-only seat guarantee: seats cannot write, drivers are
  contained by a worktree instead, and driver runs are not scored.
- The OpenEscapement boundary — balancewheel generates and justifies seat policy, esc ships
  it as a signed rule pack. The two never write the same file.

### `metrics/`

- `config.py` — config loader with key-path errors and the annotated example config.
- `store.py` — append-only JSONL store: locked appends, amendment folding, legacy
  normalisation, duplicate-id dedupe, and repair of truncated appends.
- `watches.py` — roster watches as pure functions with timestamp-free ids (panel bloat,
  skimmed verification, redundant seat).
- `log.py` — the logger CLI: `config`, `usage`, `panel`, `check`, `watches`, `resolve`,
  `amend`. Unique-catch counts are computed, not self-reported.
- `dashboard.py` — trend dashboard: current-state tiles, roster meters, stacked counts, and a
  date-axis timeline.
- 76 tests, standard-library `unittest`, synthetic fixtures only.

### `setup/`

- `backup.sh` / `restore.sh` over `paths.txt`. A restore snapshots current state before
  touching anything, reports listed paths that are absent as normal, and leaves files the
  setup *created* for you to delete rather than deleting them itself. Credentials are
  deliberately not on the path list: the setup reads them and never writes them.
- The README's install map — what gets written, where, and by whom.

### Docs and conventions

- `README.md` carries the whole plan and the paste-in setup prompt a new user's agent follows.
- `AGENTS.md` alongside `CLAUDE.md`, complementary rather than duplicated.
- `docs/handoffs/` — point-in-time session records, never the source of truth.
- Superpowers named as the workflow skeleton the panel checkpoints and drivers assume.

### Known limits

- `seats/`, `panel/` and `packs/` are planned directories, not code. The extraction criteria
  in `docs/roadmap.md` gate them; `metrics/` and `setup/` landed ahead of that gate on
  purpose, as the metrics loop and install hygiene respectively.
- Driver mode is documented, not implemented here — the launcher is part of the private
  reference setup.
- Extraction criterion 4, "a second user has run the setup from the README alone", is not
  yet met. Until it is, the setup prompt is the least-tested interface in this release.
- The `model-seats` pack in esc is written by hand from this repo's seat policy. The
  regeneration step that would derive it from the log is what `packs/` still owes.

[Unreleased]: https://github.com/tensorgroup/balancewheel/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/tensorgroup/balancewheel/releases/tag/v0.1.0
