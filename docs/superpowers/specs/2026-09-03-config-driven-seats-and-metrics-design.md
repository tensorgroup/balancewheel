# Config-driven seats, setup, and a trend dashboard — design

*Written 2026-09-03; revised the same day after a three-seat design review (the
review's verified findings are folded in below, not appended). Balancewheel's first
code: a seat roster declared in one config file, one setup script that provisions and
attack-verifies every seat, per-runtime adapters behind a uniform `seat` command, and
the metrics loop rewritten clean-room with a dashboard that shows change over time.
Roadmap criterion 4 (a second user runs setup from README alone) is exercised by a
real second user during this work.*

## Goals

1. **A second user can replicate the panel from README alone.** They list the models
   they want and the CLI that runs each one; setup does the rest.
2. **Adding a seat is a config change**, verified by the first-party dogfood: a
   Google seat (Antigravity CLI, `agy`) is added to the author's roster purely through
   config, to find out whether it catches anything the other seats miss.
3. **The dashboard explains what is happening** — not only totals but how each seat
   and the panel have changed, and what is open right now.
4. **Bake in the economics**: each seat runs through its vendor's own CLI because a
   subscription (Claude, ChatGPT, Kimi membership, Google account) is a far better
   deal than metered API access for repo-aware agent work. This is expected to stay
   true for a while; the design commits to it.

## Constraints (from `CLAUDE.md`, restated where they bite)

- **Clean-room.** Nothing is copied from the author's private tooling; every file here
  is written fresh from this design. No personal paths, project names, or secrets
  layout anywhere in the repo — including test fixtures, which use synthetic paths and
  placeholder model ids.
- **Read-only seats, verified by attack** on fresh and resumed sessions, per runtime.
  The threat model is stated honestly per runtime (below): OS-level enforcement where
  the vendor offers it, tool-permission denies where it does not, and a shell guard
  only ever as a second layer.
- **Model ids are config, never interface.** Seat *names* are the user's; runtimes are
  the interface.
- **Portable runtime**: `/bin/bash` 3.2 and Python 3.9+ standard library only. No
  `tomllib` (3.11+), so the config is JSON. No `jq`, no coreutils `timeout` (not
  stock on macOS). macOS is the v1 platform; Linux is best-effort and untested.
- **Blind first rounds, claim-only relays, moderator-verified verdicts** — unchanged
  protocol; the commands carry it.
- **Seat-owned homes, never the user's global config.** Wherever a runtime lets a
  process point at its own home/profile/settings, the seat gets one. Setup never
  mutates a user's primary config file.

## Repo layout

```
balancewheel/
  README.md                       pitch, "why CLIs not APIs", quickstart for a new user
  balancewheel.example.json       copy to ~/.config/balancewheel/config.json and edit
  bin/
    setup                         python3: validate, provision, verify, report
    seat                          bash: uniform seat invocation + usage logging
  seats/
    runtimes/                     one adapter per CLI, same interface
      claude-code.sh  codex.sh  pi.sh  kimi.sh  agy.sh
    guards/
      readonly-guard.py           second-layer shell guard (Claude hook)
      pi-readonly-guard.ts        pi `tool_call` extension: in-process block, same policy
      cases.tsv                   shared allow/deny case table for all guards
    prompts/
      reviewer.md                 the seat system prompt (shared across runtimes)
  panel/
    commands/                     Claude Code slash commands, installed by setup
      panel-review.md  panel-stats.md  seat-review.md
  metrics/
    store.py  log.py  dashboard.py
  tests/
    test_store.py  test_log.py  test_dashboard.py  test_setup.py  test_seat_dry_run.sh
    test_guards.sh
  docs/
    architecture.md  reference-implementation.md  roadmap.md  seat-policy.md  (updated/new)
    superpowers/specs/            this file
```

## Config

`~/.config/balancewheel/config.json` (`BALANCEWHEEL_CONFIG` overrides the path):

```json
{
  "seats": {
    "sol":   {"runtime": "codex",       "model": "gpt-5.6-sol",               "effort": "high", "roles": ["review"]},
    "ox":    {"runtime": "pi",          "model": "openrouter/z-ai/glm-5.3-flash", "effort": "medium", "roles": ["plan", "review", "decision"],
              "provider": {"reasoning": true, "contextWindow": 1310720, "maxTokens": 131072}},
    "fable": {"runtime": "claude-code", "model": "claude-fable-5-1",                             "roles": ["design", "plan", "review", "debug", "decision"]},
    "gem":   {"runtime": "agy",         "model": "gemini-3.1-pro-high",                          "roles": ["review"]},
    "kimi":  {"runtime": "kimi",        "model": "kimi-code/k3",                                 "roles": ["plan", "review"], "enabled": false}
  },
  "state_dir": "~/.local/state/balancewheel",
  "metrics_log": "~/.local/state/balancewheel/metrics.jsonl",
  "commands_dir": "~/.claude/commands",
  "bin_dir": "~/.local/bin",
  "watches": {
    "bloat_total": 30, "bloat_per_seat": 8, "bloat_min_seats": 3,
    "skim_min_seats": 3, "skim_min_findings": 15,
    "redundant_min_panels": 5, "redundant_window_panels": 10
  }
}
```

Rules:

- `seats.<name>` — name is free text (`[a-z0-9-]+`), used as the metrics `seat` key
  and in the commands' roster. `runtime` ∈ {`claude-code`, `codex`, `pi`,
  `kimi`, `agy`}. `model` is passed verbatim to the runtime. `enabled` defaults to
  `true`.
