# Profiles, lead rotation, a starter, and the cockpit — design

*Written 2026-10-24; revised the same day after a builder read and a deep two-seat design
review (30 raw findings, 25 after dedupe, all verified; folded in below, not appended).
Builds on `2026-09-03-config-driven-seats-and-metrics-design.md` (seat runtimes,
`bin/seat`, setup-by-attack, the metrics loop), which stays the source of truth for what
it covers except where the "Amendments to earlier documents" section below says
otherwise. Tool versions were read on the author's machine on this date: Claude Code
2.1.310, Codex CLI 0.154.0, agy 1.4.7, pi 0.85.1, Kimi Code CLI 2.3.1, nodeterm
(self-updating), cc-switch 4.0.15 (source read at commit `85ad496`).*

*Amended 2026-10-25: Orca (1.4.240, MIT) replaces nodeterm as the recommended, optional
canvas, and the cockpit, inbox, and quota-probe work it duplicates is trimmed. A spike and a
deep four-seat review (43 raw findings, 17 after dedupe) shaped the change; the sections
below are edited in place and the "Orca as the default canvas" amendment lists every change.
The integration guide is `docs/orca.md`.*

## Intent

A developer who already pays for one or more vendor plans (Claude, ChatGPT, Google,
Kimi) plus a small metered budget (OpenRouter) wants to write the best software for the
least money, using whichever frontier model is best at each step, and to *know* — from
logged evidence — how each model is doing and what it costs. They work on several
projects at once with different stakes. What hurts today is that one plan (in the
reference setup, Claude) runs out first because it does everything: it leads, it
moderates, and it sits in the panel.

So: spread the work across the plans the user already pays for, move the lead off a plan
that is capped and back when it resets, keep every CLI current without losing the
read-only guarantee, and show quota, spend, and quality side by side.

The reader this is built for: a developer or architect, comfortable in a terminal,
Homebrew already installed or one paste away, who knows there are several vendors and
that the same model can cost very different amounts depending on how you pay for it.
macOS only for now.

## Goals

1. **Profiles.** A project maps to a profile; a profile names the lead order, which
   seats sit at each checkpoint, the default panel depth, and router mode. Switching
   projects switches the mix with no edits.
2. **Lead rotation.** When the current lead's plan is capped, the next lead takes over;
   when the preferred lead's plan has reset, the lead returns. Automatic both ways,
   logged both ways, with a manual override.
3. **One starter.** `bw start [project]` checks every CLI for updates, re-verifies any
   seat whose runtime changed, picks the lead, and opens the workspace.
4. **A cockpit.** A local, read-only web page: per-model quality (confirmed findings,
   unique catches, standout panels, redundancy), lead scores, spend per metered key, and
   the connection graph. Live quota bars and "an agent is waiting" belong to the canvas
   (Orca); the facts rotation acts on are in `bw status`.
5. **Terse inter-agent messages.** One small envelope for briefs, claims, challenges,
   verdicts, handoffs, and questions for the human.
6. **Jev used dynamically, under evidence.** The decision model moves from advice-only to
   shaping the seat mix within a profile's bounds, once its shadow record earns it.
7. **Reuse before build.** cc-switch supplies usage and cost for the user's own
   sessions; Orca (optional, recommended) supplies the live canvas, notifications, and a
   quota snapshot for plans with no documented surface; vendor CLIs supply everything
   model-side.
   Balancewheel adds only the policy, the scoreboard, and the glue.

## Non-goals

- **Not a router or proxy.** No request interception or translation. cc-switch's local
  routing, failover, and aggregation modes stay **off**; everything here works with them
  off (verified: cc-switch's session-log scanners write usage rows with routing off —
  `services/session_usage_{codex,agy,gemini,kimi,pi}.rs` insert into
  `proxy_request_logs` with `data_source = 'session_log'`).
- **Not a harness replacement.** Balancewheel launches and watches vendor CLIs; the user
  works *in* one. Rotation picks which; it never sits between the user and it.
- **Not a hosted service, and nothing runs when you are not working.** The only
  long-lived processes are the ones the user starts: `bw lead` (one per open project)
  and `bw cockpit`. No launchd agent.
- **Network traffic Balancewheel itself causes**, the complete list: each tool's own
  update command; quota probes to each vendor's own host; Jev calls to OpenRouter when a
  profile's router is not `off`; the opt-in daily team summary to an esc portal the user
  configures. Nothing else. (A canvas's own traffic is the canvas's: Orca polls vendor
  usage endpoints and sends telemetry unless turned off; setup turns its telemetry off.)

## Anti-goals

- An update that silently lands a seat without its read-only guarantee.
- A dashboard that credits one model with another's work, or hides which model
  moderated (verified) a panel.
- A lead switch the user cannot see or reconstruct afterwards.
- A credential read that is logged, written, refreshed, cached, or sent anywhere but the
  vendor that issued it — or one that teaches the user to widen a Keychain ACL.
- Two writable agents in one worktree at once.
- A second tool writing a file Balancewheel or esc owns. (Precisely, since 2026-10-25: a
  key or line bw writes — the statusline entry, a seat's own config home. A canvas adding
  its own entries to a shared vendor file, as Orca does, is tolerated only because the
  file is backed up, hashed into the seat verification key, and never loaded by a seat.)

## The pieces and how they fit

```
                       you
                        │  type in the lead · bw answer
         ┌──────────────┴───────────────┐
   Orca (recommended, optional)   terminal tabs (fallback)
         │  one pane per worktree running `bw lead`; Orca's quota view and notifications
         ▼
   bw start ─ update check ─ re-verify ─ pick lead ─ open workspace ─► bw lead <project>
                                                                        │ owns the worktree lock,
                                                                        │ runs the lead as a child,
                                                                        │ watches quota & cap signals,
                                                                        │ performs switches
   lead (claude | codex | agy | kimi) ── moderates ──► seats via `seat` (read-only, headless)
         │                                                  │
         └── metrics JSONL: panel (with moderator), usage (with tokens/cost),
             session, lead, ask/answer ◄────────────────────┘
                         │
   bw cockpit (127.0.0.1) ◄── cc-switch db (read-only, user's own sessions)
                          ◄── quota.json (statusline + documented probes + Orca snapshot)
                          ◄── registry.json (derived)
         └── opt-in daily summary ──► esc portal (team view)
```

### Who leads, who moderates

