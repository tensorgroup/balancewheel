# Leads and rotation (`bw`, since 2026-10-24)

*Updated 2026-11-27: `canvas: "ghostty"` (a Ghostty tab instead of a Terminal tab); a Claude Code lead gets the
lead protocol as a system prompt and `--add-dir` for the checkpoint dir.
Written 2026-11-24: moved here, unchanged, from the README's "Leads and rotation" section, so the README
stays a short entry point. The README's Quick start is the short path; this page is the full reference for
`bw`. Interactive walk-through: [docs/start/](https://tensorgroup.github.io/balancewheel/start/).
Corrected the same day after review: the panel's summary line, the `bw start` prefix of the seat-check
line, and a stale "not in v0" note on acting.*

The **lead** is the agent you work in for a project; it is also the moderator of every
panel it convenes. One plan usually runs out first, so a project's **profile** (config
schema 3; a schema-2 file still loads) names its leads in order, and `bw lead` picks one.
Plain `bw` is the everyday command: in a project it leads that worktree in this terminal (it
is `bw lead`, and lead flags work on it: `bw --use <lead>`, `bw --auto`), first saying when the
worktree has open inbox items or when `bw start` last updated tools more than a day ago;
outside a git worktree it shows `bw status`.

    bw                       # in a project: lead it here; elsewhere: bw status
    bw start [path]          # update tools, re-verify, pick the lead, open it in a Terminal tab (or Orca)
    bw lead                  # start the profile's lead for this worktree (what plain bw does in one)
    bw lead --use <lead>     # pin a lead for this worktree; bw lead --auto unpins
    bw status                # plans (quota, caps), worktrees, leads, how each is contained, open inbox items
    bw probe [plan...]       # read quota now (OpenRouter key, Orca when mapped), one line per plan
    bw inbox [--project] [--all]   # open asks from leads and notices from bw (--all: answered too)
    bw improve [note "…"|done <key>] # changes worth making, from how you work; add a note; mark one dealt with
    bw pack export --to <dir> [--dry-run] # regenerate the marked catalog block of esc's model-seats pack.yaml from the log
    bw triage install claude       # the question-triage hook in Claude Code's settings (uninstall removes it)
    bw answer <id> "<text>"  # answer an ask, or close a notice
    bw cap <plan>            # record that a plan is capped (for leads without a statusline)
    bw sandbox verify <rt>   # attack-test a bw-contained runtime (pi, kimi) and record a pass
    bw guard review          # audit the guard's stops and sampled allows, one evidence key at a time
    bw guard review --remark <id>  # mark a decision again, to correct a wrong mark
    bw guard verify <rt>     # attack-test the guard on a runtime (pi) and record a pass
    bw guard install agy     # add the guard's entry to agy's hooks file (uninstall: bw guard uninstall agy)
    bw panel <checkpoint> [target]  # convene a review panel (your lead does this for you; see below)
    bw panel status [<panel-id>]    # open panels of this worktree (also: ask, log, amend)
    bw seat list             # seats, their verification, and caps (the panel runs seats; you rarely run one)
    bw seat <id> "prompt"    # run one read-only seat by hand
    bw seat verify [<id>...] # attack-test each seat, fresh and resumed, and record a pass per seat id
    bw setup cc-switch       # write cc-switch's settings before its first launch (optional usage view)

First time? See "Start leading" at the end of this page.

**What a panel looks like.** You normally never type `bw panel`. Your lead convenes a panel
at each checkpoint of its profile, writes a request file, and keeps working. `bw` runs round
1: it renders one brief, runs the seats blind and in parallel, merges their findings, and
counts them. The lead then checks each finding against the code, relays disputed claims one
at a time, and logs the panel. You see three lines:

    Review panel (astra, opus; kimi absent: usage cap): 7 findings, 5 confirmed, 2 refuted, 0 dropped.
    Most important: the retry loop never stops on a 4xx.
    One dispute (astra against opus on the lock order). The code agreed with astra.

A panel has the states `queued`, `running`, `ready`, `logged`, and `failed`. A worktree has
up to three open panels and one running job. A panel runs at most one agy-runtime seat. A
lead reads its own view under `inbox-view/` and runs the wait helper `bin/bw-panel-wait`,
which `bw` copies into the lead's view. A lead that `bw` contains (pi, kimi) cannot read the
panel work dirs: its sandbox denies the reads. A lead in its vendor's own sandbox (Claude Code,
Codex, agy) gets no such deny. After you deploy this version, run `bw sandbox verify <runtime>`
once for each bw-contained lead runtime.