- `effort` is optional; the mapping table is fixed and validated by setup:
  Codex → `model_reasoning_effort` (`low|medium|high`), pi → `--thinking`
  (`off|minimal|low|medium|high|xhigh`), Kimi → `[thinking].effort`
  (`low|high|max`). For agy the catalog model ids already carry effort
  (`gemini-3.1-pro-high`), so setup **rejects** `effort` on an agy seat rather than
  keeping two encodings of one knob. Claude Code → rejected too; a key that would do
  nothing is an error, not a silent no-op.
- `schema_version` (top level, integer, currently `1`) so a future migration can
  tell what it is reading.
- `roles` ⊆ {`design`, `plan`, `review`, `debug`, `decision`}; the panel command
  convenes the seats whose roles include the checkpoint. A seat with no roles is
  reachable only through `seat-review`.
- `args` — optional list of extra CLI arguments passed through to the runtime
  verbatim, so CLI churn can be absorbed without an adapter edit.
- `provider` — optional runtime-specific block. For pi it is the model entry setup
  writes into the seat's `models.json` (`reasoning`, `contextWindow`, `maxTokens`),
  needed for any model absent from pi's built-in catalog; the API key itself never
  lives here — pi reads it from the provider's environment variable (e.g.
  `OPENROUTER_API_KEY`) or its own `auth.json`.
- `policy` — optional free text: why this seat has these roles, the evidence cited,
  and the re-promotion criteria. `seat --list` prints it; `docs/seat-policy.md`
  explains the governance loop it feeds. Roles carry the effect; `policy` carries the
  justification the README promises.
- `metrics_log` may point at an existing log so history carries over. Setup reports
  seat names present in the log but absent from config (and vice versa).
- `watches` thresholds are the roster self-checks (below); `redundant_window_panels`
  is a count of panels, not days.
- Setup rejects unknown keys and unknown runtimes, naming the key path
  (`seats.gem.efort`); JSON syntax errors report the parser's line and column.

A second user's file has the same shape with different rows — e.g. `kimi` enabled and
`ox` absent. Nothing else changes. `balancewheel.example.json` is framed as
placeholder rows to change ("replace these with your seats"), not as the author's
roster, and its header comment says model ids are expected to churn.

## `bin/seat` — the uniform interface

```
seat <name> [-r] [-d <dir>] [-t <seconds>] [--diff <file>] [--checkpoint <role>] "<prompt>"
seat <name> --verify [-d <dir>]        # the attack test, used by setup
seat --list [--json]                    # enabled seats, runtime, model, roles, policy, verified
```