The **lead** is the interactive agent the user types into for a project. The lead is the
**moderator** of any panel it convenes: it frames the brief, runs seats with `seat`,
relays disputed claims, verifies findings against code, and logs the record. So:

- **Every panel record names its moderator**: `moderator: {lead, runtime, model,
  session}` with `model` the resolved id, never an alias. The cockpit shows verification
  outcomes per moderator, so a lenient moderator is visible instead of silently inflating
  every seat's confirmed rate.
- **The protocol reaches every lead runtime.** Claude Code: the existing commands.
  Codex, Kimi, agy: the same protocol text installed as a Balancewheel-owned file the
  runtime's user-level instruction file includes, or, where a runtime has no include,
  one line setup prints for the user to add. `seat` is a plain command any lead can call.
- **Leads are contained like drivers.** A lead writes, so it gets the architecture's
  driver containment, verified in both directions by attack (a write inside the
  project's worktree must succeed; a write outside it must fail): Codex with its
  workspace-write sandbox, agy with its sandbox, and **pi and Kimi under Balancewheel's
  own sandbox** (next section). Any runtime whose containment has not passed the attack
  test on its current verification key is not eligible as a lead.

### The sandbox (`bw sandbox`)

macOS's built-in Seatbelt (`sandbox-exec`, the same kernel mechanism Codex and Claude
Code use internally), driven by a profile `bw` generates per run. No install, no proxy,
nothing between the agent and the network. Profile shape (rules later in the profile
win; every path resolved with `pwd -P` first, because Seatbelt matches real paths):

```text
(version 1)
(allow default)                      ; reads, network, processes unchanged
(deny file-write*)                   ; then allow writes only to:
(allow file-write*
  (subpath "<runtime state dir>")    ; e.g. the seat home, PI_CODING_AGENT_DIR, KIMI_CODE_HOME
  (subpath "<runtime credential dir>")   ; only where the vendor CLI refreshes its own token
  (subpath "<per-user temp dir>")    ; the user's /private/var/folders/<..>/<..> pair, not all of it
  (regex #"^/dev/")
  (subpath "<worktree>"))            ; leads only; omitted for seats
(deny file-write*                    ; leads only: git control files stay read-only
  (subpath "<git-common-dir>/hooks")
  (literal "<git-common-dir>/config")
  (regex #"^<git-common-dir>/worktrees/[^/]+/config\.worktree$"))
```

A linked worktree adds its `<git-common-dir>` (writable, minus the denies) so commits
work. Seats get the same profile with **no** worktree entry: everything in the reviewed
repo is read-only to the OS.