- **Switching.** When the current lead's plan is capped, bw stops the lead (its whole
  process group, confirmed gone), writes a handoff from git facts and the lead's last
  checkpoint, and starts the next eligible lead with it. It never switches mid-turn for a
  plan that is merely low. When the preferred plan has reset, the next `bw lead` returns to
  it. Every change is logged (`session`, `lead`, `rating` events).
- **What v0 detects by itself.** Claude Code reports its usage to its status line; install
  `bin/bw-statusline <plan>` as the `statusLine` command (set `BW_STATUSLINE_CHAIN` to keep
  your existing one) and caps are seen automatically. Other leads: run `bw cap <plan>`
  (Kimi Code can do it from a `StopFailure` hook in its config that runs `bw cap <plan>` on a
  quota error, or, when Kimi itself is the sandboxed lead, writes a `cap-signal` file next to
  `$BW_CHECKPOINT` that `bw` picks up).
- **One lead per worktree.** A lock the lead itself holds; a crashed `bw` cannot let a
  second writable agent in.
- **The lead protocol (since 2026-11-27).** Every lead is told how to keep a checkpoint, how to
  ask you something, how to take a handoff, and (with panel checkpoints) how to convene a panel
  (`lead/protocol/`). A Claude Code lead gets that text as a system prompt
  (`--append-system-prompt`), so the first thing you see in its session is not the rules. Its
  first message is only the handoff from a previous lead, and there is none for a fresh lead.
  Every other runtime gets the protocol and the handoff together as its first prompt. A
  Claude Code lead also gets `--add-dir` for the checkpoint dir, so its Write tool can save the
  checkpoint.
- **Containment.** Claude Code, Codex, and agy run with their own sandboxes and your real
  configuration (`contained by self`); whether they prompt is their lead's `autonomy`
  (next bullet). pi and Kimi run under
  `bw sandbox` — nono when installed, macOS `sandbox-exec` otherwise — and only after
  `bw sandbox verify` passed on their current version: writes stop at the worktree, the git
  data a commit needs, the runtime's own state, and the checkpoint dir; git hooks and
  config stay read-only; no app launches, Apple Events, or preference writes.