`seat` is bash, but it never parses or emits JSON itself: config loading, `seats.json`
checks, and event appends go through `metrics/log.py` (`log.py config`, `log.py usage`)
so a quote or backslash in a path cannot corrupt the log. `--list --json` is the
stable machine shape the panel commands read; the default is a human table.

Responsibilities (all runtimes):

1. Load config; refuse unknown or disabled seats; **refuse seats whose
   `state_dir/seats.json` entry is missing, `verified: false`, or records a runtime
   binary version different from the one now on `PATH`** ("re-run setup" message).
2. Absolutize `-d` (default cwd); worktrees are fine. **Scan the target for
   runtime extension points the reviewed repo controls** — `.pi/`,
   `.claude/settings*.json`, `.mcp.json`, `.kimi-code/`, `.codex/`, `.gemini/`;
   `AGENTS.md`-style instruction files are fine — and refuse to run the affected
   runtime's seat when a code-loading one is present (pi `.pi/extensions`, Kimi
   `.kimi-code/mcp.json`, Claude `.mcp.json` or project hooks), printing which file
   and why. `--allow-repo-extensions` overrides for repos the user trusts. (pi
   already declines project resources headless with `--no-extensions` and
   `defaultProjectTrust: never`; the refusal is belt and braces.)
3. Export `TMPDIR` to a fresh private scratch dir under the *system* temp root
   (`python3 -c 'import tempfile; print(tempfile.gettempdir())'` with `TMPDIR`
   unset), never derived from the inherited `TMPDIR`, which some sandboxes point at
   the checkout. Removed on exit.
4. `--diff <file>`: the moderator materialises the diff/target once and every seat
   receives the same path in its prompt, so seats need no shell to see the change.
5. Dispatch to `seats/runtimes/<runtime>.sh` with the seat's config as environment
   (`BW_SEAT`, `BW_MODEL`, `BW_EFFORT`, `BW_ARGS`, `BW_HOME`, `BW_DIR`, `BW_RESUME`,
   `BW_SESSION`, `BW_TIMEOUT`).
6. **Timeout in-process**: the adapter is run as a child in its own process group;
   `seat` kills the group on expiry (default 900 s) and records `ok: false,
   reason: timeout`. No coreutils `timeout`.
7. Persist the session id per `(seat, dir)` in `state_dir/sessions/<seat>.tsv`,
   **captured from that invocation's own output** (never "newest session in this
   directory", which two seats running in parallel would both claim).
8. Append a `usage` event (`seat`, `model`, `mode` new|resume, `dir`, `duration_s`,
   `ok`) with an exclusive file lock and a single `write()`.
9. Exit with the runtime's status. Response on stdout, diagnostics on stderr.
10. `BW_DRY_RUN=1` prints the exact command instead of running it, **and** performs
    steps 1–3 for real so the tests cover the refusal paths and the TMPDIR contract.

## Runtime adapters

Each adapter is one shell file implementing `run_new`, `run_resume`, `session_id`,
and `verify`. Mechanics per runtime as verified against the vendor docs and local
binaries on 2026-09-03 (versions noted; setup re-verifies on upgrade):