**Host control channels are part of containment (added 2026-10-25).** A filesystem
allow-list does not stop a contained process from asking another, uncontained process to
write for it. Orca's runtime takes `terminal create`/`send`/`read` over a unix socket and a
localhost WebSocket, authenticated by a token in a readable file under
`~/Library/Application Support/orca/`. On 2026-10-25, with the Orca app running, a process
under this `sandbox-exec` profile (and under the reference agy seat's profile) ran `orca
terminal create --command "touch <outside>"` and the file appeared outside the worktree,
while its own direct write was denied. nono denied it only because its defaults happened to
block reading that directory; Codex's sandbox refused the connection. Every profile, nono
and `sandbox-exec`, lead and seat, therefore adds:

```text
(deny file-read* (subpath "<home>/Library/Application Support/orca"))   ; hides the auth token
(deny network-outbound (remote unix-socket (subpath "<home>/Library/Application Support/orca")))
```

Both were verified to block the attack (each alone suffices; both are kept). `bw sandbox
verify` and the seat attack test gain a case that tries `orca terminal create` from inside
when the `orca` binary is present. A canvas added later gets the same treatment before it is
recommended: find its control channel, deny it, attack it.

**Verified by attack on 2026-10-24** (filesystem-decided, kernel `EPERM` as the
mechanism evidence):

| Run | Inside the worktree | Outside, `.git/hooks`, `.git/config`, symlink escape, `node` child writing outside |
|---|---|---|
| Kimi 2.3.1 as a **seat**, fresh session and resumed (token recalled) | all writes denied, `.git` byte-identical | all denied |
| Kimi 2.3.1 as a **lead** | write and commit succeed | all denied |
| pi 0.85.1 + `deepseek/deepseek-v4.1-flash` as a **lead** | write and commit succeed | all denied |

Two findings from the same session that shape the adapters:

- **Kimi 2.3.1 ignores `[[permission.rules]]` deny rules in `-p` mode.** With denies for
  `Write`, `Edit`, `Bash`, `Agent` and others (both `Write` and `Write(*)` forms), every
  attack succeeded unsandboxed: file writes, edits, shell writes, a commit, a sub-agent's
  write, a git hook. A `PreToolUse` hook exiting 2 did block, but Kimi's hooks are
  fail-open by its own docs. So the Kimi seat's enforcement is the sandbox; the hook is a
  second layer with a guard log for mechanism evidence. The 2026-09-03 table's Kimi row
  ("tool denies") is superseded.
- `codex sandbox` (Rust, ships with Codex) works for shell-level tools but crashed
  Kimi's single-executable Node at startup, so it is not the shared mechanism.
- pi in `-p` mode needs stdin closed (`< /dev/null`) or it waits forever.

### Backends: nono by default, `sandbox-exec` as the zero-install fallback

`bw sandbox` has two backends behind one interface, each with its own attack test:

- **nono** (Rust, Apache-2.0, `brew install nono`; tested at 0.80.0) — **the default.**
  It allow-lists *reads* as well as writes, and its default profile denies credentials,
  keychains, browser data, shell history, and shell config files. An agent under nono
  cannot read `~/.ssh`, `~/.aws`, or a secrets file even if the model is talked into it;
  under the profile above it can. It also has an audit trail and Linux (Landlock)
  support for later.
- **`sandbox-exec`** with the profile above — **the fallback** when nono is absent:
  nothing to install, but reads stay open.

**nono, verified by attack on 2026-10-24** — the same three runs as above, all passing,
disk-checked: the Kimi seat (fresh and resumed, token recalled; every write, commit,
branch, hook, symlink escape, and `/tmp` write denied; `.git` byte-identical), the Kimi
lead, and pi + `deepseek/deepseek-v4.1-flash` as a lead (write and commit inside;
outside, `.git/hooks`, `.git/config`, symlink escape, a `node` child, and `/tmp` all
denied).

What `bw` must generate for nono, learned in that run:

- **A profile file per run** that `extends: "default"`, **excludes the
  `system_write_macos` group** (its default makes all of `/tmp` and `/var/folders`
  writable, which would let a seat write a repo under `/tmp`), and grants: `allow` for
  the runtime state dir, the per-user temp dir, any credential dir the vendor CLI
  refreshes, and (leads only) the worktree; `write` for `/dev`; `read` for the runtime's
  install dir (for pi, the Volta tree it runs from), the reviewed repo (seats), and
  nothing else in the home directory.
- **`.git/hooks` and `.git/config` read-only for leads via one raw Seatbelt rule**
  (`unsafe_macos_seatbelt_rules`: `(deny file-write* (subpath "<common>/hooks") (literal
  "<common>/config"))`). nono's typed `filesystem.deny` blocks *reads* too, which breaks
  git, and a `read` grant does not narrow an enclosing `allow`.
- **A private `HOME` for the child, set inside the sandbox** (`nono run … -- /usr/bin/env
  HOME=<state>/h <cli> …`). nono refuses any grant on the real home directory (it
  protects its own state root under it), and Kimi watches `$HOME` at startup and exits
  on `EPERM`. Setting `HOME` outside the sandbox moves nono's own state root instead.
- `--silent --allow-cwd` for non-interactive runs.

`sandbox-exec` is marked deprecated in Apple's man page and has been for years while
remaining the mechanism the major agent CLIs (and nono itself, on macOS) use; the attack
test on every verification key change is what keeps both backends honest.
- A model may sit in a panel moderated by its own vendor's lead only as a **fresh blind
  session** (the existing same-vendor seat rule). The moderator never scores itself.

## Config additions (`~/.config/balancewheel/config.json`, `schema_version` → 2)

Version 1 files load unchanged; new keys are optional and default to "one profile, the
user's current behaviour".

```json
{
  "schema_version": 2,
  "plans": {
    "claude-max": {"vendor": "anthropic",  "billing": "plan",    "quota": "statusline"},
    "chatgpt":    {"vendor": "openai",     "billing": "plan",    "quota": "orca"},
    "google":     {"vendor": "google",     "billing": "plan",    "quota": "probe"},
    "kimi-code":  {"vendor": "moonshot",   "billing": "plan",    "quota": "orca"},
    "openrouter": {"vendor": "openrouter", "billing": "metered", "quota": "probe", "budget_usd_month": 20}
  },
  "tools": {
    "claude":    {"update": ["claude", "update"]},
    "codex":     {"update": ["<detected by setup>"]},
    "agy":       {"update": ["agy", "update"]},
    "pi":        {"update": ["<detected by setup; pi runs on its own pinned node>"]},
    "kimi":      {"update": ["<detected by setup>"]},
    "orca":      {"app": "/Applications/Orca.app", "self_updating": true},
    "cc-switch": {"update": ["brew", "upgrade", "--cask", "cc-switch"]}
  },
  "leads": {
    "claude-opus": {"tool": "claude", "model": "opus",                 "plan": "claude-max"},
    "astra":       {"tool": "codex",  "model": "<model id>",           "plan": "chatgpt"},
    "agy-opus":    {"tool": "agy",    "model": "claude-opus-5-5-high", "plan": "google"},
    "kimi-pro":    {"tool": "kimi",   "model": "kimi-code/k3.1-pro",   "plan": "kimi-code"},
    "flash":       {"tool": "pi",     "model": "openrouter/deepseek/deepseek-v4.1-flash", "plan": "openrouter"}
  },
  "seats": {"...": "as in the 2026-09-03 design, plus \"plan\" and optional \"fallback\" (a seat name)"},
  "profiles": {
    "high-stakes": {
      "lead_order": ["claude-opus", "astra", "agy-opus"],
      "checkpoints": {
        "design": {"seats": ["astra", "fable", "argon"], "required": ["astra"], "min_seats": 2},
        "review": {"seats": ["astra", "fable", "argon", "kimi"], "required": ["astra", "fable"], "min_seats": 3}
      },
      "depth": "deep-on-stakes",
      "router": "shadow"
    },
    "product": {
      "lead_order": ["claude-opus", "astra", "kimi-pro"],
      "checkpoints": {
        "design": {"seats": ["astra", "kimi", "argon"]},
        "review": {"seats": ["astra", "kimi", "argon", "flash"]}
      },
      "depth": "standard",
      "router": "shadow"
    }
  },
  "projects": {"~/code/example-app": "product"},
  "default_profile": "product",
  "rotation": {"return_above_pct": 50, "cap_expiry_hours": 5, "probe_every_min": 15},
  "updates":  {"min_interval_hours": 0},
  "cockpit":  {"port": 8765}
}
```

Rules (the loader rejects anything else, naming the key path, as v1 does):

- `plans.*.billing` ∈ {`plan`, `metered`}; `quota` ∈ {`statusline`, `probe`, `orca`,
  `none`}; `budget_usd_month` only on metered plans. `orca` reads Orca's snapshot under
  the rules in "Quota probes"; without Orca it reads `unknown`. Optional
  `plans.*.orca_windows` maps an Orca model-pool bucket to a lead or seat (below).
- `tools.*.update` is an argv list, run without a shell. Setup detects each tool's
  install method on first run and writes a suggested value; it never guesses silently.
- `leads.*` reference a tool and a plan; `model` is passed to the runtime verbatim and
  **resolved to the concrete id the runtime reports at session start** before anything
  is logged. A lead whose runtime has not passed the containment attack is refused.
- `seats.*.plan` ties a seat to its plan, so a capped plan takes its seats out of panels
  (recorded `absent: usage cap`, per the existing rule). `seats.*.fallback` names a seat
  that stands in for it while capped, scored under its own name (the architecture's
  stand-in rule).
- `profiles.*.checkpoints.<checkpoint>`: `seats` is the roster for that checkpoint and
  **wins over seat `roles`**; `roles` apply only to checkpoints a profile omits.
  `required` seats always run; `min_seats` (default 2) is the floor the router may not go
  under. `depth` ∈ {`standard`, `deep`, `deep-on-stakes`}. `router` ∈ {`off`,
  `shadow`, `acting`}.
- `projects` maps a path to a profile, matched on **path components** after resolving
  `~`, symlinks, and git worktrees (`git rev-parse --git-common-dir`, so every worktree of
  a repo gets the repo's profile); the longest component match wins; anything unmatched
  gets `default_profile`. Nothing is ever written into a project repo.
- `updates.min_interval_hours` defaults to **0**: every `bw start` checks every tool.
- Model ids remain data. Names (`flash`, `argon`, `kimi`, `astra`) are the user's.

## Seats added in this round

Through the 2026-09-03 runtime adapters (`bin/seat`), on trial with fresh scoreboards:

| Seat (example name) | Runtime | Model (config) | Billing | Intended use |
|---|---|---|---|---|
| `argon` | `agy` | `gemini-4-argon-high` (agy encodes effort in the id) | Google plan | peer on design, plan, review |
| `kimi` | `kimi` | `kimi-code/k3.1-pro` (`-highspeed` for fast reads) | Kimi plan | peer on review; plan on trial |
| `flash` | `pi` → OpenRouter | `deepseek/deepseek-v4.1-flash` (not the pricier dated `-1017` snapshot) | metered | fast and cheap: triage of noisy panels, first-pass reads, "already covered?" checks |

- `flash` needs a pi `provider` block: the model is absent from pi 0.85.1's built-in
  catalog (context 1,048,576).
- `seats/runtimes/versions.json` moves to the versions above; every runtime is
  re-verified by attack before its trial. The Kimi adapter was specified against 0.27 and
  the CLI is now 2.x, so its permission mechanics are re-read from the vendor docs.
- **agy writes into its working directory**: running `agy models` from a repo left an
  `unleash-repo-schema-v1-codeium-language-server.json` feature-flag cache there. The agy
  adapter runs agy with its working directory inside the seat home and passes the target
  with `--add-dir`; the attack test's empty `git status --porcelain` check catches any
  regression.
- **agy offers Claude models on the Google plan**: 1.4.7 lists Opus 5.5 and Sonnet 5.5
  (low/medium/high). That makes agy a Claude-family lead that does not draw on the
  Anthropic plan; the example lead order uses it, and the scoreboard decides whether it
  matches first-party Claude Code.

## Jev: from advice to acting, on evidence

**What Jev is asked.** Jev never sees code, diffs, or the seat brief; it gets the
redacted one-paragraph task description the router already sends today. It answers typed
questions: difficulty, stakes, and, per candidate seat, one yes/no question ("would a
reviewer described as <the seat's role text from config> likely add a finding here?").
**Balancewheel's policy, not Jev, turns those answers into a subset**: keep `required`
seats, keep every seat whose yes-probability clears a threshold, top up to `min_seats` by
the seats' scoreboard rank. Jev can still only *raise* depth. The client is a clean-room
OpenRouter call in this repo, and it fails open: on any error the full checkpoint roster
runs.

**The miss metric is coverage, not uniqueness.** For a panel, a *miss* is a confirmed
finding group at major or above to which **no retained seat** contributed (if seats A and
B both found it and both were dropped, that is a miss even though neither was unique).

**Shadow.** In `shadow`, every panel runs the full roster; Jev's subset is computed and
logged **before** round 1 results exist; misses are counted afterwards.

**Promotion** is the user's edit, and the cockpit says when evidence supports it, per
profile: at least 20 shadow panels in which Jev would have dropped at least one seat,
containing at least 5 confirmed major-or-above groups, with zero misses. A change to the
profile's roster or to Jev's pinned model version resets the count.

**Acting, with audits.** In `acting`, every fourth panel (configurable) is an **audit**
that runs the full roster blind, with Jev's subset recorded first. A miss on an audit
returns the profile to shadow automatically by writing an override to
`state_dir/router.json` (the config file is never rewritten), logs the reason, and puts a
notice in the inbox. Non-audit panels in acting cannot produce misses and are not counted
as evidence.

## Lead rotation

### The actor

`bw start` hands off to **`bw lead <project>`**, a small process that:

- holds the project's **worktree lock** (`state_dir/locks/<repo-id>.lock`) for as long
  as any lead runs there, so two writable leads never share a worktree;
- runs the lead runtime as its child in the foreground terminal (an Orca pane or a plain tab);
- reads quota signals while the lead runs (below) every `probe_every_min`;
- records `session` events (start and end: `project`, `worktree`, `lead`, `runtime`,
  resolved `model`, `plan`, `session_id`), so every lead minute is attributable;
- performs switches and returns.

### Quota and cap signals, per plan

- `statusline` (Claude): Claude Code's documented status line input carries
  `rate_limits.five_hour` and `rate_limits.seven_day` with `used_percentage` and
  `resets_at` (added in 2.1.80). Setup installs a Balancewheel statusline command (or
  wraps the user's existing one, preserving its output) that writes those two windows to
  `quota.json`. No credential is touched. The field is absent before the first response
  of a session and has regressed in at least one release (anthropics/claude-code#45133),
  so absence is `unknown`, never zero.
- `probe` (agy, OpenRouter: documented surfaces) and `orca` (Codex, Kimi: Orca's
  snapshot): see "Quota probes".
- **Cap observed**: a usage-limit message from the lead runtime (detected by the
  runtime's hook where one exists, or by the statusline reaching 100%). The cap record
  stores the window and its `resets_at` (from the message or the probe; if neither,
  now + `cap_expiry_hours`).

### Transitions

| Situation | Action |
|---|---|
| Current lead's plan capped mid-session | Switch now (the session cannot continue anyway): stop the child (the runtime's own quit, then SIGTERM to its process group after a grace period), release and re-take the worktree lock, start the next eligible lead with the handoff. |
| Current lead's plan low but not capped | Nothing mid-session. At the next `bw start` for that project, if a window is below 10% **and** its reset is more than an hour away, start on the next eligible lead instead. |
| Choosing the next lead | First in `lead_order` that is not under an unexpired cap record. A plan whose quota is `unknown` is eligible. |
| No eligible lead | Stay put, put an inbox notice: "all leads capped; earliest reset <time>". |
| Preferred lead's cap has passed its recorded `resets_at` | Return at the next task boundary: the current lead's session ends, or the next `bw start`. If the probe reads above `return_above_pct`, or the probe is `unknown`, the earlier lead is eligible again. Never mid-turn. |
| User override | `bw lead <project> --use <lead>` pins a lead until `bw lead <project> --auto`. Shown in the cockpit. |

Every change appends a `lead` event (`project`, `from` and `to` as resolved
`{lead, runtime, model}`, `reason`, the quota snapshot used).

### Handoff

The handoff is built from facts `bw` can read, not from transcripts: the branch, `git
status`, the last commits since the session started, the diff stat, open inbox items, and
the most recent **checkpoint** the lead wrote. Leads are instructed to write a checkpoint
in the terse envelope (goal, done, next, open questions) to
`state_dir/handoffs/<repo-id>.md` at every task boundary, so a capped lead's last
checkpoint is never more than one task old. The incoming lead starts with the handoff as
its first prompt. Handoffs live in the state dir, never in the repo.

## Quota probes

cc-switch shows quota but keeps it only in memory (`services/usage_cache.rs` is
in-process and not persisted; there is no CLI or local API), so Balancewheel reads quota
itself. Preference per plan:

1. **A documented surface**: Claude's statusline (above); `agy --print /usage
   --output-format json`; OpenRouter's documented `GET /api/v1/key`.
2. **Orca's snapshot** (`quota: "orca"`), for plans with no documented surface (Codex,
   Kimi): `orca account list --json` → `result.rateLimits.<provider>`. Orca already
   polls these vendors (amended 2026-10-25; this replaces the earlier plan to read
   `~/.codex/auth.json` and the Kimi credentials file and call the vendors ourselves, so
   bw now reads no vendor credential at all). Without Orca, these plans read `unknown`.
3. **Keychain-held credentials are not read** by Balancewheel. Orca reads Claude's token
   from the Keychain for its own display (through `/usr/bin/security`; its source has no
   Claude or Kimi token refresh); bw does not use Orca's Claude numbers for decisions —
   the statusline stays the only Claude input — and shows them only as a cross-check.

Rules for (2), because the field is not a documented Orca interface (its CLI help says
listing "shows no usage") and Orca updates itself:

- Accept a provider entry only with `status: "ok"` and no `error`; anything else is
  `unknown` with Orca's reason.
- `observed_at` is the entry's `updatedAt` (Orca's poll time, epoch ms ÷ 1000), never the
  time bw ran the command; the CLI returns Orca's cache (`refreshUsage: false`), so a
  re-read is not a fresh observation. Older than 2 × `probe_every_min` is `unknown`.
- `resetsAt` is epoch **milliseconds**; convert and bounds-check. `usedPercent` must be
  finite and within 0–100.
- Only account-wide windows (`session`, `weekly`) feed rotation. Model-pool windows —
  Claude's Fable-only weekly window, agy's per-pool buckets — are ignored unless
  `plans.*.orca_windows` maps one to the leads or seats it limits; a full pool then takes
  out only those, never the plan.
- Orca readings never create, extend, or clear a cap record; caps come from first-party
  signals (statusline, the runtime's own limit message, `bw cap`). A cached percentage
  never triggers a mid-session switch.
- Each snapshot records `source: "orca"` and the Orca version; a version change re-runs the
  shape check (synthetic fixtures) before the next reading is trusted.
- Precedence when a plan has several sources: statusline → documented surface → Orca →
  `unknown`. Each source writes its own snapshot; the arbiter picks, so a stale reading
  never overwrites a fresher one.
- A plan whose only source has been `unknown` for more than one probe interval gets one
  notice in `bw status` ("no quota reading since <t>; rotation is using observed caps
  only"), not a silent fallback.

Rules for every probe: only percentages, windows, and reset times are kept, in
`state_dir/quota/<plan>.json` with a timestamp. A failed probe is `unknown` with its
reason; nothing retries in a loop. Parsers are tested against
**synthetic** responses written by hand from the documented or observed schema — never
captured responses, redacted or not (privacy firewall).

## Updates and verification

**Runtime locks.** `seat` and `bw lead` take a **shared** lock on their runtime
(`state_dir/locks/runtime-<name>.lock`) for the whole run. The updater needs the
**exclusive** lock; if any holder is live, `bw start` skips that tool's update, says so,
and tries again next start. A resumed cross-examination therefore never lands on a binary
that changed under it.

**Verification key.** A seat's verification is valid for a key made of: runtime binary
version, hashes of the guard files and the seat's settings or profile files, and for pi
the Node version it runs on. `seat` recomputes the key at every invocation and refuses
on mismatch or on an **unreadable version** (a half-finished install reads as
unreadable). **Amended 2026-10-25:** the key also hashes every user-level config and hook
file the runtime loads (for Claude Code the user settings file, for Codex `config.toml` and
`hooks.json`, for agy its settings and hooks, for Kimi Code its `config.toml`), because a
canvas can edit those without any update (Orca adds hooks to all of them on first launch).
A changed file makes the seat `pending` until the attack test passes again. Seats are
built to load no user-level hooks at all (seat-owned config homes; for agy, its own
configuration directory), and `seat` and `bw lead` drop `ORCA_*` and other canvas variables
from the environment they pass to a seat, so a gated canvas hook stays inert inside one.

**`bw start` steps.**

1. Resolve the project (argument, else cwd) to its repo id, worktree, and profile.
2. Check every tool for an update (default: every start), **all tools in parallel**: one
   child process per tool, each taking its own runtime's exclusive lock (tools never
   share a lock, so they never wait on each other), with a per-tool timeout and one
   live status line per tool. Tools that share an installer (two Homebrew casks, two npm
   globals) are serialised within that installer only, because Homebrew and npm take
   their own global locks. Record before and after versions. Orca updates itself;
   the starter reports its version only (and a change re-runs the quota shape check). A failed or timed-out update is reported; the
   tool is then re-checked for a readable version. Steps 3 and 4 for a tool start as soon
   as that tool's update finishes, not after the slowest one.
3. Recompute every seat's verification key. A changed key runs the attack test (fresh
   and resumed sessions, filesystem-decided, mechanism evidence required). A seat that
   fails is `verified: false`; a seat whose test could not run (plan capped, offline) is
   `verified: pending` with the reason, and `seat` refuses both until a pass. Leads get
   the containment test (both directions) on their key change.
4. Read quota for every plan the profile touches; pick the lead per the transitions.
5. Refresh `registry.json`.
6. Open the workspace: with Orca running, `orca terminal create --worktree
   path:<worktree> --title "<project> lead" --command "bw lead"` (verified 2026-10-25: the
   lead runs in bw's own permission mode and Orca still shows its status); without it, a
   terminal tab with `bw lead`. This is the **only** way bw work starts in Orca: Orca's
   `agent launch` (permission bypass by default), orchestration workers, automations, and
   session restore start agents without bw's worktree lock or containment and are not used
   on a worktree bw manages. Setup checks that Orca's agent permission default is Manual
   and its automatic account switching is off (an account changing under a running lead
   would make the logged `plan` wrong), and says so in `bw status` when either is not.
7. The cockpit is opened on demand (`bw cockpit`); it is read-only and need not run.
8. Once per day, if team reporting is configured, send the summary (below).

On a day with no updates, steps 1–7 should add only a few seconds (version checks and
probes run in parallel with short timeouts); with updates, progress prints per tool.

## Terse message envelope

Every message between agents uses one shape:

```
@<to> <kind> [ref:<file:line|section>] [sev:<crit|major|minor>] [id:<short>]
<one-line claim or ask>
ev: <one line of evidence, or omitted>
```

Kinds: `brief`, `claim`, `challenge`, `verdict`, `handoff`, `checkpoint`, `ask`. Rules:
never restate what the recipient already has; reference by `file:line` instead of
quoting; one claim per message; a relayed claim carries the claim, never the transcript.
Briefs and handoffs may run longer but keep the header line. The reviewer prompt asks
seats to answer in the same shape, which also fills `findings_detail` more directly.

## The inbox

An `ask` addressed to `@human` from a lead is appended to `state_dir/inbox.jsonl` (`id`,
`ts`, `project`, `from`, `text`, `status`). It surfaces in `bw status` and `bw inbox`; a
lead asking in an Orca pane also shows as "waiting" there, through Orca's own hooks. The
user answers with `bw answer <id> "<text>"`; the answer is appended as an `answer` record.
The asking lead reads answers with `bw inbox --project`, and its instructions say an
answer is **the user's words relayed by bw**, to be treated like anything else the user
types. Seats never ask. Rotation notices (all leads capped, a pinned lead capped, the
router returned to shadow) are inbox items too, so they survive the terminal they were
printed in.

*Amended 2026-10-25:* the records and the two commands stay — they feed handoffs ("open
questions"), rotation notices, and the Human load and Continuity columns of lead scoring,
and Orca's inbox shows agent state, not bw's questions. What is cut is the cockpit's inbox
view, its answer endpoint, and the `osascript` notifications; Orca's notifications cover
"an agent is waiting".

## The registry (`state_dir/registry.json`, derived)

Config is canonical; the registry is a derived snapshot rebuilt by `bw registry` and by
`bw start`, so deleting it loses nothing:

- `tools[]`: name, path, version, install method, last update, auth state, live lock holders.
- `plans[]`: billing, quota source, last snapshot, cap records.
- `models[]`: id, reached through which tools and plans, list price where metered.
- `seats[]`: runtime, model, plan, fallback, verification state and key, enforcement layer.
- `leads[]`, `profiles[]`, `projects[]` (repo id → profile → current lead, pinned or auto).
- `edges[]`: `project→profile`, `profile→lead|seat`, `lead|seat→tool`, `tool→plan`,
  `plan→model` — what the cockpit graph draws.

## Usage and cost data

- **The user's own sessions** (leads, and anything else run in the default vendor homes):
  cc-switch's `~/.cc-switch/cc-switch.db`, tables `proxy_request_logs` (rows with
  `data_source = 'session_log'`) and `usage_daily_rollups`, opened read-only
  (`sqlite3.connect("file:...?mode=ro", uri=True)`), one short connection per refresh,
  unknown columns or tables treated as `unknown` rather than an error. cc-switch is
  optional; without it the view says so.
- **Seats** run in seat-owned homes cc-switch does not scan, so seat cost comes from the
  seat's own output: the `usage` event gains optional `tokens_in`, `tokens_out`,
  `cost_usd`, and `session_id`, filled from whatever the runtime reports (pi's JSON mode
  reports tokens and cost; plan-billed runtimes report tokens or nothing). A metric a
  runtime does not report is **unavailable**, never zero.
- **Jev** cost comes from its own response and is logged with each route.

## The cockpit (`bw cockpit`)

A Python standard-library HTTP server bound to `127.0.0.1`, serving one page of vanilla
JavaScript and inline SVG with no external assets (works offline).

**Security.** The starter opens `http://127.0.0.1:<port>/?t=<token>` once; the server
exchanges the token for an `HttpOnly`, `SameSite=Strict` cookie and redirects to the
clean URL, so the token does not stay in history or a referrer. Every request must carry
the cookie and a `Host` header equal to `127.0.0.1:<port>` or `localhost:<port>` (blocks
DNS rebinding). Responses send a nonce-based `Content-Security-Policy` (`default-src
'none'`, scripts and styles by nonce, `connect-src 'self'`), `Referrer-Policy:
no-referrer`, and no CORS headers; `OPTIONS` is refused. The cockpit is **read-only**
(amended 2026-10-25: the inbox-answer endpoint is gone; answers go through `bw answer`),
so it accepts `GET` only. **Every** value from
every source — logs, cc-switch rows, probe results, handoffs, inbox text — reaches the
DOM through `textContent` or an escaping helper, never `innerHTML`.

**Views.**

1. **Now** — per project: profile, current lead (pinned or auto) and why, next eligible;
   per plan, one line: remaining per window, cap-until, **source and age** of the reading
   (live bars and countdowns are Orca's; this line exists so a stale or mis-mapped reading
   is visible where decisions are explained); OpenRouter spend this month against
   `budget_usd_month` (Orca has no OpenRouter); open watches. `bw status` prints the same
   facts as text and is the sanctioned surface for every rotation-relevant fact.
2. **Models** — seat rows: panels attended, confirmed findings, unique catches,
   **standout panels** (a unique confirmed finding at major or above), dispute win share,
   the redundancy meter, and cost (dollars per confirmed finding when metered; tokens per
   panel when the runtime reports them; otherwise "unavailable"). A **Leads** table per
   "Lead scoring" below. Counts, not rates, at small n.
3. **Usage** — tokens, requests, and cost per tool and model over time (sources above).
4. **Graph** — the registry's edges as a layered diagram (projects → profiles →
   leads and seats → tools → plans → models), each node coloured by health (verified,
   pending, capped, unknown).