- **Autonomy per lead (config schema 3, `bw guard`, since 2026-10-25).** Each lead has an
  `autonomy`: `native` (Claude Code `--permission-mode auto`, Codex `--approve-for-me`: the
  runtime's own classifier decides), `guard` (bw's rules-only guard decides), `bypass`, or
  `manual` (today's prompts). The defaults are Claude Code and Codex `native`, agy and pi
  `guard`, Kimi `manual`. **Schema 3 moves Codex leads to `--approve-for-me` by default;** a
  schema-2 file keeps today's argv exactly. The guard starts **in shadow**: for a pi lead
  it logs what it would have decided and allows everything (prompts off, plus logging);
  nothing is blocked or asked. Since 2026-11-23 it can promote itself (see "Acting and the
  gate" below). `bw guard review` shows the stops and a sample of the allows,
  you mark each right or wrong, and the evidence accumulates per evidence key: profile,
  runtime, rules version, policy digest, judge model, and prompt hash (not per lead, and not per
  runtime version). A guarded agy lead
  is refused until agy's hook behaviour has been observed (a usage-capped spike), so
  `bw guard install agy` and `bw guard verify agy` refuse for the same reason; **under
  schema 3 an agy lead must set `autonomy` explicitly (`manual` or `bypass`) until agy's
  hooks are verified**, because its default is `guard`. `bw status` prints a `guard:` line
  per evidence key, and `bw lead` exits 7 when a guarded lead cannot start: the state dir
  overlaps the worktree, the socket path is over the length limit, agy's hook or spike is not
  ready, the guard clients could not be copied, the start-time self-test fails, or Node 22.6+
  is missing (a guarded pi lead needs it). Two per-profile keys steer the rules:
  `protected_branches` (default `main`, `master`; a push or a fetch into one asks) and
  `deploy_commands` (command prefixes such as `make deploy`; matched after the command's
  options, so `make -j4 deploy` asks too). See `balancewheel.example.json`.
  **Guarded pi leads run with `--no-extensions`:** a project-local extension in the worktree
  would load into the guard client's process, so pi's discovered extensions stop loading for
  a guarded lead; one you still want goes in the lead's `args` as `-e <path>`.
  Details: `docs/superpowers/specs/2026-10-25-bw-guard-design.md`.
  - **The judge, in shadow.** Set `guard.model` to an OpenRouter model id to judge gray calls.
    The key comes from the variable that `guard.key_env` names (default `OPENROUTER_API_KEY`).
    The lead gets its `allow` at once; the judge runs after the reply, and its verdict goes into
    `guard.jsonl`. This sends gray calls to OpenRouter, redacted and with zero data retention.
    After 3 judge failures in a row, bw stops calling the judge for 5 minutes and writes one
    `guard-judge-down` notice to `bw inbox`. Every judge failure or skip records the rules' own
    decision. `guard.on_judge_failure` is deprecated: it is still accepted and changes nothing.
  - **Acting and the gate (since 2026-11-23).** `guard.mode` is `shadow`, `auto`, or `acting`
    for each profile. A schema-3 profile you do not name is `auto`; a schema-2 file keeps
    `shadow`. An explicit `"shadow"` is a pin: it never promotes. At lead start, bw checks six
    conditions for the profile and runtime: enough reviewed marks (50 reviewed, 20 sampled
    allows, under 10% false stops, no false allow); a judge model; a passing `bw guard eval`;
    a guard attack pass; the one-time interactive check; and the cooldown after a demotion.
    **The guard promotes itself only after you set a judge model (`guard.model`) and the evidence
    holds.** With no judge it stays shadow: lead start prints one line, `bw status` says so,
    and `bw start` offers neither the interactive check nor the daily review. In acting, the rules
    decide first. A rule block blocks. A rule ask asks. The judge sees only gray calls and can
    answer only `allow` or `ask`; if it fails or times out, the rules' own decision stands, so
    acting is never weaker than shadow. bw steps back to shadow by itself when a sampled allow is
    marked wrong, the evidence key changes, the guard key is unverified, the interactive check is
    not on its current key, the eval fails or ends incomplete, or false stops reach 10%. The
    demotion line and `bw status` name what acting needs again for that reason: for a wrong
    sampled allow, the allow re-marked right (`bw guard review --remark <id>`, when the mark was a
    mistake) or a key change; for the eval, an eval pass; for false stops, under 10% of reviewed
    stops; for an unverified key, `bw guard verify <runtime>`; for the interactive check,
    `bw guard verify <runtime> --interactive`. After a demotion on evidence (a wrong allow, a
    failed eval, false stops), acting also waits until 7 days after the demotion and 5 new marks;
    marks made during the 7 days count. A key change, an unverified key, a changed interactive
    check, or an incomplete eval has no cooldown: the new key needs the gate's full counts. A
    profile pinned `acting` on an unverified key exits 7. `bw improve` suggests `auto` for a
    profile pinned `shadow` when every other condition holds.
  - **After upgrading.** pi and Kimi leads need `bw sandbox verify` again (the adapter hash
    changed); run `bw guard verify pi` once to record a guard pass, and `bw guard verify pi --interactive`
    once at a terminal (the guard key changed in 2026-11-23); and launch a Claude
    Code or Codex lead once interactively to confirm its native flag is accepted by your
    installed version. pi models reached through OpenRouter need the `openrouter/` provider
    prefix in the model id.
  - **Run `bw` from a checkout no lead edits.** A lead that can change the code of the next
    `bw lead` can change the guard.
  - **Limits, plainly.** The guard is not an execution boundary: test runners, `npm run`,
    `make`, and repo scripts run code the lead may have written, and the sandbox is the
    boundary. A repo whose own `core.hooksPath` points inside the worktree (husky, for
    example) lets a lead edit a hook your next unsandboxed commit runs; that is a
    sandbox-level gap the guard did not create. A pi lead on the `sandbox-exec` backend can
    read `guard.jsonl` (nono denies it). The log has no retention in v0. Expect a large gray
    share: the allow grammar is deliberately small, so most unfamiliar commands start gray.
    Another agent runtime started by the lead (a second `claude`, `codex`, or `pi`) is
    outside the guard: it is gray, so it asks, and what it does inside is not judged.
    When the guard is acting, a false block is worked around by hand, outside the lead.
- **`bw start [path] [--dry-run] [--no-update] [--no-verify]` (since 2026-10-25).** One command
  to begin a session in a project (the path defaults to the current directory). It refuses
  when `bw` itself lives inside the target worktree or its git directory (a lead could edit
  the next `bw lead`), then:
  1. **updates every tool you configured** under `tools` (`update`, `version`, `app`,
     `runtime`, `self_updating`, `timeout_s`; an unknown key under a tool warns), in parallel
     across installers and one after another within one (two Homebrew casks, two npm
     globals), one line per tool as it finishes. A tool counts as updated only when both
     versions read and differ. A tool whose runtime is in use by a running lead is skipped
     and tried next start; Orca and other self-updating apps are version-only.
     `updates.min_interval_hours` (default 0: every start) skips a tool that was updated more
     recently than that (`skipped-interval`). `--no-update` skips this step and keeps the
     tools' last recorded history.
  2. **re-verifies each lead runtime** whose containment key (pi, Kimi) or guard key (guarded
     leads) changed, on a pool with one worker per runtime, starting as that tool's update
     finishes. This runs whether or not anything updated, spends a little model quota, and
     leaves a runtime `pending` with the reason when the test cannot run (plan capped,
     version unreadable, agy's hook spike not done) or with `--no-verify`.
  3. **reads and shows the quota** of every plan the profile touches (OpenRouter's key
     endpoint and, for a plan that maps it, Orca's cache; see "Quota sources") and the lead it
     would start.
  4. **opens the worktree.** By default it opens a Terminal tab, through a small `.command`
     file under the state dir's `start/` that `open -a Terminal` runs. With `canvas: "ghostty"`,
     it opens a Ghostty tab that runs the same file through your login shell, by Ghostty's
     AppleScript (checked on Ghostty 1.4; the first run asks for macOS Automation access to
     Ghostty). When that fails, it falls back to the Terminal tab and says why first. With `canvas: "orca"`
     and Orca running, it opens an Orca pane titled `<worktree basename> lead` instead, through
     `orca terminal create --worktree path:<worktree> --command "<bw> lead"`; when
     `orca terminal create` fails, it falls back to the Terminal tab (bw says so first). A
     schema-3 config with no `canvas` key keeps Orca when it has `tools.orca` or an
     `orca_provider`. If no canvas works, bw prints the command and exits 1. A
     worktree that already has a lead is not opened again. `bw start` never starts the
     cockpit; it prints the `bw cockpit` hint.

  Runs are recorded in `start.json` in the state dir (`bw status` shows the `last start:`
  line). `--dry-run` runs only git (and `orca status` when the canvas is Orca) and changes nothing. **Runtime locks:**
  every `bw lead` holds a shared lock on its runtime from before the verification check
  through the session, so an update can land neither between the check and the launch nor
  under a running lead. `bw lead` waits for an update of its runtime that is in progress when
  it starts; a switch mid-session never waits on one (it stays on the current lead until a
  later poll finds the lock free).
- **Quota sources (`plans.*.quota`, since 2026-10-25).** Each plan names where its numbers
  come from: `statusline` (Claude Code reports them; `bin/bw-statusline`), `probe` (a
  documented vendor surface, chosen by `plans.*.probe`, which defaults from the plan's
  `vendor`: `openrouter` reads OpenRouter's key endpoint, `google` is agy's `/usage`), `orca`
  (an opt-in source: Orca's cached poll of that provider, named by `orca_provider`), `cli`
  (what a Codex seat run prints: its `rate_limits`; schema 3 only), or `none`. Every source
  writes its own snapshot under the state dir's `quota/`, and bw picks per plan by precedence:
  a `statusline` plan reads the statusline only, a `probe` plan its probe and then Orca (when
  `orca_provider` is set), an `orca` plan Orca, a `cli` plan its `cli` reading and then Orca
  (when `orca_provider` is set); the first source whose reading is known and
  fresh wins, and a plan with none reads `unknown` with every source's reason (a notice after a
  probe interval without one). Readings from `probe` and `orca` older than twice
  `probe_every_min`, dated in the future, or malformed are `unknown`. A `cli` reading is fresh
  for `rotation.cli_fresh_min` minutes (default 120, schema 3 only), and `unknown` after its
  window's reset. No CLI feeds `cli` yet: Codex 0.164.0 prints no `rate_limits`, so the source waits for a later Codex version. A seat attack
  (`bw seat verify`, `bw start`) runs in its own state dir and feeds no quota. bw does not read
  the output of a Codex lead's TUI. Between seat runs, rotation uses observed caps. `bw probe [plan...]`
  reads now and prints one line per plan; `bw start` and `bw lead` (at its start and each
  session boundary) do the same, and no network probe ever runs mid-session. In the poll loop
  `bw lead` re-reads Orca's local cache only, for display and freshness, and a failing re-read
  never stops the lead.
  - **Orca.** `orca_provider` (a name from `orca account list --json`: `claude`, `codex`,
    `kimi`, `antigravity`, …) is checked per provider for the shape bw reads, and a version
    change re-checks it, so an Orca update that renames a field makes only that provider's
    plans `unknown` ("Orca <version> changed shape: <path>"). Account-wide windows feed
    rotation only when Orca is the plan's source; on a `statusline` plan they ride along as a
    cross-check, shown and never chosen. **Pools** (Claude's Fable-only window, agy's
    per-model buckets) are ignored unless `orca_windows` maps one (`fableWeekly` or
    `bucket:<name>`) to lead or seat names: a lead named in a full pool does not start, and is
    skipped as a replacement when a cap forces a switch, while a pool alone never moves a
    running lead or caps a plan. Pools come from Orca's freshest known reading whichever
    source won the plan's windows. A malformed pool is dropped with a note; the plan's windows
    stand.
  - **OpenRouter.** `probe: "openrouter"` sends the key from `key_env` (default
    `OPENROUTER_API_KEY`) to `https://openrouter.ai/api/v1/key` and nowhere else; redirects are
    refused and the key appears in no file, output, or error. With `budget_usd_month > 0` the
    plan gets a `budget` window: the UTC month's spend (`usage_monthly`) against it, reset on
    the 1st. If the key's response lacks `usage_monthly`, bw counts spend from its own last
    reading of the previous month (carried forward as the month's baseline, so the old month's
    last hours count too: spend is overstated, never understated); with no reading from before
    the 1st, monthly spend reads **unknown**, and the plan has no budget window and says so. The key's own credit
    limit is shown, never a window. A budget of 0 means no budget window.
  - **agy and Google.** agy's `/usage` output has not been observed yet, so `probe: "agy"` reads
    `unknown` ("awaiting Task G") and runs nothing. Until then a Google plan can read Orca's
    `antigravity` entry (`quota: "orca"`).
  - **Caps.** A reading never creates or extends a cap. bw removes a cap past its
    `resets_at` once a fresh known reading taken at or after that time exists, else
    `cap_expiry_hours` after it; an unexpired cap is never touched by a reading.
- **Question triage (since 2026-10-30, off by default).** A decision model settles the questions
  that do not need you. `triage.mode.<profile>` is `off`, `shadow` (compute and log, change nothing)
  or `acting`. A lead's ask needs a `Recommendation:` line; bw drops anything that reads as outward
  or irreversible straight into your inbox with no model call, and asks the model four yes/no
  questions about the rest. The thresholds live in config. A settled ask reaches the lead as an
  `@bw decision` block, which says it is not your answer and grants nothing. Claude Code pop-ups go
  through a hook (`bw triage install claude`): a settled pop-up is denied with a note to take the
  recommended option, anything else shows as usual. Any failure leaves the question as it would
  have been. `bw inbox` counts what was decided, `bw inbox --all` lists it, and `bw answer <id> "…"`
  reverses a decision. The question's text, best-effort redacted, goes to the model provider with
  zero-data-retention routing. Guard approvals and permission prompts never pass through triage.
- **The inbox (since 2026-10-25).** A lead asks you something only you can answer by writing
  a file, not by printing it: it writes `$BW_ASKS/.tmp-<random>` and renames it to
  `$BW_ASKS/<random>.ask`, one question per file (`BW_ASKS` is a directory the lead can
  write). bw reads only complete `*.ask` regular files, bounded in size and number (50 per
  session, then one notice), and records each in `inbox.jsonl` in the state dir, which only bw
  writes; it drains once more when the lead exits or is switched, so open asks reach the
  handoff. `bw inbox` lists open asks and bw's own notices (all leads capped, a pinned lead
  capped, a guard that could not be reached, an ingest problem); `--project` limits it to
  this worktree and `--all` adds answered ones. `bw answer <id> "<text>"` answers an ask or
  closes a notice, and `bw status` shows open items. Answers reach the lead through a file
  bw rewrites (`BW_INBOX`, `inbox-view/<worktree-key>/answers.md`) that holds **only your answers**,
  never ask text, and that the lead can read but not write, so everything in it is your words; a
  lead cannot read another worktree's answers or the inbox record itself. Whatever a lead
  wrote is shown escaped and shortened wherever bw prints it. A `bw lead --use` that changes
  the lead the profile order would have picked is logged as an `override`.
- **Settings checks (`bw status`, `bw start`).** `orca:` warns when Orca's agent permission
  default (or any per-agent override) is a full-access value, when telemetry is on, or when
  it holds managed Claude or Codex accounts; it says so when Orca is installed but not running.
  `cc-switch:` checks the settings `bw setup cc-switch` wrote. bw reads Orca's profile
  database only through a sealed copy in the state dir, which every sandbox denies.
- **Where you watch it: a Terminal tab (Orca is one option).** `bw start` opens `bw lead` in
  a Terminal tab. [Orca](https://github.com/stablyai/orca) is an MIT desktop app that hosts
  agent panes per worktree, with notifications and a per-plan quota view. To use it, set
  `canvas: "orca"`; `bw start` then opens `bw lead` in an Orca terminal
  (`orca terminal create --command "bw lead"`). Without Orca, bw loses the canvas pane, Orca's notifications ("an agent is waiting"), the Codex and Kimi quota readings, the Claude per-model pool (`fableWeekly`), and the Gemini reading (Orca's `antigravity` entry; agy's own probe still awaits its shape). Everything else works the same in a Terminal tab.
  Never launch a lead through Orca's own agent launch. It bypasses permissions and bw's
  worktree lock. Orca adds hooks to every vendor's config when it first starts. A sandboxed
  process can drive Orca's control channel unless the sandbox denies it, and every `bw`
  sandbox denies it. Read [docs/orca.md](orca.md) before you install Orca.
- **The cockpit and the registry (`bw cockpit`, `bw registry`; since 2026-10-26).** `bw cockpit
  [--port N] [--no-open]` serves one read-only page on `127.0.0.1` until Ctrl-C, with five tabs.
  **Models**: seat rows (panels, confirmed findings, unique catches, standout panels, dispute
  wins, cost where reported) and a **Leads** table (sessions, minutes with lost and unparsed
  sessions left out, cap switches, overrides, ratings, asks; the task-based columns say they
  are pending until `task` events exist), with counts only below 10 verdicts. **Usage**: tokens,
  requests, and cost per tool and model over 30 days. **History**: panels, disputes, findings,
  and session and lead markers. **Now**: per plan the windows, pools, source and age of the
  reading, cap, and cross-check, per worktree the current and pinned lead and open asks, and
  open watches. **Graph**: the registry's edges as a layered diagram coloured by health. A
  number nobody reported shows as "unavailable", never 0, and a tab whose data cannot be read
  says why instead of blanking the page.
  Security in two sentences: the page opens through a one-time link whose token is exchanged
  once for an `HttpOnly`, `SameSite=Strict` cookie, and every request needs that cookie, a
  `127.0.0.1` or `localhost` `Host`, and `GET`. It serves only on a port every lead sandbox
  denies (`cockpit.port`, default 8765), so a contained lead cannot reach it; the denied ports
  are part of the containment key, so **after changing `cockpit.port`, `bw lead` refuses pi and
  Kimi until `bw sandbox verify` passes again** (`bw start` re-verifies by itself). Unknown `cockpit.*`
  keys warn and are ignored, and idle connections close after 10 s. A page on another localhost
  port can set a second cookie with the same name and lock the cockpit out (403, nothing
  disclosed); that denial of service is accepted, and restarting `bw cockpit` gives a fresh
  link. `bw registry [--print]` writes the derived snapshot the Graph draws
  (`registry.json` in the state dir: tools, plans, models, seats, leads, profiles, projects,
  edges, each node with a health). A runtime a lead or seat uses without a `tools` entry still
  gets a tool node, marked `configured: false`. Delete the file and nothing is lost.
- **Usage data: cc-switch (optional).** The Usage tab reads [cc-switch](stack.md)'s
  session-log table from a sealed copy in the state dir; bw never opens or writes the original,
  and an unreadable row is counted in `skipped_rows`, not guessed. Tokens or costs a row does
  not carry show as unavailable. Keep its proxy, failover, and every config, prompt, MCP, and
  skill sync off, and never switch a provider in its UI; `bw setup cc-switch` writes the
  settings that do this before its first launch.

## Start leading (first run)

1. **Install `bw` from a checkout no lead works in.** A lead can edit its worktree, so if
   `bw` ran from that same checkout the lead could change the guard and the sandbox profiles
   of the next `bw lead`. Keep a separate clone you only ever update:

       git clone https://github.com/tensorgroup/balancewheel ~/.local/share/balancewheel
       ln -sf ~/.local/share/balancewheel/bin/bw ~/.local/bin/bw
       ln -sf ~/.local/share/balancewheel/bin/bw-statusline ~/.local/bin/bw-statusline

   Update it with `git -C ~/.local/share/balancewheel pull`. A clone you develop
   balancewheel in stays separate. `bw` needs Python 3.9+; a guarded pi lead also needs
   Node 22.6+.
2. **Name your leads** in the config (`schema_version` 3): each lead's `runtime`, `model`,
   `plan`, and an `autonomy` where the default does not suit (an agy lead needs `manual` or
   `bypass` for now). List them in each profile's `lead_order`, map project paths to
   profiles under `projects`, and validate with
   `python3 ~/.local/share/balancewheel/metrics/log.py config`.
3. **Let Claude Code leads report quota** through the status line, in
   `~/.claude/settings.json` (leave out `BW_STATUSLINE_CHAIN` if you had no status line):

       "statusLine": {"type": "command",
         "command": "BW_STATUSLINE_CHAIN=\"<your previous command>\" ~/.local/bin/bw-statusline <plan>"}

4. **Verify what `bw` contains**, once per runtime version: `bw sandbox verify pi` and
   `bw sandbox verify kimi` for pi and Kimi leads (`bw lead` refuses them without a pass),
   and `bw guard verify pi` for a guarded pi lead (without it, the lead starts with a note).
   Each run spends a little model quota. Changing `cockpit.port` changes the containment key
   (the attack probes every denied port), so verify again after it.
5. **Lead.** In a project's worktree, `bw status` shows plans, leads and guard lines; `bw
   start` updates and re-verifies, then opens the profile's lead (a Terminal tab or an Orca
   pane), or `bw lead` starts it in the terminal you are in. Quit the
   lead to end the session; `bw guard review` later shows what a guarded lead's guard
   logged.
6. **Seats.** You do not need `bw seat` in normal use: the panel runs the seats. `bw start`
   checks changed seats in the background after the lead opens. It prints `bw start: checking
   seats in the background (log: <path>)`. `bw seat verify` does the same check by hand, and
   `bw seat list` shows the state of each seat. Each seat has its own login step:
   - **Claude seat.** The keychain login does not carry over to the seat's private home. Run
     `claude setup-token` once. Export `CLAUDE_CODE_OAUTH_TOKEN` in the shell that runs `bw`.
     Without it, the Claude seat is absent with a "no login" reason. Put the export in your
     shell profile: the Terminal tab that `bw start` opens does not get variables you exported
     only by hand.
   - **Codex seat.** It uses the real `~/.codex/auth.json`. The sandbox grants read and write
     on that one file only.
   - **agy seat.** It keeps the real home, because its login does not carry over. Only its own
     `~/.gemini/antigravity-cli/` folder is writable. Its memory dirs (`knowledge`, `brain`,
     `implicit`, `annotations`) are read-only. Residual risk: the seat can edit agy
     conversation files. When the lead is agy, the panel leaves the agy seat out.
   - **pi seat.** It needs `OPENROUTER_API_KEY` in the environment. OpenRouter is the only
     backend for now.

   A seat refuses a repo whose project files load code, unless the seat's own "ignore project
   config" flags cover those files. The seat attack proves each flag with a positive control.
