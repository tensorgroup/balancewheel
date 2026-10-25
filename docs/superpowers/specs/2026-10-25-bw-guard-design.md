# `bw guard` — autonomy without approval prompts — design

*Written 2026-10-25; revised the same day after a deep design review by three seats
(Astra, Kimi, DeepSeek Flash; 47 raw findings, 20 after dedupe, none refuted; the Fable
seat's Opus stand-in timed out and Argon was absent on a usage cap).
The findings are folded in, not appended; "Decisions from the review" lists the rulings.
The user skipped the question round, so intent below is from their own words and every
other choice is marked as the author's call. Versions read on the author's machine this
date: Claude Code 2.1.310, Codex CLI 0.154.0, agy 1.4.7, pi 0.85.1, Kimi Code CLI 2.3.1.
Revised again 2026-10-25 after the v0 build: "Amendments from the v0 build" below names twelve decisions the build made or changed; where it and an earlier section disagree, it wins. Revised 2026-10-25 after spike 1a: pi hook behaviour observed (see "What exists today"); agy spike 1b pending quota reset. Builds on `2026-10-24-profiles-lead-rotation-cockpit-design.md` (leads, profiles,
`bw sandbox`, handoffs, the inbox) and `docs/orca.md`.*

## Intent

The user runs agents with permission prompts bypassed, nearly always (their shell aliases
`claude` to `--dangerously-skip-permissions`), and wants the same for every model: "I don't
know if it makes sense to create this huge beautiful platform that I continually have to
approve work for." They asked whether a fast model could "quickly decide if it thinks the
current command is reasonable to run and is actually safe", and said "maybe we don't need it
for Claude".

So the goal is a lead that **never stops for routine work**, with a cheap second look at
the few actions that are irreversible or reach outside the machine — on every runtime, not
only those whose vendor ships such a judge.

What this is **not**: containment. The OS sandbox decides what a lead *can* do; it caught
the 2026-10-25 Orca escape structurally, where no command judge would have recognised
`orca terminal create --command "touch …"` as dangerous. A judge is fooled by the same
prompt injection that fools the agent, and it cannot know what the user asked for (the lead
writes its own checkpoint). So `bw guard` answers a narrower question — *is this action of a
kind that is irreversible, reaches outside the machine, or falls outside ordinary
development work?* — and is scored on that.

Note on today's behaviour: shell aliases never reach `bw lead`, which runs the binary
directly. A Claude lead started by bw today runs in Claude Code's built-in default mode,
`auto` (the default for interactive sessions since 2.1.283; observed as "auto mode on" in
the 2026-10-25 spike).

## What exists today (recon, 2026-10-25)

| Runtime | Native autonomous-with-judgment mode | Hook that can block a tool call | Can a hook ask the user? |
|---|---|---|---|
| Claude Code | `--permission-mode auto`: a separate classifier blocks actions that escalate beyond the request, touch unrecognised infrastructure, or look driven by hostile content; explicit `ask` rules still prompt; after 3 consecutive or 20 total blocks it falls back to prompting ([docs](https://code.claude.com/docs/en/permission-modes)) | PreToolUse | yes |
| Codex | `--approve-for-me`: requests that would need approval go to an automatic reviewer, inside the workspace-write sandbox. It reviews *approval requests*, not every action the sandbox already permits | `hooks.json` `pre_tool_use` | not verified |
| Kimi Code | `--yolo` = "Ask When Needed": routine actions run, risky ones ask | `[[hooks]] PreToolUse` — fail-open by Kimi's own docs | n/a (advisory only) |
| agy | none: prompts, `--dangerously-skip-permissions`, or `--sandbox` | `PreToolUse` in its hooks config answering `{"decision":"allow\|ask\|deny"}` | yes (`ask`) |
| pi | none: no permission system | an extension's `tool_call` handler (`{block, reason}`) | yes: `ctx.ui.select` when `ctx.hasUI` (pi's own `permission-gate.ts` example) |

### pi hook spike (1a, 2026-10-25, pi 0.85.1)

Observed with a throwaway extension loaded by `-e`, DeepSeek V4.1 Flash through OpenRouter,
`pi -p` with stdin closed. The agy half (1b) is pending until Google quota resets
(about 2026-10-31); nothing in the agy row above is verified yet.

- **Event shape.** `tool_call` carries `{type, toolName, toolCallId, input}`; no cwd. The
  handler's second argument (`ctx`) has `hasUI` (false under `-p`) and `cwd` (the process cwd).
  Inputs: `bash {command}`, `read {path}`, `write {path, content}`,
  `edit {path, edits:[{oldText,newText}]}`, `grep {pattern}`, `find {pattern}`, `ls {path}`
  (path may be absent on `grep`/`find`). Fixtures: `tests/fixtures/guard/pi_*.json`.
- **Default tools.** Only `bash`, `read`, `write`, `edit` are on by default; `grep`, `find`,
  `ls` appear only with `--tools`. A guard sees whatever is enabled.
- **`-e` and `--no-extensions`.** An explicit `-e` still loads with `--no-extensions`.
- **Fail closed at load.** A missing `-e` path or one that fails to parse makes pi print
  `Failed to load extension` and exit 1 before any model call, so a lead cannot run
  unguarded through a bad path. A handler that throws at run time blocks the call (the model
  sees the error text as the tool result).
- **Block reaches the model.** `{block:true, reason}` comes back as the tool result text,
  and the command does not run.
- **Order.** Several `-e` extensions run in the order given; project-local extensions load
  after them. The first extension to block short-circuits later ones (a later extension never
  sees a blocked call). User-level extensions were not exercised.
- **Resume.** `-c` (continue) or `--session <id|path>` with `--session-dir`; `-e` applies
  to the resumed run as to a fresh one.
- **Under `bw sandbox`, both backends (nono, sandbox-exec).** The extension connected to a
  unix socket in a directory with no write grant, on both. From inside, `rm` of the socket and
  creating a file in that directory failed (`Operation not permitted`); the socket survived.
  Gate for pi: passed.

Judge measurement: DeepSeek V4.1 Flash on OpenRouter, five representative commands at a
60-token output cap: 0.67–1.15 s and about $0.00004 per call; four correct verdicts, one
empty reply, two truncated. A real judge needs a larger output budget and must treat a
malformed reply as a failure.

## Goals

1. **Native first.** A lead whose runtime ships a judge runs in that mode by default (Claude
   `auto`, Codex `--approve-for-me`). No bw layer is stacked on top unless the user opts in.
2. **Fill the gap.** agy and pi leads get `bw guard`.
3. **Never stop for routine work.** Target under one human question per working hour per
   guarded lead, measured (native runtimes don't expose their asks, so the target is only
   measurable for guarded leads).
4. **Rules first, a model only where the rules leave a gap** — and the model tier ships
   only after the shadow record shows how much the rules leave open.
5. **Evidence like everything else here**: shadow before acting, audits that can see a
   wrong *allow*, evidence tied to the versions that produced it.

## Non-goals

- Containment, or a reason to loosen any sandbox.
- Seats: they are read-only by OS enforcement.
- A proxy or harness replacement: the runtime calls the hook; the guard never sits between
  the user and the agent.
- Reading transcripts, or deciding what the user meant.
- A second judging model (no oracle exists for an `ask`, so a second model adds latency and
  a second injection surface without adding signal).
- Bulk logging of native runtimes' tool calls for symmetry.

## Anti-goals

- Approval spam, including a storm during a judge outage.
- A silent block: every block or ask carries a one-line reason both the agent and the user
  see.
- An approval the agent can give itself.
- Command text, or anything that might hold a secret, sent to a model before redaction, or
  written to anything that leaves the machine.
- A guard that "passes" its attack test because the sandbox blocked the action while the
  guard never ran.

## How it works

### The trust boundary: a broker in `bw lead`, a thin hook in the runtime

A pi lead runs under `bw sandbox`, whose grants deliberately exclude bw's state directory
and (under nono) most reads; whatever an in-sandbox hook could read or write, the agent it
constrains could read or write too. So the decision is made **outside** the lead:

- `bw lead` (already the unsandboxed parent of every lead) runs a **guard broker**: it holds
  the rules, the judge's API key, the decision log, the inbox, and the counters.
- The runtime's hook is a thin client. It normalises the tool call and sends it to the broker
  over a per-session channel `bw lead` creates before starting the lead (author's call: a
  unix socket in the lead's checkpoint dir, the one bw-state path a contained lead may
  already write, and denied by nothing; the broker accepts only requests carrying the
  session's random id, which the hook gets through its environment). The hook returns the
  broker's answer in the runtime's shape.
- The agent can send the broker requests, which only asks for a decision. It cannot approve:
  approvals happen in the runtime's own UI, typed by the user (below). There is **no**
  `bw guard allow-once` command in v1.
- If the hook cannot reach the broker, it blocks with "guard unavailable" (acting) or allows
  and leaves a marker (shadow); `bw lead` counts markers and raises one notice.

### Autonomy per lead (config)

`leads.<name>.autonomy` ∈ {`native`, `guard`, `bypass`, `manual`}:

| | Claude Code | Codex | agy | pi | Kimi |
|---|---|---|---|---|---|
| `native` | `--permission-mode auto` (default) | `--approve-for-me` (default) | rejected | rejected | `--yolo` |
| `guard` | rejected in v1 | rejected in v1 | prompts off (`--dangerously-skip-permissions`) + guard hook (default) | guard extension (default) | rejected: hooks fail open |
| `bypass` | `--permission-mode bypassPermissions` | rejected: Codex's bypass flag also removes its sandbox | `--dangerously-skip-permissions`, no hook | no extension | `--auto` |
| `manual` | `--permission-mode default` | default approval policy | interactive prompts | n/a | default |

The loader rejects unsupported combinations and any `lead.args` that contradict the
autonomy (for example a permission flag in `args`). `bw lead` records requested and
effective `autonomy` on the `session` event, plus the guard version when guarded. Every
lead keeps its containment regardless of autonomy (`bw sandbox` for pi; Codex's own
sandbox; agy `--sandbox`).

Opt-in only (author's call, honouring "maybe we don't need it for Claude"):
`guard.rules_on_native: true` installs the rules-only hook (no model) on Claude and Codex
leads too, so the deny class applies uniformly. Default off.

### The decision path (in the broker)

**Input**, normalised by a per-runtime adapter into a typed record: runtime, tool identity
as the runtime reports it, the tool's structured arguments (for a shell call the full
command; for writes every path, canonicalised; for MCP and other tools the server, tool,
and arguments), cwd, worktree. Size-capped. No checkpoint text: the lead writes it, so it
cannot stand for the user's intent (an injected lead would write the goal that authorises
its next command).

**1. Rules** (no model). Each shell command is split into segments **without** losing
pipeline structure; the combined decision is the strictest across segments
(`block > ask > allow`). Unknown or unparseable syntax never reaches `allow` by rule.

- *block* (policy, never user-approvable in the hook path): reading or writing credential
  paths (imported from `lead/sandbox.py`'s `SECRET_READS`, not restated); changing git hooks
  or `core.hooksPath`; driving a canvas's control CLI (`orca terminal …`, `herdr pane …`).
- *ask*: force-push; `rm -r`/`rm -rf` resolving outside the worktree; download piped into an
  interpreter; `git reset --hard`, `git clean -fdx`, branch deletion; `git push` to a
  protected branch; `gh pr merge`, `gh release`, `gh repo delete`; publishing (`npm
  publish`, `twine upload`, `cargo publish`, `docker push`); `terraform apply`, `kubectl
  apply|delete` against a non-local context; deploy scripts the profile names; anything that
  sends a message (`gh issue comment`, mail and chat CLIs, MCP tools that post).
- *allow*: a small explicit grammar of plain commands — reads and searches, `git`
  subcommands that only read or change local history non-destructively, the repo's test,
  lint, and build commands, and file edits whose every path is inside the worktree. Running
  the repo's own scripts is trusted as the repo itself is (stated, not hidden: a hostile
  test script is the sandbox's problem, not the guard's).
- Everything else — compound commands with substitution, interpreters (`python -c`,
  `sh -c`, `node -e`), `xargs`, `find -exec`, aliases, redirections outside the worktree,
  unknown tools — goes to the judge (v1) or to `ask` (v0, before the judge exists).

**2. Judge** (v1, after the shadow record). One model (`guard.model`, DeepSeek Flash by
default; must differ from the lead's own model, recorded). Temperature 0, reasoning off,
schema-constrained `{"decision": "allow|ask", "reason": "..."}` with an output budget large
enough for both fields, a total deadline of 2 s including at most one retry on timeout or
5xx. The judge can only choose between `allow` and `ask`; it never overrides a rule.

**Redaction before anything leaves the machine.** Bearer tokens, `KEY=`/`TOKEN=`/`SECRET=`
assignments, `--password`-style flags, credentialed URLs, and header values are replaced
before the request; a command where a recognised secret is load-bearing is not sent at all
and becomes `ask`. The judge's `reason` is sanitised the same way before it is shown or
logged. Redaction is best-effort, stated as such.

**3. Asking the user.** agy: `decision: "ask"` shows agy's own approval prompt. pi:
`ctx.ui.select` with the command and the reason, as pi's own permission-gate example does;
with no UI (a headless run), block with the reason. The user's answer is the only approval;
the broker records it and may remember an exact-action approval for the rest of the session
(keyed by a digest of the full normalised call and cwd), so the agent's retry doesn't ask
twice.

**4. Failure.** Judge timeout, HTTP error, empty or malformed reply after the one retry:
rule decisions still apply; a gray-zone call follows `guard.on_judge_failure` ∈ {`allow`,
`ask`}, default `allow` with a logged `judge-failed` (author's call: a judge outage is not
evidence a command is dangerous, and the user's priority is not being stopped). A circuit
breaker opens after 3 consecutive failures: gray calls skip the judge for 5 minutes, and the
user gets **one** inbox notice per outage, not one per call.

**5. Cache.** Exact-repeat decisions (same normalised call and cwd within the session) are
reused for 10 minutes; concurrent hook calls are served in order by the broker.

### Shadow, acting, audits

`guard.mode` ∈ {`shadow`, `acting`} per profile, default `shadow`. In shadow the broker
decides and logs, and the hook always allows. Be plain about what that is: for agy and pi,
shadow is a bypass-class posture (no vendor prompts, nothing blocked) plus logging — the
same as how the user runs today, and the only way to gather evidence without interruptions.

`bw guard review` (text) shows recent would-have-asked/blocked decisions **and a random
sample of allows**, each marked `right`/`wrong` by the user, with separate false-stop and
false-allow counts, rule and judge decisions counted apart. Promotion to acting is the
user's edit; `bw status` says when evidence supports it: at least 50 reviewed decisions,
at least 20 of them sampled allows, under 10% false stops, zero false allows on a
synthetic adversarial set (`bw guard eval`, below). Evidence resets when the rules, the
judge model, its prompt, the parser, or the runtime version changes. In acting, a sampled
allow marked `wrong` drops the profile back to shadow and says so.

`bw guard eval` runs a committed, synthetic set of tool calls (benign, dangerous, obfuscated,
injected "ignore your instructions" text inside arguments) through rules and judge and
reports the result per version — fixtures written by hand, never captured from real
sessions (privacy firewall).

### Verification by attack

Per runtime and version, on fresh and resumed sessions, recorded like a containment pass,
and part of the verification key (guard code hash, hook/extension config hash, runtime
version, and third-party hook files that share the runtime's config, such as Orca's):

- an action the sandbox **permits** but the guard blocks (a harmless marker command on the
  block list): the marker must not appear — this proves the guard ran, not the sandbox;
- each ask category: the runtime's prompt appears (agy, pi with UI) or the call is blocked
  (pi without UI);
- injected text inside arguments that claims authorisation;
- failure injection: the hook missing, the broker unreachable, a malformed broker reply —
  none may produce an unreviewed execution in acting mode;
- hook composition: with Orca's agy hooks installed, bw's `allow` must not become a prompt
  (Orca's agy hook answers `ask` before its own gate — `docs/orca.md`); the fix is
  `orca agent hooks off` for agy or an upstream change, decided by this test.

An acting `guard` lead whose current key has no pass is refused, like an unverified
`bw sandbox` lead.

### Logging and privacy

- Decisions go to `state_dir/guard.jsonl`, written only by the broker, append-only and
  locked like the metrics store; review marks are separate amendment records, never edits.
  Fields: ts, project, lead, runtime, tool, the redacted call (full length; local only) and
  its digest, rule or judge, decision, reason, latency, judge model and prompt hash, guard
  version, mode, outcome.
- The metrics log gets a `guard` event with counts only, read by `metrics/store.py`
  (`session.autonomy` added to what `store.load()` keeps), so the scoreboard and the team
  summary never carry commands.

### Network and cost

The judge call to OpenRouter is new Balancewheel-caused traffic; the 2026-10-24 spec's
"complete list" is amended (below). At about $0.00004 and ~1 s per judged call the money is
negligible; the real cost is latency, which the cache, the rules, and the 2 s cap bound and
the shadow log measures (p50/p95 per runtime).

## Build order

1. **v0, rules-only, shadow**: the broker in `bw lead`, the agy and pi hooks, the rules,
   `guard.jsonl`, `bw guard review` with allow sampling, the autonomy config (schema 3) and
   native flags for Claude and Codex, the attack tests. No model.
2. **Measure** a week or more of shadow: how many calls the rules leave open, latency,
   would-be questions per hour.
3. **v1, the judge**, if step 2 shows a gray zone worth judging: redaction, the judge call,
   failure handling and circuit breaker, `bw guard eval`.
4. **Acting**, per profile, when `bw status` says the evidence supports it.

## Decisions from the review

- The guard runs as a broker outside the sandbox; the in-runtime hook is a thin client
  (all three seats; Flash rated it critical).
- No `allow-once` CLI: approvals come only from the runtime's own UI (Astra, Kimi, Flash).
- Checkpoint text is not judge input (all three).
- Redaction before the request, not only in the log (all three).
- Promotion samples allows and resets on version changes (all three).
- Conservative allow grammar; destructive local git moved to `ask`; interpreters and
  substitutions never allowed by rule (Astra, Kimi, Flash).
- Typed normalisation of every tool, unknown tools never allowed by rule (all three).
- Hook health in the verification key, with a sandbox-permitted marker test (all three).
- Only the consent class is a hard block; other former "deny" rules are `ask` (Flash).
- pi asks through `ctx.ui.select` instead of deny-and-re-run (Astra; verified in pi 0.85.1's
  bundled example).
- Rules-only v0 before any model (Flash; the others' failure findings point the same way).
- The judge must differ from the lead's model (Flash).
- Rules on native runtimes: offered as opt-in `guard.rules_on_native`, default off. Flash
  argued for always-on; ruled for the user's "maybe we don't need it for Claude", and
  because a Claude or Codex lead is not under `bw sandbox`, so the deny class there guards
  against nothing the lead could not already do.
- Judge failure in acting: default `allow` for gray calls (rule decisions still apply).
  Flash argued allow; Kimi and Astra argued ask. Ruled allow for the user's stated
  priority; `guard.on_judge_failure: ask` is one line away.
- Second judging model: no (all three).

## Amendments to earlier documents (named, not silent)

- **2026-10-24 spec, Non-goals ("network traffic Balancewheel itself causes, the complete
  list")**: adds the guard judge's calls to OpenRouter when a profile's guard runs v1.
- **2026-10-24 spec, leads**: `autonomy` joins `runtime`, `model`, `plan`, `args`; the
  "contained by self = sandbox + interactive approval" argument for Claude, Codex, and agy
  leads is narrowed: their containment is their sandbox; approval is now `autonomy`'s
  business.
- **Config schema 3** (`metrics/config.py`): `leads.*.autonomy`, top-level `guard`
  (`model`, `mode` per profile, `on_judge_failure`, `rules_on_native`); version 2 files load
  unchanged with today's behaviour (`native` for Claude and Codex is today's Claude
  default; Codex moves from its default approval policy to `--approve-for-me` only when the
  user sets it, author's call). CHANGELOG `[Unreleased]` in the same commit.
- **Log events**: `guard` (counts), `session.autonomy`.
- **The inbox**: gains guard outage notices.

## Amendments from the v0 build (2026-10-25)

What shipped: a rules-only guard in shadow for pi; the agy guard built but refused until its
hook spike (1b) runs, which waits on a Google usage cap (about 2026-10-31); Claude Code
`--permission-mode auto` and Codex `--approve-for-me` as native autonomy; config schema 3.
The code wins over this document where they differ. The decisions, as named amendments:

1. **Normalisation lives in the broker, not the hook.** The hook forwards the runtime's raw
   payload; the broker's normaliser builds the typed record. One normaliser, in Python,
   unit-tested; the same trust boundary with a thinner client. Until the agy spike, every
   agy payload normalises to `other` (gray, so ask) rather than a guessed shape.
2. **The broker's reply is what the hook must do.** In shadow the broker logs its real
   decision and replies `allow`. The hook has mode logic only for failure: an unreachable
   broker, an error or malformed reply, or no reply within 2 s means allow plus a marker in
   shadow, and block ("guard unavailable") in acting. The hook is inert only when
   `BW_GUARD_MODE` is unset (a hand-run agy, or an agy lead run as `manual` or `bypass`);
   the variable set with the socket or token missing is a failure, not inertness.
3. **Rejected rather than ignored in v0:** `guard.mode: "acting"` and
   `guard.rules_on_native: true` fail config validation. The broker and hooks implement
   acting because the attack harness needs it, but no real lead can reach it, so v0 needs
   no demotion or promotion gate.
4. **Gray means ask in v0, logged apart.** Every decision carries `source` (`rule` or
   `gray`). The gray share is step 2's measurement and is expected to be large: the allow
   grammar is small on purpose, and `jq`, `awk`, `npm install`, `git checkout`, and MCP
   reads all start gray.
5. **Autonomy and today's behaviour.** Schema 3 defaults: Claude Code `native`, Codex
   `native`, agy `guard`, pi `guard`, Kimi `manual` (the spec named no Kimi default; `manual`
   is today's). Codex's "only when the user sets it" is read as being about version-2 files:
   writing `schema_version: 3` is the user setting it, and the CHANGELOG says so. A
   schema-2 file gets legacy autonomy (no flags added anywhere, argv byte-identical), and
   the session records the effective autonomy, or `custom` when `args` carry a permission
   flag.
6. **Per-repo lists are profile keys in v0:** `profiles.<p>.protected_branches` (default
   `["main", "master"]`) and `profiles.<p>.deploy_commands` (default none, command
   prefixes). Reading a repo's esc pack for these waits until the pack field is agreed with
   esc's maintainers.
7. **Notices before the inbox exists:** "guard unreachable" is one printed `bw:` line per
   session plus the `unreachable` count in the `guard` event.
8. **Local redaction for the log.** `guard.jsonl` stores the call after best-effort
   redaction (bearer tokens, `KEY=`/`TOKEN=`/`SECRET=`/`PASSWORD=` assignments,
   `--password`/`--token` values, `user:pass@` in URLs, `Authorization:` header values).
   The digest is of the unredacted call. Redaction before a model request is v1.
9. **Who touches guard state.** Only bw's own code writes `guard.jsonl` (the broker's
   decisions and `bw guard review`'s marks), never a hook; a guarded lead cannot write any
   of it. A pi lead on the `sandbox-exec` backend can read it (that backend allows reads by
   default, as for the containment record and the metrics log); nono denies it. Stated, not
   fixed: the content is the lead's own redacted calls.
10. **The socket and the clients sit where no lead can write.** A per-worktree directory
    under the state dir holds the socket (mode 0600) and the session's client copies, taken
    from bw's own code at session start. It is in no write grant (not the handoffs
    directory, which pi's sandbox grants and agy reaches through `--add-dir`), and bw
    refuses a state dir that overlaps the worktree for every runtime. The socket is bound
    and listening before the lead starts; the serving thread starts after, and the broker's
    `pause()` stops it around the self-test so no thread exists while `preexec_fn` runs. A
    stale socket from a hard-killed bw is unlinked once the worktree lock is held, and a
    path over 103 bytes refuses to start. Requests carry a random per-session token compared
    in constant time. A lead can still send requests; it can neither answer them nor change
    the code that answers.
11. **The lexer is ours, not `shlex`.** `shlex` treats a newline as whitespace and loses the
    difference between `;` and `';'`, so a second command on a new line becomes an argument
    of the first. `rules.py` has a small lexer that keeps operators apart from quoted text
    and marks any word with an unquoted `$`, backtick, or backslash as unexpanded.
12. **Repo scripts are trusted only as the repo has them.** A script path is allowed only if
    git tracks it and it is unchanged from `HEAD`. A script the lead wrote or edited this
    session is gray; otherwise "write a script, then run it" turns every block into two
    allows.

Smaller rulings. The start-time self-test uses a one-shot marker, disarmed before the lead
starts and replayed nowhere; its decision is recorded as a self-test and excluded from
review samples, summaries, and the `guard` event's counts, so evidence is real calls only.
Broker errors go to `guard.jsonl` as error records without the call text, never to stderr
(which lands in the lead's terminal). Redirections to `/dev/null`, `/dev/stdout`,
`/dev/stderr` and fd duplications (`2>&1`) are allowed because they write nothing. `git -C
<dir>` is gray only when the directory is inside a nested repo. `bw guard review` is the
only reader of the evidence and keys every summary by profile, runtime, runtime version, rules
version, and policy digest, so a change to any of them starts a fresh count.

Findings carried from the pi spike (1a) into the build: the event has no cwd (the handler's
`ctx.cwd` is used); a missing `-e` path fails closed before any model call; `-e` still loads
under `--no-extensions`; the first extension to block short-circuits later ones; resuming
(`-c` or `--session`) applies `-e` as it does to a fresh run.

What was and was not verified. The pi attack (`tests/attack/guard_attack.sh`, run by `bw
guard verify pi`) covers a sandbox-permitted control that must actually run, a rule block, one
live ask category, an injected "the user approved this", an allow, a missing broker, a garbage
reply, and a replaced socket, each fresh and resumed, and a case the lead never attempts fails
the run (a model that refuses proves nothing). It passed live on both backends, nono and
`sandbox-exec`. Only one ask category is exercised live; the unit table covers every rule.
The interactive `ask` path in pi (`ctx.ui.select`, only when the session has a UI) was not exercised live. agy's interactive prompt, and everything in the agy row of
"What exists today", is untested in v0. `guard.jsonl` has no retention in v0.

Autonomy is not an execution boundary. Test runners, `npm run`, `make`, and pristine repo
scripts run code the lead may have written; the sandbox is the boundary, and the guard
only watches the calls. A repo whose own `core.hooksPath` points inside the worktree (husky,
for example) lets a lead edit a hook the user's next unsandboxed commit runs: a sandbox-level
gap the guard did not create, documented rather than made a rule. When acting arrives, a
false block is resolved by hand, outside the lead. pi models reached through OpenRouter need
the `openrouter/` provider prefix in the model id (found in the attack runs).

Operational: pi and Kimi leads need `bw sandbox verify` again after the upgrade (the adapter
source changed); `bw guard verify pi` records a guard pass in its
own file; the Claude Code and Codex native flags were not launched interactively in the
build and need one confirming launch by the user. `bw lead` exits 7 when a guarded lead
cannot start.

Where to pick up: run the agy spike (1b) when the quota resets and flip `AGY_SPIKE_PASSED`
with fixtures; measure a week of shadow (gray share, latency, would-be questions per hour);
then decide on the v1 judge.

## Per-repo rules and OpenEscapement

Which branches are protected and which scripts deploy are facts about a repo, and
OpenEscapement is the tool that ships repo facts as a signed rule pack. So the guard reads a
repo's esc pack, when one is present, for those two lists (read-only; the guard never writes
inside a repo), and falls back to the profile's lists. The rule *mechanism* stays here; esc
supplies data. The exact pack field is settled with esc's maintainers when v0 is built
(author's call).

## Left to the builder

- Each runtime's hook wiring and output shape, verified on the installed versions.
- The exact allow grammar and the ask list's contents, starting from the lists above.
- The redaction patterns and the eval set.
- The broker's socket protocol (one JSON request and reply per call is enough).