5. **History** — the existing dashboard's timeline, dispute record, and findings audit,
   plus `lead` and `session` markers.

The static `metrics/dashboard.py` stays for `file://` use; the cockpit reuses its data
functions.

## Third-party tools and the boundaries with each

| Tool | Role here | Settings setup requires |
|---|---|---|
| **Orca** (MIT) | recommended, optional canvas: one pane per worktree running `bw lead`; notifications; quota snapshot for `quota: "orca"` plans | agent permissions **Manual**; automatic account switching **off**; orchestration, automations, and `agent launch` unused on bw-managed worktrees; telemetry off (`DO_NOT_TRACK=1` at launch and the in-app switch). It adds status hooks to every vendor's user config on first launch and pre-trusts its Codex hooks (setup backs up first; `orca agent hooks off` removes them; seats never load them). Every sandbox denies its control channel. Never a hard dependency. Guide: `docs/orca.md` |
| **nodeterm** | another canvas that can host `bw lead` in a terminal frame; no longer driven by `bw start` | BUSL license, so never a hard dependency; it also adds a Claude Code hook, and its control CLI gets the same deny-and-attack treatment before any use |
| **cc-switch** | usage and cost for the user's own sessions (read-only SQLite) | routing, failover, aggregation **off**; provider management **off for Codex** (it migrates Codex history and writes Codex profiles); prompt, MCP, and skills sync **off** for any file Balancewheel or esc manages |
| **Vendor CLIs** | leads and seats | per the 2026-09-03 seat homes; Balancewheel's setup never edits a user-global config except the statusline line it asks the user to accept (a canvas may — Orca does — which is why those files are in `setup/paths.txt` and in the seat verification key) |
| **Jev** (OpenRouter) | decision model | metered on the user's key |
| AWS CLI Agent Orchestrator | not adopted now: the lead relays messages; revisit if relay becomes a bottleneck | — |
| tokscale | superseded by cc-switch's usage view | — |
| ACP | the long-term seam for driving any CLI uniformly; not used in this round | — |