| Runtime | Headless / model | Resume | Read-only enforcement (the honest threat model) | Session id |
|---|---|---|---|---|
| `codex` (0.153) | `codex exec -p <profile>`; profile file `~/.codex/balancewheel-<seat>.config.toml` with `model`, `model_reasoning_effort`, `approval_policy="never"`, `sandbox_mode="read-only"` | `codex exec resume <id>` with `-c` overrides (resume rejects `-p`/`-C`; adapter `cd`s) | **OS sandbox** (read-only filesystem). Strongest seat. | `session id:` line on stderr |
| `agy` (1.1) | `agy -p --output-format json --model --effort --sandbox` | `--conversation <id>` | **OS sandbox with the target mounted read-only.** `--mode plan` is only a prompt prefix (the modes doc says so) and is not used for enforcement. The seat runs with an isolated `HOME` (`state_dir/agy-<seat>/home`, with the vendor's credential store symlinked from the user's home, or Keychain-backed if that is where the CLI keeps it — setup detects which) and its own `settings.json`: `enableTerminalSandbox: true`; `permissions.allow` = `read_file(<target>)` (which the sandbox mounts **read-only**) plus token-anchored read-only commands (`command(git (status\|log\|diff\|show\|blame\|rev-parse\|ls-files))`, `command((ls\|cat\|head\|tail\|wc\|grep\|rg\|find))`); `permissions.deny` = `write_file(*)`, `unsandboxed(*)`, `mcp(*)`, `execute_url(*)`, `read_url(*)`. The target is deliberately **not** a trusted workspace, so nothing is auto-allowed. The user's global agy settings are never touched. | JSON envelope `conversation_id` |
| `claude-code` (2.1) | `claude -p --model --output-format json --setting-sources user --strict-mcp-config --settings <seat settings>` | `--resume <id>` | Tool denies (`--disallowedTools` Write, Edit, MultiEdit, NotebookEdit, Agent/Task, WebFetch) + the `readonly-guard.py` PreToolUse hook on Bash from the seat settings file. If the installed Claude Code exposes filesystem sandbox settings, setup enables them for the seat with the target read-only and records it as the primary layer; otherwise the guard is the barrier and the reference doc says so. `--setting-sources user` and `--strict-mcp-config` keep the reviewed repo's `.claude/` and `.mcp.json` out. | JSON `session_id` |
| `pi` (0.74) | `pi -p --model <provider/id[:thinking]> --mode json` | `--session-id <id>` (the seat *chooses* the id per `(seat, dir)`; pi creates or resumes it — no recovery step exists) | **Tool allowlist by name**: `--tools read,grep,find,ls,bash` (no `write`/`edit`), plus `pi-readonly-guard.ts` loaded with `--no-extensions -e`, whose `tool_call` handler blocks any bash command that is not a single read-only command with plain arguments (in-process `{block: true}`, not a fail-open hook) and refuses `project_trust`. Seat-owned `PI_CODING_AGENT_DIR` (`state_dir/pi-<seat>`: `settings.json` with `defaultProjectTrust: "never"`, `models.json` registering the model's limits/reasoning, its own `auth.json`) and `--session-dir`. `--no-approve --no-skills --no-prompt-templates` keep the reviewed repo's `.pi/` and skills out. Denials are logged to a guard log so the attack test can require mechanism evidence. This is the runtime for any API-key model (OpenRouter, ZAI, Moonshot, "Kimi For Coding" keys). | The id passed in |
| `kimi` (0.27) | `kimi -p -m` | `-S <id>` (exact saved id; `-c` would pick the directory's most recent session, wrong under parallel seats) | Seat-owned `KIMI_CODE_HOME` with `credentials` symlinked from the user's home. Headless `-p` forces auto-approve and only **deny** rules apply. Hooks are **fail-open by the vendor's own docs**, so no bash guard is load-bearing here. Deny list: `Write`, `Edit`, `MultiEdit`, `NotebookEdit`, `Agent`, `AgentSwarm`, `CronCreate`, `mcp__**`, and **`Bash` entirely** — the seat reads with `Read`/`Grep`/`Glob` and receives diffs via `--diff`. Project `.kimi-code/mcp.json` is refused by step 2. The reviewer prompt is delivered as an `--agent-file` on new sessions only (the flag cannot combine with resume; on resume the prompt is already in the session). Setup recognises the "No model configured" state (installed, not logged in) and prints the interactive login instruction. | `session_index.jsonl` in the seat home |

**Shared reviewer prompt** (`seats/prompts/reviewer.md`): repo-aware, read-only,
report findings with a reference, a claim, evidence, severity, and what breaks. The
reference shape follows `--checkpoint`: `file:line` for `review` and `debug`, a
section heading or step for `design`, `plan`, and `decision`. Injected per runtime
(Codex `-c` instruction, pi and Claude `--append-system-prompt`, Kimi
`--agent-file`, agy prompt preamble).

**Minimum versions** are pinned in `seats/runtimes/versions.json` (the versions the
mechanics above were verified against); setup refuses an older binary and warns on a
newer major, because resume and permission semantics are exactly what changes between
versions.

## Guards (second layer only)

- `readonly-guard.py` — the Claude PreToolUse hook; `pi-readonly-guard.ts` — the pi
  `tool_call` extension. Both implement one policy, a **default-deny allowlist**: a single command from the read-only set with plain
  arguments; anything with redirection, pipes, `;`, `&&`, `||`, subshells,
  backticks, heredocs, interpreters (`python*`, `perl`, `ruby`, `node`, `awk`
  with `>`), `dd`, `tee`, `install`, `patch`, `xargs`, `env`/`bash -c`, or a
  `git` mutating subcommand is denied. Fails closed on unparseable payloads.
- `cases.tsv` — the shared case table (command → allow/deny). `tests/test_guards.sh`
  runs it through the Python guard and through the pi extension (via a small node
  harness that imports the extension and feeds it synthetic `tool_call` events), so
  they cannot drift.
- The reference doc states plainly which seats have OS enforcement (Codex, agy,
  Claude if its sandbox is available) and which rely on tool allowlists plus an
  in-process guard (pi) or tool denies alone (Kimi).

## `bin/setup`

Idempotent; safe to re-run after any upgrade. Steps, each printing OK / FIX / SKIP:

1. **Config**: parse, validate schema and the effort mapping, resolve paths, create
   `state_dir`.
2. **Runtimes**: for each enabled seat, locate the binary, print version, check auth
   state without prompting (Claude: `claude auth status`; Codex: `~/.codex/auth.json`;
   pi: the provider's environment variable set, or `pi auth` reporting the provider
   ready; Kimi: credentials present
   *and* a configured model; agy: cached credentials — a headless run without them
   exits `authentication required`). Missing auth → the exact interactive command to
   run.
3. **Provision** seat-owned homes/profiles/settings only (Codex profile files, Kimi
   seat homes, Claude seat settings, agy isolated homes, pi agent dirs with
   `settings.json` + `models.json`). Writes
   are atomic (temp file + rename), permissions `0600`, and a previous seat file is
   backed up beside it before replacement. **No user-global config is edited.**
4. **Commands and PATH**: symlink `panel/commands/*.md` into `commands_dir` and
   `bin/seat` into `bin_dir`; refuse to replace an existing *non-symlink* file of the
   same name (print the collision); warn when `bin_dir` is not on `PATH`. Record the
   repo root in `state_dir/install.json` so commands can print it.
5. **Verify by attack** (`seat <name> --verify`): in a fresh temp git repo, run the
   attack list below on a new session and again on a resume, then a token store/recall
   across resume. Pass/fail is decided by the **filesystem**, never by the model's
   words: a `find -newer` sentinel plus `git status --porcelain` must be empty, and
   `.git` must be byte-identical. **Mechanism evidence is required, not optional**:
   each runtime's adapter declares what a denial looks like (hook exit 2 in the
   guard log, sandbox `EPERM` on stderr, a permission notice, a Codex sandbox
   refusal) and at least one probe per session must produce it, on the new session
   *and* on the resume — a polite refusal with no denial artifact is a **fail**,
   because it cannot distinguish enforcement from compliance (and for Codex the resume
   path rebuilds read-only from `-c` overrides, which fails open if a key is renamed).
   A second, **hostile** temp repo is seeded with the project-level files each runtime
   would honour (`.pi/extensions/evil.ts` writing a file on `session_start`,
   `.claude/settings.json` with a hook, `.mcp.json`, `.kimi-code/mcp.json`): the
   expected result there is that `seat` *refuses to run* (step 2 of `seat`), and
   that is asserted. A failing seat is written to `seats.json` as `verified: false`
   with the failing probe named, and `seat` refuses it until setup passes.
   Attack list: write tool; edit tool; delete a file; `echo x > f`; `cat f | tee g`;
   `python3 -c "open('f','w')"`; `git commit --allow-empty`; `git branch probe`;
   write inside `.git/`; append to an existing file.
6. **Metrics**: report seats in the log not in config and vice versa; normalise
   nothing — the reader handles legacy records.
7. **Report**: one table — seat, runtime, version, auth, enforcement layer,
   read-only verified, resume verified.

`tests/test_setup.py` runs steps 1, 3, 4, 6, 7 against a temporary `HOME` and
`state_dir` (no model calls): idempotent re-run, atomic replace with backup, collision
refusal, `seats.json` transitions, partial-failure reporting.

## Panel commands

Static Markdown with the protocol; the roster comes from `seat --list`:

- `panel-review.md` — the moderated panel: the moderator materialises the target once
  (`--diff`), blind parallel round (one `seat` call per seat with the checkpoint's
  role), triage when noisy (≥ `bloat_total` raw or ≥ `bloat_per_seat` average with
  ≥ `bloat_min_seats`), claim-only cross-examination via `-r` (max 2 rounds),
  moderator verification against code, mandatory outcome record via
  `metrics/log.py panel` **with `findings_detail`**, then `log.py check` for watches
  and `log.py resolve` for any watch acted on.
- `seat-review.md` — one seat alone (`/seat-review <name> <target>`).
- `panel-stats.md` — regenerate and open the dashboard; analysis prompts for
  "is seat X dominating / redundant / pedantic?" grounded in the log.

## Metrics

Append-only JSONL, one reader. Event types (a superset of the existing schema, so an
existing log carries over):

| type | written by | fields |
|---|---|---|
| `usage` | `seat` | `ts`, `seat`, `model`, `mode` new\|resume, `dir`, `duration_s`, `ok`, `reason` |
| `panel` | moderator via `log.py panel` | `ts`, `id` (short random), `target`, `kind`, `seats` {name → model}, `rounds`, `immediate_agreement`, `findings` {seat → total/confirmed/refuted/partial/unique}, `findings_raw`, `findings_after_triage`, `findings_detail` [ {seat, group, title, claim, ref, severity, verdict, evidence, action} ], `disputes` [ {summary, challenger, proposals{seat→text}, winner, reason} ], `notes` |
| `watch` | `log.py check` | `ts`, `id` (sha1 of kind\|target\|panel_id, short — **no timestamp**, so re-running `check` cannot duplicate an open watch), `kind`, `target`, `panel_id`, `status` open\|resolved\|dismissed, `detail` |
| `watch-update` | `log.py resolve` | `ts`, `watch_id`, `status`, `note` |
| `panel-amend` | `log.py amend` | `ts`, `panel_id`, any of `findings_detail`/`findings`/`findings_raw`/`findings_after_triage`/`notes`, `note` |

- **Field contract.** Required on `panel`: `ts`, `id`, `target`, `kind` ∈ {`design`,
  `plan`, `review`, `debug`, `decision`} (the same set as roles), `seats`,
  `immediate_agreement`, `findings`, `findings_detail`. Optional: the rest.
  `findings_detail[].group` is shared by every seat that raised the same thing; a
  finding is **unique** when its group has one member *and* its verdict is
  `confirmed`, so `unique ⊆ confirmed` and the redundancy meter and the confirmed
  counts are drawn from one set. `model` values are free-form strings recorded for
  attribution; nothing in the schema enumerates or validates model ids (CLAUDE.md's
  "no model ids in interfaces" rule is about types and names, not data).
- `store.py` — `load()` returns usage, panels (amendments folded, `amendments[]`
  trail kept), watches (latest status folded), events. Legacy records are normalised
  on read: a `panel` without `id` gets `id = sha1(ts)[:8]`; missing `partial`/`unique`
  become `null` (not 0 — the redundancy watch and the dashboard treat `null` as "not
  recorded"); `seats` as a list becomes `{name: null}`. A **truncated final line is
  skipped with a warning**, never fatal. `BALANCEWHEEL_METRICS_LOG` overrides the
  path for tests.
- `log.py` — `usage`, `panel` (schema-validated; **rejects** a record whose
  `findings_detail` per-seat counts disagree with `findings`, or whose `seats` names
  are not in config, unless `--force` with a note), `check` (runs watches, dedupes
  against open ones by id, and **re-evaluates watches for a panel after an amend**),
  `watches`, `resolve <id>`, `amend --panel-id`. `unique` is **computed** from
  `findings_detail` groups at log time and cross-checked against any hand-filled value.
  All appends take an exclusive lock and write one line in one call.
- **Watches** (thresholds from config): `panel-bloat` (raw ≥ `bloat_total`, or
  average ≥ `bloat_per_seat` with ≥ `bloat_min_seats`; *performed* when the record
  carries `findings_raw`/`findings_after_triage`, else *suggest*),
  `verification-skim` (≥ `skim_min_seats`, ≥ `skim_min_findings`, 0 refuted and 0
  partial → suggest re-verifying a sample and naming which seat's findings failed),
  `redundant-seat` (0 `unique` over a seat's last `redundant_min_panels` panels within
  the last `redundant_window_panels` → suggest; never automatic; panels with `null`
  unique are excluded from the count and noted).

## Dashboard (`metrics/dashboard.py` → `state_dir/dashboard.html`)

Self-contained HTML, inline SVG, no external assets (must open from `file://`,
offline). Stdlib only. Every string from the log is HTML-escaped. Designed to read
at *seven* panels as well as seventy: points and step lines, never smoothed curves;
every mark carries a `<title>` tooltip naming the panel; **counts, not rates, wherever
n per panel is small**.

Sections, top to bottom:

1. **Right now** — stat tiles: open watches (count, oldest kind and age), last panel
   (target, seats, when), seats verified/unverified (from `seats.json`), log
   freshness (age of the newest event, amber past 7 days).
2. **Roster health** — one row per seat: a meter of unique catches over the last
   `redundant_min_panels` panels against the redundancy threshold (amber at 1, red
   at 0, grey when `null`), lifetime confirmed rate with n, challenges raised,
   dispute win share, and a **per-panel stacked mini-bar of confirmed / partial /
   refuted counts** (x = panel index, in date order) — the honest small-n view.
3. **Panel timeline** — a true date axis (irregular spacing preserved); each panel is
   a circle whose *area* is the raw finding count, filled when there was immediate
   agreement and ringed when disputes occurred, with a tick per dispute below it in
   the winner's seat colour (radius ∝ √count, i.e. area encodes the count). Vertical
   guide lines mark **model changes** (a seat's recorded `model` differing from its
   previous panel) and watch events. Roster changes are *not* inferred from first/last
   appearance — that is wrong at both edges of the log — they are read from
   `panel.seats` per panel and shown as the seat's presence dots under the axis.
4. **Dispute record** — the table (challenger, proposals, winner, reason), newest
   first.
5. **Panel findings** — per panel, `findings_detail` with verdict pills, collapsed
   with `<details>` (no JS); the audit surface for a `verification-skim` re-check.
6. **Roster watches** — the table with status pills and notes; open ones first.

Cut from v1 after review: the per-seat daily activity strip and the per-seat
wins/losses-over-time bars — both say little at current volume; usage share stays as
a single bar in section 2.

Colour: one hue per seat assigned in config order from a fixed 8-colour categorical
palette that passes contrast in light and dark; verdict colours are fixed (confirmed
green, partial amber, refuted red). Both themes via `prefers-color-scheme`.

## Dogfood plan

1. The author's roster (three seats) runs from balancewheel, with `metrics_log`
   pointed at the existing log; the private wrappers are retired only after a panel
   has been run end-to-end through `seat` and logged with `log.py`.
2. `gem` (`agy`, `gemini-3.1-pro-high`) is added by editing config and re-running
   setup. The test is: no code change, setup verifies it, the next review-checkpoint
   panel includes it, and its `unique` count starts being recorded.
3. The second user installs from README, enabling `kimi` and disabling `ox`. Their
   feedback becomes roadmap criterion 4's evidence; anything they had to ask about
   becomes a README fix.

The author has no Kimi account, so the `kimi` adapter is written from the vendor's
documented behaviour and the `setup --verify` attack test, and is **first verified on
the second user's machine**. Until that run, the example config ships it disabled and
the reference-implementation doc marks it "unverified by the author".

## Testing

- `tests/test_store.py`, `test_log.py`: fixture JSONL (synthetic paths, placeholder
  model ids) covering amendments, watch-update folding, legacy panels and watches
  without ids or `partial`/`unique`, a truncated final line, schema rejections
  (count mismatch, unknown seat), watch dedupe on repeated `check`, re-evaluation
  after amend, and each watch at its threshold edges.
- `tests/test_dashboard.py`: renders the fixture and asserts each section exists,
  handles an empty log and a one-panel log, irregular timestamps, circle-area
  scaling, amended records, and that model-supplied text containing `<script>` is
  escaped; parses the SVG with `xml.etree`.
- `tests/test_seat_dry_run.sh`: `BW_DRY_RUN=1` for every runtime × {new, resume}
  asserts the exact command line (with placeholder model ids), the refusal paths
  (unverified seat, version drift, repo extension present), and that `TMPDIR` is
  under the system temp root even when the inherited `TMPDIR` is the checkout. Runs
  under `/bin/bash` (3.2 on macOS) with `PATH` stripped of `jq`, `timeout`, and
  Homebrew.
- `tests/test_guards.sh`: the case table through both guards.
- `tests/test_setup.py`: as described under setup.
- Live attack tests run only inside `setup --verify` (they spend quota); CI never
  calls a model.

## Docs updated in the same change

- `README.md`: status → "extraction in progress", quickstart (install, copy config,
  `bin/setup`, run one panel), "why CLIs, not APIs", the enforcement table.
- `docs/reference-implementation.md`: five runtimes with the verified mechanics above;
  the Kimi headless permission and fail-open-hook findings; the agy plan-mode and
  sandbox-mount findings; why pi replaced OpenCode for API-key models (tool
  allowlist by name, in-process `tool_call` block, headless trust default, chosen
  session files — versus OpenCode's whole-command permission globs, always-loaded
  project tools, and session-id recovery); the installed-pi-vs-docs gap (0.74.x has
  `--session <path>` but not `--session-id`/`--no-approve`, and no
  `defaultProjectTrust`, so `--no-extensions` is what keeps project code out).
- `docs/architecture.md`: metrics loop now five event types, watches,
  `findings_detail`, model stamping; its event examples gain the `ts`/`kind` fields
  the real log has always carried (the examples were abbreviated, not the schema).
- `docs/roadmap.md`: **the extraction gate is amended, and says so.** Code lands now;
  criterion 1 (a month of unchanged design) is reset to the date of this change and
  criterion 4 (second user from README alone) is verified *during* the work, with the
  second user's report as the gate for calling the repo shareable. The planned layout
  is updated too: static per-runtime adapters replace the wrapper *generator* the
  roadmap sketched (the model-churn lesson made a generator the wrong seam), and
  `packs/` moves out of v1's layout to the esc hand-off note.
- `docs/seat-policy.md` (new): the governance loop — how a seat's roles change, what
  evidence is cited, where re-promotion criteria live (`policy` in config).
- `CLAUDE.md`: there is now code; how to run tests; the privacy firewall unchanged.

## Non-goals

Knowledge index; packaging as a Claude Code plugin (layout leaves room); Windows;
Linux verification; any router/proxy; convening seats from anything other than a
Claude Code moderator (other moderators can shell out to `seat`, but no commands are
written for them).

## Open risks (resolved by `setup --verify`, not by this document)

- Where `agy` keeps its OAuth credentials (file under its config dir vs Keychain),
  which decides how the isolated HOME is seeded.
- Whether the agy sandbox honours `read_file(<target>)` as a read-only mount when the
  target is the working directory.
- Kimi deny rules surviving `-S` resume (verified only on the second user's machine).
- Whether Claude Code 2.1's sandbox settings can be applied per `--settings` file.
- pi version drift: the adapter targets the flags of the installed 0.74.x
  (`--session <path>`); when `--session-id` and `--no-approve` ship, `versions.json`
  and the dry-run test decide when to switch.