## Team view (OpenEscapement)

Opt-in (`team.portal_url` in config; the token in the environment only, matching esc's
`ESC_PORTAL_TOKEN` convention). Once a day `bw start` sends: date, tool, model, plan,
requests, tokens, cost, panels, confirmed and unique counts per seat, lead switches.
Never prompts, code, paths, project names (a salted hash, or a label the user sets), or
findings text. The esc side (ingest, storage, the portal view) is designed in esc's own
telemetry work; this document fixes only the payload.

## Amendments to earlier documents (named, not silent)

- **2026-09-03 design, Non-goals**: "no commands are written for [other moderators]" is
  reversed — the protocol is installed for every lead runtime.
- **`docs/architecture.md`**: "driver mode is failover, not promotion" is narrowed — a
  profile may make a non-Claude agent the default lead; driver mode outside profiles is
  unchanged, and leads inherit driver containment.
- **`docs/roadmap.md`**: the "harness replacement" non-goal is restated as above; the
  extraction gate gets an amendment (below).
- **README**: "nothing runs as a service" stays true and is restated as "nothing runs when
  you are not working".
- **`setup/paths.txt`** (a versioned contract): gains the statusline script entry, the
  per-runtime protocol include files, and `state_dir` subpaths that matter for backup
  (`leads.json`, `router.json`, `inbox.jsonl`, `handoffs/`). CHANGELOG records it with
  config schema 2 and the new and extended log events (`session`, `lead`, `ask`,
  `answer`; `panel.moderator`; `usage` token and cost fields).

### Amendments made while building step 2 (2026-10-24)

- Leads name a `runtime` (the seat runtime names), not `tool`.
- `bw sandbox` contains pi and Kimi leads, which must pass `bw sandbox verify` on their current
  verification key. Claude Code, Codex, and agy leads are contained by their own sandboxes and
  interactive approval with the user's real HOME, and are not gated on an attack pass in v0.
- Locks and handoff/checkpoint dirs are keyed by worktree (`sha1(worktree)[:12]`), not by
  repository: two worktrees of a repo are two independent tasks. Profiles stay per repository.
- A contained lead writes its worktree, the git data a commit needs, its runtime state, and
  `handoffs/<worktree-key>/` only; never locks, pins, quota, caps, or containment records.
  Git control files (hooks, config, config.worktree, other worktrees' admin dirs, the linked
  `.git` file) are write-denied, and bw reads git with hooks, fsmonitor, and external diff off.
- v0 detects caps automatically only through the Claude Code statusline; other leads use
  `bw cap`. `task` events and `panel.subject` move to later steps.

### Orca as the default canvas (2026-10-25)

Decided by the user: Orca is the recommended, default canvas and never a requirement.
Evidence: a spike on Orca 1.4.240 and a deep panel (Astra, an Opus 5.5 stand-in for the
Fable seat, Kimi, DeepSeek Flash; Argon absent on a usage cap). Every seat put the same
preconditions on a "default"; they are now part of this design rather than reasons to drop
it. Changes, by section:

- **Goals 4 and 7, the diagram, the config example, `bw start` step 6, the third-party
  table**: nodeterm → Orca. `bw start` opens `orca terminal create --command "bw lead"`;
  nodeterm can still host `bw lead` but is not driven.
- **The sandbox**: every profile denies Orca's control channel (read of its runtime dir,
  connect to its socket). Found by the panel (Astra) and confirmed by attack: under
  `sandbox-exec`, a contained process had Orca write outside the worktree.
- **Verification key**: hashes the user-level config and hook files each runtime loads;
  seats load none and get no `ORCA_*` variables.
- **Quota probes**: `quota: "orca"` replaces the planned credential-file probes for Codex
  and Kimi (bw now reads no vendor credential); agy keeps its documented `/usage`,
  OpenRouter its key endpoint, Claude its statusline. Orca readings are advisory under the
  freshness, units, window-scope, and no-cap rules there.
- **The inbox**: records, `bw inbox`, `bw answer`, and rotation notices stay; the cockpit's
  inbox view, answer endpoint, and `osascript` notifications are cut.
- **The cockpit**: read-only; Now keeps one per-plan line with source and age instead of
  live bars and countdowns.
- **Launch rules**: Orca's `agent launch`, orchestration, automations, and session restore
  are not used on bw-managed worktrees; setup checks Manual permissions and account
  auto-switch off.
- **`setup/paths.txt`** gains the vendor hook files Orca edits (CHANGELOG `[Unreleased]`).
- **Open**: Orca's agy hook answers `ask` to PreToolUse before its own gate, which can stall
  a headless agy run outside Orca (untested live: Google quota capped); the agy seat's own
  configuration directory covers seats, and the agy lead needs `orca agent hooks off` or an
  upstream fix. `BW_STATE_DIR` does not yet redirect the metrics log.

## Extraction gate

Roadmap criterion 4 (a second user has run setup from the README) is not met, and the
2026-09-03 seat runtimes are not built. This round builds in this repo anyway,
clean-room, because these pieces are what a second user needs most and the author's own
setup becomes `bw`'s first consumer. The roadmap records the amendment and its reason.
The privacy firewall is unchanged: the author's projects, paths, and plans live only in
their private config.

## Build order (each a separate plan)

Ordered so the stated pain (one plan running out) is relieved first and every later
step has data to show:

1. **esc model guidance and packs** — independent of code; ships first.
2. **v0 rotation**: config schema 2 (plans, leads, profiles, projects), `bw sandbox` and
   the lead containment attack, `bw lead` with the worktree lock, statusline quota for
   Claude, cap detection, transitions, `--use`/`--auto` override, handoff and
   checkpoints, `session`/`lead`/`task`/`rating` events. No cockpit yet; `bw status`
   prints the same facts as text.
3. **Seat runtimes and the three new seats** (`bin/seat`, setup-by-attack, runtime locks,
   verification keys including user-level config hashes, no user hooks and no canvas
   variables in seats), `panel.moderator`, `usage` token and cost fields — trials start.
   (Done 2026-10-25 as a fix to step 2: the Orca control-channel denies in both sandbox
   backends, `lead/sandbox.py` `CANVAS_CONTROL_DIRS`, with a model-free escape test and an
   `orca terminal create` probe in the attack script.)
4. **The starter** (`bw start`: update checks, re-verify, lead pick, workspace — an Orca
   pane when Orca runs, a terminal tab otherwise; Orca settings check).
5. **Quota sources and the inbox**: the Orca snapshot adapter (shape check, freshness,
   units, window scope, per-source snapshots and arbitration), agy `/usage`, OpenRouter
   key; `ask`/`answer`, `bw inbox`, `bw answer`, rotation notices.
6. **Registry and read-only cockpit** (Models, Usage, History, Now's plan lines first;
   Graph last).
7. **Jev shadow selection**, then audits and the promotion view.
8. **Docs pass in both repos**, then **team summary** with esc's ingest.

## Left to the builder

- Port, file layout under `bin/` and a new `cockpit/` package, and how the cockpit reuses
  dashboard data functions.
- Graph layout (layered is the requirement; force-directed is not).
- The Orca snapshot parser and the agy/OpenRouter parsers, against synthetic fixtures
  written by hand from the observed shape (never a captured response).
- Each lead runtime's usage-limit detection mechanism and containment mechanism, verified
  by attack; a runtime with no verifiable containment is not eligible as a lead.
- How the statusline command chains with a user's existing one.
- Visual design of the cockpit within: both themes, no external assets, counts not rates
  at small n, every number's source on hover.

## Lead scoring

Seats are scored by the moderator's verification of their findings. Leads need a
different evidence base, because a lead's output is *work*: code, decisions, panels it
moderated. The rule that keeps this honest is the same one as for seats: **no lead is
scored from its own testimony.** Every number comes from git, the metrics log, other
models' verified findings, or the user.

### Attribution

- A **task** is the span between two checkpoints a lead writes (goal, done, next). Each
  task records its Jev difficulty and stakes scores (the router already computes them),
  so leads are compared on like-for-like work, never raw averages across easy and hard
  tasks.
- A lead's **commits** are the commits `bw lead` sees appear on the worktree during its
  session (`git rev-list <head at start>..HEAD` at each checkpoint and at session end),
  recorded in the `session` event. Nothing is written into commits or the repo (no
  trailers, no notes).
- A panel's **subject** is recorded: when a code-review panel's diff range contains a
  lead's commits, its confirmed findings are attributed to that lead (as defects in its
  work), separately from the panel's moderator.

### What is measured, per lead (runtime + resolved model), per profile

| Dimension | Metric | Source |
|---|---|---|
| **Throughput** | tasks completed; median wall-clock per task by difficulty band; time to first commit after a handoff | checkpoints, `session` events |
| **Cost** | metered dollars per task; tokens per task; plan-quota share per task (estimate, from quota snapshots at task start and end) | cc-switch session rows, `usage`, `quota.json` |
| **Defects caught in review** (errors) | confirmed findings in panels on its commits, per task and per 100 changed lines, by severity | `panel` subject + `findings_detail` |
| **Escapes** (misses) | later fixes to its lines: a subsequent commit (any author) that modifies lines its commits introduced within 14 days and is labelled a fix, or reverts one of its commits; failing CI on its commits where `gh` is available | git blame / log on the worktree, optional `gh` |
| **Clean work** (things done right) | tasks whose commits drew no confirmed major+ finding and no escape in 14 days; **standout tasks**: clean tasks at the top difficulty band | the two rows above |
| **Moderation quality** | verdicts later overturned (a `panel-amend` changing a verdict, or a later panel confirming a finding it refuted); panels it was required to convene at a checkpoint but did not; records missing `findings_detail` | metrics log |
| **Runtime errors** | API errors and failed requests per task | cc-switch rows with error status, `StopFailure`-type hooks where the runtime has them |
| **Human load** | inbox asks per task; user overrides away from it (`bw lead --use` while it was current); the user's end-of-session rating | inbox, `lead` events, rating |
| **Continuity** | caps hit and switches it caused; handoffs it received that needed an `ask` in the successor's first task | `lead` events, inbox |

**The user's rating** is one keypress when `bw lead` ends a session: good / bad / skip,
with an optional note, appended as a `rating` event. It is the only subjective input,
shown separately, never blended into the other numbers.

### How it is shown

The cockpit's Models view gets a **Leads** table with the dimensions above as columns,
counts with n beside every rate, a difficulty-band filter, and a side-by-side compare of
two leads on the same profile (the question that motivated this: does agy-hosted Opus
lead as well as first-party Claude Code, and at what cost). There is deliberately **no
single composite score**: the trade-off between cost and escapes is the user's call, and
a composite hides which one moved. Small n is flagged rather than ranked (under 10
tasks in a band, the row shows counts only).

New log events: `task` (checkpoint-derived: lead session, difficulty, stakes, commits),
`rating`; `panel` gains `subject` (commit range and the lead sessions that authored it).
CHANGELOG records them with the others.
