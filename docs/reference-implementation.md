# Reference implementation — the working example

*The concrete stack behind [architecture.md](architecture.md), with the software and
models named and the configuration that matters. Not a dump of the private setup —
a rebuild guide: each section names the tool, the settings that took debugging to
get right, and the traps. Written 2026-08-26 against the versions noted; updated
2026-09-02 when the stealth seat model graduated (see the pi section); updated
2026-09-11 when the Codex seat moved to a new model generation and gained planning duties
(see the Codex section) and to describe the metered seat's runtime move from OpenCode to pi
(2026-09-03); updated 2026-09-16 with the driver-mode section (which harness may run
which model at flat rate, first-party-harness containment, two new traps) and the pi
version bump to 0.85.x it required; the cast table gained the same-vendor blind seat
and the on-demand second seat the same day (both in the roster since 2026-09-02); updated
2026-09-21 with the seat-brief flag and the effort knob on the Claude seat (see Seat wrappers);
updated 2026-09-22 when the on-demand Opus seat moved to Opus 5.5; updated 2026-09-25 for
OpenAI's GPT-6 Sol and Luna (two new traps in the Codex section; the astra seat is unchanged);
updated 2026-09-26 with the quota fallback for the Claude seat and Claude subagents (see Seat
wrappers); updated 2026-11-11 when Opus 5.5 at effort `medium` became the default for the
primary agent and the blind seat, and Fable 5.2 (released 2026-11-09) became a reserved
seat for deep panels and stuck debugging; updated 2026-11-20 when the Gemini seat moved to
Gemini 4 Neon and became the stand-in for the OpenAI and Kimi seats while their plans are capped,
and a Gemini lead joined the lead order after the OpenAI lead (see Seat wrappers); updated
2026-11-23 with the flags and event shapes of the five seat CLIs, checked on the installed versions,
and the result that Seatbelt sandboxes do not nest (see Seat runtimes under `bw seat`); updated
2026-11-23 again: `bw seat` now does each seat wrapper step in this repo (step map in Seat
wrappers), and the seat login outcomes are recorded; updated 2026-11-23 a third time after the first
live seat attack on the pi seat: the git config read, the toolchain reads (Volta and pyenv among
them), the cargo token denies, and the `--approve` control; updated 2026-11-23 a fourth time after
the live attack on the codex and agy seats: the codex install read, the codex CA bundle, the zsh denial line, and the agy tool step shape;
updated 2026-11-23 a fifth time after the seat review fixes: the agy seat moves to its own gemini dir in the seat
home (`--gemini_dir`), seats lose the agent sockets and the keychain, and the parent of each code
name is rename-proof (see Seat runtimes under `bw seat`); updated 2026-11-23 a sixth time: the panel instructions
now live in this repo as `bw panel` (see Panel instructions); updated 2026-11-23 a seventh time after the
first live panel: the kimi seat runs `-p` with no `--auto` (Kimi Code 2.3.2 refuses the pair); updated 2026-11-23 with the known limits of the lead guard rules (see Lead guard rules); updated 2026-11-23 an eighth time: the
Codex `cli` quota reading is not applicable on `codex-cli 0.164.0` (see the note under the runtime table); updated
2026-11-24: one more known limit of the lead guard rules (`eval`, `trap`, and `alias` text); updated
2026-11-27 when the metered pi seat moved from GLM 5.3 Flash (seat "ox") to DeepSeek V4.1 Flash
(seat "flash"; see the pi section); updated 2026-11-27 again when the astra seat moved to
`gpt-6.2-astra` (seat, lead, and driver) and GPT-6.2 Sol became a budget driver, not a seat (see the Codex section and Driver mode).*

## The cast

| Role | Software | Model | Billing |
|---|---|---|---|
| Moderator / primary | Claude Code (CLI) with the [superpowers](https://github.com/obra/superpowers) plugin (6.x) as the workflow skeleton | `claude-opus-5-5` at effort `medium` (raised per task); Fable only on an escalation (since 2026-11-11) | Anthropic subscription |
| Planning + review seat ("astra") | Codex CLI ≥0.153 | `gpt-6.2-astra`, xhigh reasoning (since 2026-11-27; `gpt-6-astra` from 2026-09-11; `gpt-5.6-sol`, seat "sol", before) | OpenAI Pro subscription (CLI OAuth) |
| Metered seat ("flash") | pi 0.85.x (0.74.x until 2026-09-16; was OpenCode ≥1.18 until 2026-09-03) | `deepseek/deepseek-v4.1-flash` via OpenRouter, thinking `medium` (since 2026-11-27; seat "ox" on `z-ai/glm-5.3-flash` before, `stealth/ox-alpha` until 2026-09-02) | OpenRouter API key (prepaid credits) |
| Same-vendor blind seat ("opus") | Claude Code headless (`claude -p`), fresh session, write tools disallowed plus a pre-tool guard hook | `claude-opus-5-5`, effort `medium` (since 2026-11-11; the on-demand seat from 2026-09-22, `claude-opus-4-8` before) | Claude subscription (flat); a fresh scoreboard |
| Reserved same-vendor seat ("fable") | same mechanism | `claude-fable-5-2` (`claude-fable-5-1` from 2026-09-02 until 2026-11-11, when it was the default blind seat) | Claude subscription (flat), twice Opus 5.5 per token; deep panels and stuck debugging only, each run with its reason |
| Blind one-shot fallback | plain HTTPS (`chat/completions`) | same OpenRouter model | OpenRouter API key |

Why these: each vendor's *own* CLI gives the seat repo exploration, native session
storage, and subscription billing — no proxy, no prompt translation. Roles were
assigned from logged dispute outcomes (see architecture.md); the seat model is a
config detail, not an interface commitment — which paid off within a week (below).

## Seat: Codex CLI (`gpt-6.2-astra`)

Profile file — **`~/.codex/review.config.toml`** (Codex ≥0.149 uses per-profile
*files*; a `[profiles.review]` table inside `config.toml` is rejected as legacy):

```toml
model = "gpt-6.2-astra"
model_reasoning_effort = "xhigh"   # this model also offers max and ultra; ultra delegates tasks — unverified read-only
approval_policy = "never"       # headless: no interactive prompts
sandbox_mode = "read-only"      # the load-bearing line
```

Invocation and traps:

- New session: `codex exec -p review -C <target-dir> --skip-git-repo-check "<prompt>"`.
  Session id appears on stderr as `session id: <uuid>` — capture it for resumes.
- Resume: `codex exec resume <uuid> "<prompt>"` — accepts **no `-p`/`-C`**; replicate
  the profile via `-c key=value` overrides and `cd` to the target first. Have the wrapper
  *parse* `model`/`model_reasoning_effort` out of the profile file for those overrides
  rather than repeating the values — otherwise a model swap has two places to miss.
- A move to a different model line is a seat swap: the reference setup retired seat
  "sol" and started "astra" with an empty scoreboard, because seat policy (roles,
  rebuttal caps) was earned by the old model's dispute record and the per-seat metrics
  would otherwise blend two models. A newer version of the same model line keeps the
  seat id and its history, with a dated note: `opus` kept its id from Opus 4.8 to 5.5,
  and `astra` from `gpt-6-astra` to `gpt-6.2-astra` (2026-11-27). A same-model re-pin
  (the stealth-id graduation below) also keeps the seat id and its history.
- Don't name a seat after a vendor's tier name; vendors reuse them across generations.
  OpenAI shipped GPT-6 Sol (`gpt-6-sol`) on 2026-09-22, so the retired seat "sol" (which
  held `gpt-5.6-sol`) now reads like the new model. A GPT-6 Sol seat needs its own id,
  and any rule that maps a retired id onto its successor must not catch the new one,
  or two models' findings merge silently.
- Under a ChatGPT sign-in, new models reach accounts in a rollout. Until one reaches
  yours, Codex answers with a 400, "not supported when using Codex with a ChatGPT
  account", while the vendor docs already list the model. `~/.codex/models_cache.json`
  lists what the account can actually use; check it before pinning a new model in a
  profile. As of 2026-09-25 a Pro account on Codex 0.154 did not yet have `gpt-6-sol`
  or `gpt-6-luna`, and pi 0.85.1's `openai-codex` provider did not list them either.
- On 2026-11-27 the account's model cache listed the 6.2 generation. OpenAI calls
  `gpt-6.2-astra` its most capable model and `gpt-6-astra` the previous generation. It
  calls `gpt-6.2-sol` its fast everyday model and `gpt-6-sol` legacy. Both 6.2 models
  answered a probe under a ChatGPT Pro sign-in, and the astra seat ran on `gpt-6.2-astra`.
  The seat, the `astra` lead, and the astra driver moved to it that day. The seat kept
  the id `astra`, because `gpt-6.2-astra` is a newer version of the same model line.
- Set `TMPDIR` to a private scratch dir: the Rust binary extracts multi-MB temp
  `.dylib`s into `$TMPDIR` — *not* cwd, as first assumed. Some agent sandboxes point
  `TMPDIR` at the project checkout, which litters it with `.<hash>-00000000.dylib`
  files; found 2026-09-02 after the wrappers had been "running from a scratch dir"
  for a week. Derive the scratch from the real system temp dir
  (`getconf DARWIN_USER_TEMP_DIR` on macOS), export it as `TMPDIR`, remove on exit.
- Auth survives ChatGPT-app removal (CLI holds its own OAuth in `~/.codex/auth.json`),
  but the desktop app manages `config.toml` — expect churn there; keep your profile in
  its own file.

## Seat: pi (`deepseek/deepseek-v4.1-flash` via OpenRouter)

Runtime: [pi](https://www.npmjs.com/package/@earendil-works/pi-coding-agent)
(`@earendil-works/pi-coding-agent`, 0.85.x since 2026-09-16 — 0.74.x before). Re-verify the
read-only layers by attack after every upgrade: the 0.74→0.85 jump crossed eleven minor
versions and was re-checked the same day. This seat ran on OpenCode until 2026-09-03; the
move is described at the end of the section.

**Install it with a manager that pins the tool to its own Node, not to whichever Node is
active.** pi's `engines` floor moves with the release — 0.74 wanted Node ≥20.6, 0.85 wants
≥22.19 — so a plain `npm install -g` binds the copy to whatever Node was on PATH at the
time. The reference setup uses [Volta](https://volta.sh):

```sh
volta install @earendil-works/pi-coding-agent   # resolves latest, fetches a Node that satisfies it
pi --version                                    # confirm; re-run the same command to upgrade
```

Volta fetches and pins a satisfying Node per tool, so a repo that pins an older Node for its
own build does not change which Node pi runs on. Without that, the failure is quiet: on
2026-09-16 this setup had **two** pi installs — 0.85.1 on the pinned Node, and an orphaned
0.74.2 sitting in an older Node image's globals from an earlier `npm install -g`. Both were
on disk, and which one ran depended on PATH order, so the seat and the driver could run
different pi versions on the same machine. `pi --version` in one shell disagreed with
another.

Two rules that follow. Never put a Node image's own `bin` directory on PATH to find a tool
(`$VOLTA_HOME/tools/image/node/<version>/bin`) — those are one Node version's globals, and
searching them defeats the pinning; probe the manager's shim directory only. And after any
pi upgrade, check the installed model catalog rather than the docs: 0.74 stopped at the
previous model generation.

**History.** The seat began as OpenRouter's stealth `ox-alpha` preview (served as GLM-5.3
Flash), ran GLM 5.3 Flash until 2026-09-26, and was replaced by DeepSeek V4.1 Flash
(seat `flash`) on 2026-11-27. The scoreboard showed DeepSeek doing the same job (a cheap,
metered OpenRouter reader on the same pi engine) with fewer timeouts (1% of runs against 13%)
and more unique catches (40% against 34%), so the GLM seat was redundant.

**The stealth-model lesson, lived (2026-09-02):** when OpenRouter ended the free preview one
week after setup, every call returned a 404 that also revealed the model's identity. Because
the id was pinned in exactly one config line per component, re-pinning took minutes; the
2026-11-27 model change was the same one-line edit. Two operational notes: the model is
paid, so the OpenRouter account must hold credits (a $0 balance fails identically to a
missing model), and a per-key spend cap is the cost control. The seat is metered at
OpenRouter's list price; a repo-aware review costs a few cents.

**Read-only, in three layers** — the reason this runtime won the seat:

1. **Tool allowlist by name.** `--tools read,grep,find,ls,bash` — no write or edit
   tool is loaded at all, so bash is the only write-capable surface left.
2. **In-process bash guard.** A small extension (`-e guard.ts`, loaded with
   `--no-extensions` so nothing else is) registers a `tool_call` handler that
   permits only a single read-only command with plain arguments (`ls`, `cat`,
   `grep`, `find`, `git status|log|diff|show|blame|…`) and denies anything with
   redirects, pipes, chaining, subshells, heredocs, interpreters (`python`, `node`,
   `sh -c`), package managers, `sed -i`, `find -delete`, or mutating git
   subcommands. It also blocks any write/edit tool call outright (in case the tool
   list is ever widened) and logs each denial as one JSON line to a guard log, so
   "no file appeared" can be cross-checked against "the guard actually fired".
   Because it runs inside the agent process, it is not a fail-open hook.
3. **No project-supplied code.** The seat runs with its own agent directory
   (`PI_CODING_AGENT_DIR`, holding `settings.json`, `models.json`, auth) and
   `"defaultProjectTrust": "never"`, and `--no-extensions --no-skills
   --no-prompt-templates --no-themes` keep a reviewed repository's `.pi/` tree from
   loading anything into the reviewer. Verified with a hostile extension planted in
   the target: it did not load.

Config — `models.json` in the seat's agent directory registers the OpenRouter model
explicitly so reasoning and limits are pinned rather than inherited:

```json
{ "providers": { "openrouter": { "models": [
  { "id": "deepseek/deepseek-v4.1-flash", "name": "DeepSeek V4.1 Flash (flash seat)",
    "reasoning": true, "input": ["text"],
    "contextWindow": 1048576, "maxTokens": 131072 }
] } } }
```

The model is absent from pi 0.85.1's built-in catalog, so this block is required. Add a
`cost` block with OpenRouter's current list price if you want pi to report spend per run.

Invocation (from the target directory, `OPENROUTER_API_KEY` **exported**):

```
pi -p --model openrouter/deepseek/deepseek-v4.1-flash --thinking medium \
   --session-dir <seat-sessions> --session <seat-sessions>/<uuid>.jsonl \
   --no-extensions -e <guard.ts> --no-skills --no-prompt-templates --no-themes \
   --tools read,grep,find,ls,bash \
   --append-system-prompt "<reviewer system prompt: read-only, evidence-first, severity-ranked>" \
   "<prompt>"
```

Sessions: the **wrapper chooses the session file** (`--session <path>` creates it if
missing, resumes it if present), so there is no session-id recovery step — a fresh run
mints a new uuid, `-r` reuses the one saved for the directory. Set `PI_OFFLINE=1` so a
headless run never phones home for updates.

Installed-vs-docs gap (0.74.2): the project's main-branch docs describe
`--session-id`, `--approve/--no-approve`, and a `defaultProjectTrust` CLI flag; the
installed release has none of them (`Unknown options`). Re-check on upgrade.

**Why not OpenCode (the previous runtime, 2026-08-25 → 2026-09-03):** its bash
permission was a glob over the whole command line, so `git log*` admitted
`git log | tee x`; project-local `.opencode/tools` loaded regardless of config and
could replace `bash`; and the session id had to be scraped from its SQLite store by
directory, which is ambiguous when seats run in parallel. Each of those is a
read-only or continuity hole the pi setup closes by construction.

## Seat wrappers (the whole integration surface)

One ~60-line shell script per seat, uniform interface `seat.sh [-b] [-r] [-d dir] "prompt"`:
self-source credentials → absolutize `-d` → make a private scratch dir under the
system temp root and export it as `TMPDIR` → run the CLI from it →
persist session id per target directory (tsv) → append a `usage` event to the metrics
JSONL → exit with the CLI's status. The moderator (Claude Code) drives seats through
slash-command instructions that carry the panel protocol and the mandatory
outcome-logging step.

**`bw seat` does these steps now.** The `seats/` package in this repo does each wrapper step,
for every seat, in Python. You can use it instead of a shell script. The panel calls it; you
rarely run it by hand. The step map:

| Wrapper step | Module |
|---|---|
| Headless argv, event parsing, answer and usage shapes | `seats/adapters.py` |
| The pre-built seat brief | `seats/brief.py` |
| Session id per target directory, one run at a time | `seats/sessions.py` |
| Wall-clock cap, cap flags, stand-in seat | `seats/caps.py`, `seats/run.py` |
| Private scratch dir, OS sandbox, env allowlist | `seats/run.py` with `seats/grants.py` and `lead/sandbox.py` |
| The `usage` row in the metrics log | `seats/run.py` |
| Attack-based verification, a pass per seat id | `seats/verify.py`, `seats/judge.py`, `tests/attack/seat_readonly.sh` |
| `seat.sh`-style command line | `seats/cli.py` (`bw seat`) |

Login outcomes, checked 2026-11-23:
- **Claude seat.** The keychain login does not carry over to the seat's private home. Run
  `claude setup-token` once and export `CLAUDE_CODE_OAUTH_TOKEN` in the shell that runs `bw`.
  Without it, the Claude seat is absent with a "no login" reason.
- **Codex seat.** It uses the real `~/.codex/auth.json`, granted read and write as that one file only.
- **agy seat.** It keeps the real home, because its keychain login does not carry over to a private
  home. The hidden flag `--gemini_dir` moves its config and state into `<seat home>/gemini`, and the
  keychain login still loads. It writes nothing under the real `~/.gemini`, and it reads no agy lead
  session store. Its code names and memory dirs in its own dir stay read-only.
- **pi seat.** It needs `OPENROUTER_API_KEY` in the environment (OpenRouter only for now).

`bw start` checks seats in the background after the lead opens (`bw: checking seats in the
background (log: <path>)`). `bw seat list` shows the state of each seat.

Hostile repos: a seat refuses a repo whose project files load code, unless the seat's own "ignore
project config" flags cover them. The attack's positive control proves each flag.

**The seat brief (`-b`, 2026-09-21).** One shared script builds it; each wrapper calls it only
when starting a *new* session and replaces the prompt with brief + reading rule + task (a
resumed session already carries it). What goes in, and the limits that keep it useful:
- the head of the project's instruction file (about 80 lines) and pointers to the
  architecture doc and README — pointers, not contents;
- `git diff --stat` and `git diff` against `merge-base(base, HEAD)`, which includes
  uncommitted edits; capped (a few thousand lines) with a note to open the rest from the stat;
- untracked files, listed, since the diff can't show them;
- "direct callers": `git grep -l -w` on each touched file's stem, skipping generic stems
  (`index`, `utils`, `types`, …) and anything under four characters, a handful per file.
  A starting point, not a call graph — say so in the brief;
- base: an explicit override, else the first of the integration branch's remote, the default
  branch's remote, the local default branch.
Cache it keyed on directory, both commit ids, and a hash of the working-tree diff plus the
untracked list, so an edit rebuilds it and a re-run doesn't. Two traps: on a case-insensitive
filesystem, probing `ARCHITECTURE.md` *and* `docs/architecture.md`-style variants will list the
same file twice — probe one spelling per location; and resolve the base *before* computing
the cache key, or a moved base branch serves a stale brief. Verify it the cheap way: ask each
seat, fresh with `-b`, a question only the brief answers ("what command runs the tests?"),
and check all of them answer it.

**Effort.** The Claude seat's CLI takes an effort level per run; the wrapper defaults it to
`high` and the deep tier raises it to `max`, the same way it raises the other seats'
reasoning. Before this the Claude seat had no knob and ran at the CLI's default.

**Quota fallback for the Claude seat (2026-09-26).** A flat-rate plan still has a usage cap
per model, and when the top Claude tier hits it, every place that pins that model fails at
once: the blind seat, any subagent definition pinned to it, any ad-hoc subagent that names it.
The fix is one piece of state and three readers, not a per-call judgment:
- **One flag with an expiry** (a small script: `on [hours] | off | status | check`, storing an
  epoch deadline in the state directory). Expiry matters: a flag that never clears quietly
  keeps the fallback in place after the cap resets. Default to a few hours; set the real
  reset window when you know it.
- **The seat wrapper reads it.** While the flag is fresh, a run of the top-tier seat goes to
  the fallback model instead (the next tier down, not a model you have ruled out), and the
  wrapper prints a one-line notice. The seat name follows the model, so the run logs and
  scores under the fallback's id, and the panel record lists the original seat as `absent`
  with reason "usage cap". Folding it into the top-tier seat's scoreboard would credit one
  model with another's findings.
- **The wrapper arms it.** When a top-tier run fails and its output matches a usage-limit
  pattern, the wrapper sets the flag and re-runs itself once. A re-entry guard
  (an environment variable) keeps a mismatch from looping.
- **A pre-tool hook on the subagent tool reads it too.** If the call names the capped
  model, or leaves the model unset while the subagent definition pins it, the hook rewrites
  the call's model to the fallback (Claude Code: `hookSpecificOutput.updatedInput`). A
  post-failure hook on the same tool arms the flag when a pinned call fails on the limit. Skip
  forked subagents: they ignore the model field anyway.
Trap: the subagent tool only accepts family aliases (`opus`, `sonnet`, …), and an alias
resolves to whatever the client currently maps it to, which may be a model you have ruled out.
Pin each alias in the harness settings (Claude Code: `ANTHROPIC_DEFAULT_OPUS_MODEL`, and
`ANTHROPIC_DEFAULT_SONNET_MODEL` for the cheaper tier) so the
fallback is the exact version you chose. Verify end to end: arm the flag, dispatch a subagent
pinned to the capped model, and check the model id in its transcript, not the hook's own
output.

**Cross-vendor stand-in for capped seats (2026-11-20).** The same flag pattern works across
vendors, one flag per seat. When the OpenAI or the Kimi plan is used up, the Gemini seat runs
in its place: the OpenAI and Kimi wrappers check their own flag first and hand the same
arguments to the Gemini wrapper, and a run that fails on a usage limit arms the flag and re-runs
once on Gemini. In config this is `seats.<seat>.fallback`. The Gemini seat runs Gemini 4 Neon
(`gemini-4-neon-high` in agy), the newest Gemini 4 model as of 2026-11-18; it is not on a
checkpoint roster by itself. The stand-in run scores under the Gemini seat's id and the capped
seat is `absent` with reason "usage cap"; if two capped seats both stood in, keep one run.
Traps: match the usage-limit pattern on **error lines only**, because Codex echoes the prompt on
stderr and a review of quota code would trip a plain match; and let the attack harness bypass
the stand-in, or an attack on a capped seat silently tests the Gemini seat instead.
The lead order follows the same plans: Claude Code while the Anthropic plan has quota, then
Codex on the OpenAI plan, then agy on Gemini 4 Neon when both are capped (`lead_order` in each
profile; rotation picks the first lead whose plan is not capped and returns to an earlier one at
a boundary once its quota recovers). The Gemini lead runs with `autonomy: manual`, because an agy
lead cannot use the guard until agy's hooks are verified.

**Temp-dir hygiene (seats and drivers alike).** Every vendor CLI extracts scratch files into
`$TMPDIR` — Codex unpacks multi-megabyte native libraries there, Node writes its compile
cache under it, Python and pi follow suit — and some agent sandboxes force `TMPDIR` to the
*project checkout* for anything run through them. The symptom is hash-named directories,
`.tmpXXXXXX` files, and a `node-compile-cache/` tree appearing in the working directory,
none of them ignored. Two layers fix it: every wrapper, seat or driver, resolves the real
system temp directory with `TMPDIR` unset, creates a private scratch directory there, and
exports it as `TMPDIR` (and as Node's compile-cache path) before launching the CLI; and a
global git excludes file carries the patterns for whatever still lands, so a stray cache
never reaches a commit in any repo. Neither layer alone is enough — the wrapper cannot
cover a CLI the user runs by hand, and the ignore file cannot stop the litter, only hide it.

## Seat runtimes under `bw seat` (verified 2026-11-23)

This section records the facts that the `bw seat` adapters use. Each fact was checked on the
installed CLI on 2026-11-23. A mark follows each value: **h** = the CLI's `--help`, **d** = the
vendor's docs or text built into the CLI, **o** = observed in a run, **u** = not checked here
(the value comes from an earlier version of the setup, or a run was not possible). The table has one column per
runtime, so that it fits on a page. `none` means that the CLI has no such flag or file.

Only shapes are recorded: event types and field names. No values from a run are recorded.

One headless run per CLI gave the event shapes (the prompt "reply with the word OK"). Codex and
Claude Code also did one resume run each. The Kimi plan was at its monthly cap, so the Kimi run
returned only the usage-limit error. The kimi column is therefore "help" plus that error path.

| Field | codex | claude-code | pi | kimi | agy |
|---|---|---|---|---|---|
| Version line (`--version`) | `codex-cli 0.164.0` o | `2.1.330 (Claude Code)` o | `0.85.1` o | `2.3.2` o | `1.5.13` o |
| Headless flag | `exec` subcommand h | `-p` h | `-p` h | `-p <prompt>` h | `-p` h |
| JSON output flag | `--json` h,o | `--output-format stream-json --verbose` h,o | `--mode json` h,o | `--output-format stream-json` h | `--output-format stream-json` h,o |
| Model flag | `-m` h | `--model` h | `--model` h | `-m` h | `--model` h |
| Effort flag | `-c model_reasoning_effort=<e>` o | `--effort <e>` h,o | `--thinking <e>` h | none h | `--effort <e>` h |
| Prompt from stdin | yes, argv `-` o | yes, no token (stdin when no prompt argument) o | yes, no token o | no: `-p` needs an argument o | u: only `--input-format stream-json` reads stdin h |
| Resume argv | `exec resume [flags] <id> -` o | `-p --resume <id>` o | `-p --session <id>` h | `-S <id> -p <prompt>` h | `-p --conversation <id>` h |
| Session id (event, field) | `thread.started`, `thread_id` o | every event, `session_id` (`system`/`init`, `result`) o | first line `session`, `id` o | `session.resume_hint`, `session_id` u | `init` and `result`, `conversation_id` o |
| Config-dir env var | `CODEX_HOME` h | `CLAUDE_CONFIG_DIR` o | `PI_CODING_AGENT_DIR`; sessions `PI_CODING_AGENT_SESSION_DIR` h | `KIMI_CODE_HOME` u | none h |
| Where the login lives | file `auth.json` in the config dir h,o | macOS keychain, one item per config dir o; a `.credentials.json` file in the config dir also works o | provider API key in env o; `auth.json` for OAuth providers h | file under `credentials/` in the config dir o | macOS keychain u |
| "Ignore project config" flag | `--ignore-rules` (exec-policy rules only; partial) h | `--setting-sources user` and `--strict-mcp-config` h | `--no-approve` h,d | none h | none h |
| "Hooks off" flag | none h (partial) | `--safe-mode` h | `--no-extensions` h | none h | none h |
| Own sandbox: on | `-s read-only` (resume: `-c sandbox_mode="read-only"`) h,o | none | none | none | `--sandbox` h |
| Own sandbox: off | `--dangerously-bypass-approvals-and-sandbox` (new and resume) h | none | none | none | omit `--sandbox` h |
| Project files it loads as code | `.codex/config.toml`, `.codex/hooks.json`, `.codex/skills`, `.rules` d | `.claude/settings.json`, `.claude/settings.local.json`, `.mcp.json`, `.claude/agents`, `.claude/commands`, `.claude/skills` d | `.pi/settings.json`, `.pi/extensions`, `.pi/skills`, `.pi/prompts`, `.pi/themes`, `.pi/SYSTEM.md`, `.pi/npm`, `.pi/git` d | `.kimi-code/mcp.json` u, `.kimi-code/skills` d | `.agents/hooks.json`, `.agents/plugins.json`, `.agents/plugins/`, `.agents/skills/`, `.agents/workflows/`, `.agents/agents/`, `.agents/rules/` d |
| Session store under the real home | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` o | `~/.claude/projects/<cwd>/<id>.jsonl` o | `~/.pi/agent/sessions/--<cwd>--/` o | `~/.kimi-code/sessions/` u | `~/.gemini/antigravity-cli/conversations/<id>.db` and `brain/<id>/` o |
| Error-line shape | events `error` and `turn.failed` u; stderr was empty o | `result` with `is_error`, `subtype`, `api_error_status` o (fields) | assistant `message_end` with `stopReason` `error` u; stderr `Warning:` lines are not errors o | stderr `error: failed to run prompt: provider.api_error: 429 …`, exit 1 o | `result.status` other than `SUCCESS` o (field) |
| Prompt echo, and where | none with `--json` and a stdin prompt o | none o | stdout: `message_start`, `message_end`, `agent_end` with `role` `user` o | u | none (the `user_input` step has no text) o |
| Headless tool-permission flag | `--dangerously-bypass-approvals-and-sandbox` h | `--permission-mode bypassPermissions` h,o | none (no approval prompts) h | none: `-p` runs headless, and 2.3.2 refuses `-p` with `--auto` o | `--dangerously-skip-permissions` h,o |
| Env keys it needs | none | none (or `CLAUDE_CODE_OAUTH_TOKEN`, see below) o | the provider key, for OpenRouter `OPENROUTER_API_KEY` h,o | none | none |
| Credential file it refreshes in place | `auth.json` (it holds a refresh token) o | `.credentials.json` when the login is a file u; none with a token o | none for an API key o | the JSON file under `credentials/` (it holds a refresh token) o | none (keychain) u |
| Answer (final text) | last `item.completed` with `item.type` `agent_message`, field `item.text` o | `result.result` o | last assistant `message_end`, `content[].text` o | u | `result.response` o |
| Token counts | `turn.completed.usage` (`input_tokens`, `output_tokens`, more) o | `result.usage`, `result.total_cost_usd` o | assistant `message_end` `usage` (`input`, `output`, `cost.total`) o | u | `result.usage` (`input_tokens`, `output_tokens`, more) o |
| Rate limits in the stream | none o | `rate_limit_event`, `rate_limit_info` o | none o | u | none o |

**Codex `cli` quota: not applicable on `codex-cli 0.164.0`.** A second check on 2026-11-23 (one
`codex exec --json` run, read-only sandbox) gave the events `thread.started`, `turn.started`,
`item.completed`, and `turn.completed`. No event and no stderr line has a `rate_limits` field. So a
Codex seat run writes no `cli` snapshot, and Codex plans use observed caps (`quota: "none"`). The
passive `cli` source stays in `lead/quota.py` for a later Codex version. Re-check after each Codex
upgrade.

**Nesting.**

sandbox-exec nests: no

nono nests sandbox-exec: no

A quick check, two `(allow default)` profiles one inside the other, exits 0. That result is
misleading. When the outer profile or the inner profile has one rule, the inner `sandbox_apply`
fails with "Operation not permitted" and exit 71. A rule can be a deny on a path, a port, a Mach
service, or `process-exec`. A real seat profile always has rules, so nesting does not work. `nono run`
over `sandbox-exec` fails in the same way. Inside `bw sandbox`, turn off every vendor sandbox that
uses Seatbelt: Codex runs with `--dangerously-bypass-approvals-and-sandbox`, and agy runs without
`--sandbox`. The `inner_sandbox` field holds those "off" flags. `bw sandbox` is then the only
enforcer, as the design requires.

**Home reads under nono.** nono denies reads under the home by default; sandbox-exec does not. A
tool that needs a file there stops before it does any work. git exits when `~/.gitconfig` exists
but is unreadable, so every seat reads the user's global git config (read only, as a lead does).
An interpreter from a version manager under the home (pyenv, for one) cannot load its libraries.
So every seat reads one fixed list of toolchain dirs, read only, when they exist: `~/.pyenv`,
`~/.volta`, `~/.nvm`, `~/.rbenv`, `~/.cargo`, `~/.rustup`, `~/.local/bin`, `~/.local/lib`
(`SEAT_TOOLCHAIN_READS` in `seats/grants.py`). The secret denies still win inside them: the crates.io
token files `~/.cargo/credentials` and `~/.cargo/credentials.toml` stay unreadable on both backends.
A seat CLI that links into PATH from a package dir under the home also needs that dir. The codex
standalone install puts a link in `~/.local/bin`, and the binary and its resources live under
`~/.codex/packages`. Without a read of that dir, `env` says "codex: No such file or directory" and
codex never starts. So the codex adapter names `codex` in `installs`. The seat then reads the real
release dir (the parent of its `bin`) and each link node on the way, read only, as tool reads. A
root that holds the home, lies in a secret path, or overlaps a session store or a write is dropped.
codex 0.164 under nono also cannot load the macOS system root store. Every HTTPS connect then
fails, and the turn ends with "workspace routing discovery failed". With `SSL_CERT_FILE` set, codex
loads that PEM bundle and runs. So the codex seat sets `SSL_CERT_FILE=/etc/ssl/cert.pem`, unless the
user set `SSL_CERT_FILE` or `CODEX_CA_CERTIFICATE`; the seat keeps those two names in its env.
codex and agy run commands in the user's shell. zsh prints a denial in its own shape:
`zsh:1: operation not permitted: g1`. The seat attack judge counts a line in exactly that shape as a
denial, with the same rule that the line names the probe's target.
When a probe's tool stops before its write, the seat attack reports the probe as skipped, never as
passed, and `bw seat verify` says that the tool could not start under the sandbox.

**Seat attack evidence (seat review fixes, 2026-11-23).** After each phase, bw re-runs every probe
as a plain process under the seat's own sandbox, whatever the model did. A probe counts as denied
only when that re-run prints its tag and then an OS denial that names its target. The model's tool
results are kept as extra evidence only: a model can print a tag and a denial line without trying the
write. So a skipped probe is a failure, never an inconclusive refusal; only a missed control and an
unused resume are. The probes also write each code file name and rename each parent of a code name
in the attack's own seat home, write bw's state dir and a sibling seat home, list a lead session
store and bw's production state dir (both read-denied), read a registry token inside a toolchain dir
when one exists, and connect to the SSH agent socket when `SSH_AUTH_SOCK` is set. The hostile-repo
case must run, and a clean run proves something only for a seat with ignore flags and an executable
covered kind whose control loaded.

**What else a seat profile denies (seat review fixes, 2026-11-23).** These rules live in
`seats/grants.py`, so a change to them resets seat keys only, never a lead key.
- No connect to an agent socket: launchd's per-user socket dirs (`/private/tmp/com.apple.launchd.*`),
  the `SSH_AUTH_SOCK` path, and any socket under a secret path (`~/.ssh`, `~/.gnupg`). Checked with a
  local socket: the connect fails with "Operation not permitted".
- No keychain: no lookup of the keychain services and no read of `~/Library/Keychains`. The agy seat
  is the exception (`keychain` in its adapter), because its login is in the keychain.
- No rename of a code name's parent: each dir node from the seat home down to a code path has a
  literal write deny. Seatbelt checks the renamed path itself, so a deny on the code names alone does
  not stop `mv <home>/.claude elsewhere`.
- More secrets: `~/.git-credentials`, `~/.config/git/credentials`, `~/.npmrc`, `~/.pypirc`,
  `~/.kimi-code/oauth`, and agy's `antigravity-oauth-token`. The global git config is granted as files
  only (`~/.gitconfig`, `~/.config/git/config`, `ignore`, `attributes`), never as the dir. These are
  seat-only (`SEAT_SECRET_READS`, `seat_git_config_reads`): a lead keeps its old list, because it may
  push through git's credential store or install from a private registry, and it reads the whole
  `~/.config/git` dir, where an `[include]` may name any file.
- More lead stores: the prompt histories (`history.jsonl`, `user-history`), the session indexes, the
  file snapshots, `~/.gemini/history`, and agy's `brain/`. A secret or store under a `CODEX_HOME` or
  `KIMI_CODE_HOME` that the user moved is denied too.
- A target inside a secret path is refused, and a directory outside its own git worktree (a planted
  `core.worktree`) is refused.

Known limit: the codex seat on the `sandbox-exec` backend (a machine without nono) failed at start
before the metadata fix ("failed to canonicalize CODEX_HOME"), because realpath could not stat the
read-denied state dir. The metadata reads above should end that; no codex run on `sandbox-exec` has
confirmed it.

**codex.** `codex exec resume` has no `-s`, `-C`, or `-p` flag. A resume therefore cannot set the
sandbox with `-s`, and it cannot set the target with `-C`: run it from the target directory. The flag
`--dangerously-bypass-approvals-and-sandbox` exists on both paths, so one flag sets the same state
on a new run and on a resume. No `-c` key can be renamed under it.
With `--json` and the prompt on stdin, stderr stayed empty. An earlier version of the setup saw the prompt echo
on stderr without `--json`, so match usage-limit words only on error events. A resume returns the
same `thread_id`. The exec stream has no rate-limit event, so Codex rate limits do not come from a
seat run. `--ignore-user-config` skips `config.toml` in `CODEX_HOME` but keeps the login there.
Codex runs project hooks only after the user trusts them (`--dangerously-bypass-hook-trust`
exists to skip that step), so a seat home without trust records runs no project hooks. This last
point comes from the help text only.

**claude-code.** A run with `-p` fired the user's `SessionStart` hooks (the stream showed
`hook_started` and `hook_response` events). A seat must not use the user's config dir. The seat
uses its own config dir: `CLAUDE_CONFIG_DIR` set to the seat home. With that dir, `claude auth
status` reports "not logged in", because Claude Code keeps one keychain item for each config dir.
So the keychain login does not carry over. Two ways work, and both were checked with a fake token
and `auth status`:
- a `.credentials.json` file in the seat config dir (Claude Code reads it when its keychain item
  is missing). A copy of the user's login would refresh, and the refresh can invalidate the user's
  own login. Do not copy the user's login.
- `CLAUDE_CODE_OAUTH_TOKEN` in the env, from a one-time `claude setup-token`. The token is
  long-lived and is never refreshed, so no credential file is written. The adapter declares
  `CLAUDE_CODE_OAUTH_TOKEN` in `env_keys`, and the backend can be `any`.

The recommendation is the token. `--bare` also skips
hooks, but it reads only `ANTHROPIC_API_KEY`, so a plan login does not work with it. `--safe-mode`
turns off hooks, CLAUDE.md, MCP servers, plugins, skills, and agents, and keeps auth and the
built-in tools. Unset `CLAUDECODE` before the run. A resume returns the
same `session_id`. The `-p` mode skips the workspace trust dialog.

**pi.** pi echoes the prompt on stdout as a `user` message, so the error matcher must skip
`user` messages. In `-p` and `--mode json`, pi does not ask about project trust. Without a saved
decision it uses `defaultProjectTrust` from the global settings, and the default `ask` ignores
project files. `--no-approve` makes that explicit. For OpenRouter the key comes from the env; the
`auth.json` file stays empty. An unknown model id gives a `Warning:` line on stderr and the run
continues. pi has no approval prompts, so it needs no permission flag.

**kimi.** `-p` takes the prompt as its argument. With no argument it fails with "argument missing".
`-p -` went on to the API, so it is not known if `-` means stdin. Pass a short fixed instruction
that names the brief file. The usage-limit error is one stderr line that starts with `error: failed
to run prompt: provider.api_error: 429`, then a `See log:` line, and exit 1. stdout holds only a
`system.version` event. Run one OK prompt when the plan resets, and record the session, answer, and
usage shapes before the kimi fixtures are final. Do not add `--auto` (or `--yolo`, or `--plan`) to a
`-p` run: Kimi Code 2.3.2 exits with "Cannot combine --prompt with --auto" before any model turn.
Kimi Code ignores its own deny rules in `-p` mode.

**agy.** The event key is `event`, not `type`. `--effort` exists (`low` to `max`), so the agy
adapter can take an effort flag. agy has no documented config-dir variable. A private `HOME` sends
agy to a browser login. Project hooks in `.agents/hooks.json` load after the user trusts the folder.
Directory rules (`GEMINI.md`, `AGENTS.md`) load as prompt text, not as code.

**agy seat-owned dir (spike, 2026-11-23, agy 1.5.15).** The binary takes hidden flags that
`agy --help` does not list. `--gemini_dir=<absolute dir>` moves the whole `~/.gemini` tree:
`config/`, and `antigravity-cli/` with its conversations, logs, and token. The login still loads from
the keychain through the real `HOME` ("keyringAuth: loaded token"). A "reply OK" run under the seat
sandbox returned `SUCCESS`. `--app_data_dir` takes only a path relative to the gemini dir, and the
env name `ANTIGRAVITY_GEMINI_DIR` had no effect. So the agy seat passes `--gemini_dir=<seat
home>/gemini` (the adapter's `home_argv`). The keychain save fails in the sandbox, so agy keeps the
refreshed token in `<seat home>/gemini/antigravity-cli/antigravity-oauth-token`; it then tries to clear
the keychain item, and the sandbox stops that too. The code names that stay read-only in that dir:
`settings.json`, `GEMINI.md`, `skills`, `policies`, `config/mcp_config.json`, and, in
`antigravity-cli/`, `settings.json`, `GEMINI.md`, `extensions`, `updater`, and the memory dirs
`knowledge`, `implicit`, and `annotations`. agy logs a failed write of `mcp_config.json` and of
`implicit/` and goes on. `bin`, `builtin`, and `brain` are writable: agy rewrites `bin/agentapi`
before each `run_command`, so a read-only `bin` stops every shell command. bw removes that `bin`
before every agy run (`adapters.SCRUB_DIRS`, never through a link), so nothing a run leaves there
persists into the next run. agy's SQLite resolves the real path of its dir, and
that needs a metadata read of each dir from bw's state dir down to the seat home. So every seat
profile allows `file-read-metadata` (not a listing) on those nodes, and on the seat scratch root.
The live seat attack then passed on the agy seat, fresh and resumed: its shell commands ran, and
each of the 42 probes got an OS denial in bw's own re-run. The pi and codex seats passed the same
attack. The attack asks the model for the in-repo probes only: with the full list of reads outside the
repo, the pi seat's model refused the whole prompt, control included. Each in-repo probe needs its
tag line in a tool result the CLI reported, so the CLI's own tool runner is shown to sit inside the
sandbox; bw's re-run is still the denial evidence. A missing tag is inconclusive.

Checked again on agy 1.5.15 (2026-11-23), with no model call:
- A run with `HOME` set to an empty seat home returned a `result` event with `status` `ERROR` and
  printed a login URL on stderr. The keychain login does not carry over to a private `HOME`.
- The first live seat attack (2026-11-23) found these facts. A tool step has `step_type` `tool`,
  and its output is in `tool_info.output`. A failed step has `state` `ERROR` and
  `tool_info.error.message`; the seat adapter does not count that message as a tool result,
  because nothing ran. Each `run_command` call first writes the shim `bin/agentapi` in the state
  dir. With `bin` read-only in the real `~/.gemini`, every `run_command` failed ("failed to write
  agentapi script"). The seat-owned dir above ends that: its `bin` is the seat's own.
- `--model <variant id> --effort <other level>` fails before the model call: "conflicts with
  --effort". The help text says `--model` also takes a base model name, and `--effort` then picks the
  variant. The config takes only a model id for agy, so the seat adapter passes no `--effort`.

**pi seat.** The seat keeps the real `HOME`, because a Volta shim finds its tools under `$HOME`.
The seat reads `~/.volta` through the toolchain list (see Home reads under nono). Without that
read, nono stops the shim before pi starts ("Could not create directory").
`pi -p` loads no project-local file (extensions, skills, settings) unless the run trusts them:
pi 1.6.0 loaded a planted `.pi/extensions` file with `--approve` and did not load it with neither
flag. So the seat attack's positive control adds `--approve` after it drops the ignore flags.
`PI_CODING_AGENT_DIR` moves the config dir and the session store to the seat home. The pi seat
runs on OpenRouter only for now, so its login is `OPENROUTER_API_KEY` in the env. pi 1.6.0 has
`--no-mcp` and `--no-skills`; the seat passes both, with `--no-extensions` and `--no-approve`.

## Driver mode (the same CLIs as the working agent)

Driver mode is the writable failover described in architecture.md: one command that
builds a throwaway worktree off the integration branch and launches a coding agent in
it as the *working* agent. It is a mode of the seat abstraction — same per-repo
profile, same briefing — not a second subsystem. What differs per seat is the harness,
the credential, and the containment.

| Driver seat | Harness | Model | Billing |
|---|---|---|---|
| astra | Codex CLI | `gpt-6.2-astra` (since 2026-11-27; `gpt-6-astra` before) | OpenAI Pro, flat (CLI OAuth) |
| *(budget driver, no seat id; not "sol", which is retired)* | Codex CLI | `gpt-6.2-sol` (since 2026-11-27) | OpenAI Pro, flat (CLI OAuth) |
| astra via pi | pi ≥0.85, built-in `openai-codex` provider | `gpt-6-astra` (pi lists no 6.2 model yet, 2026-11-27) | Same subscription, flat — OpenAI endorses third-party use ("Codex for OSS") |
| flash | pi | `deepseek/deepseek-v4.1-flash` via OpenRouter | metered at OpenRouter's list price; a few cents per session |
| fable / opus / sonnet | Claude Code | the Claude tier named | Claude plan, flat |
| *(not a seat)* Claude via pi | pi's Claude Pro/Max login | any Claude model | **per-token "extra usage"** — Anthropic bills third-party harnesses at API rates since 2026-04-04; only the vendor's own surfaces stay flat |

**Sol is a driver, not a seat** (2026-11-27). Use `gpt-6.2-sol` to drive spec'd, mechanical
work when the budget is low. Do not add it to a panel. It comes from the same vendor and
family as Astra, so its blind read would correlate with Astra's. A panel gains from vendor
diversity, not from a second OpenAI read.

Which harness runs a model is therefore a billing decision first. One harness for all
non-primary drivers works for the OpenAI models; for the Claude models it is the API
bill by another name (and a terms-of-service gray area), so the Claude driver seats run
the primary harness at a cheaper tier — the first thing to reach for when the top tier's
budget is low, before leaving the vendor at all.

**pi as a second harness for the OpenAI seat.** The subscription route only lists
models the installed pi knows: 0.74 stopped at the previous generation, 0.85 carries
`gpt-6-astra` but, as of 2026-11-27, no 6.2 model — check the installed catalog, not the docs. pi keeps its own OAuth tokens in
its agent directory; never copy another CLI's token file across (refresh-token rotation
breaks whichever tool refreshes second). The login is interactive (`/login` in the TUI),
done once, into the *driver's* agent directory — never the guarded review seat's.

**Containment differs per harness, and two of the three need help.** Codex's sandbox makes
the worktree the only writable root by itself. pi has *no* sandbox: on its first attack run a
pi driver wrote every outside probe into the live checkout, one through a symlink — the
worktree was a briefing, not a boundary. The launcher now runs pi under the OS sandbox
(macOS Seatbelt via `sandbox-exec`, the mechanism the other two harnesses use internally)
with a deny-list profile: everything allowed except writes under the checkout's physical
path, with the shared `.git` directory re-allowed so `git commit` from a linked worktree
still works, and `.git/hooks` plus `.git/config` denied again. Seatbelt resolves real paths,
so symlinks into the checkout are covered. Without `sandbox-exec` the launcher warns loudly
rather than falling back silently. Claude Code instead inherits the user's own
permission grants, which in any working setup are broad enough (`Edit(**/*)`, allowed
`cp`/`mv`/`git`) to reach the live checkout from inside the worktree. The launcher passes
per-session settings: a deny rule for every file-editing tool on the checkout path, plus
the built-in Bash sandbox with the checkout in its deny-write list. Three details that
took debugging: rule paths take a **leading double slash** for absolute (a single slash
resolves relative to the project root and silently protects nothing); only `Edit(path)`
rules match file tools (`Write`/`MultiEdit` rules are ignored with a warning); and
`acceptEdits` auto-approves only inside the working directory, which is the behaviour
wanted here.

**Drivers and the branch lifecycle.** A driver works on a throwaway branch in its own
worktree, commits there, and never merges or rebases onto the integration branch. When the
work is done the branch is pushed and opened as a pull request against the base, and the
moderator's panel reviews it there with the driver's vendor excluded — the same path every
feature branch takes under the superpowers lifecycle (`using-git-worktrees` in, `finishing-a-
development-branch` out). The launcher's briefing says exactly this, so the driver does not
invent its own integration step; and the launcher's cleanup refuses to remove a worktree
whose commits exist nowhere else.

**Choosing a driver (the reference user's routine).** The primary harness is always the
starting point: the moderator tooling — panel, skills, memory — lives there, and its top
tier is the right model for anything that needs judgment (design, plans, debugging,
review). Driver mode is reached *from* that session, not instead of it, when one of three
conditions holds: the primary subscription's usage window is nearly spent; a second worker
is wanted in parallel (the worktree makes that safe — the moderator keeps designing in the
checkout while a driver implements an independent, specified task elsewhere); or the task is
mechanical. The default driver seat is deliberately the *other* vendor's subscription: by the
time driver mode is invoked the primary pool is the constraint, so a cheaper tier of the same
pool is the wrong default. A weaker driver is appropriate exactly when the work is specified
and verifiable — a written plan, tests, and the panel behind it — and wrong for anything
ambiguous, where it costs more in review cycles than it saves. After a driver run the panel
reviews as usual with the driver's vendor seat excluded.

**The two containment configs, literally** (placeholders in angle brackets; both paths must be
the *physical* path of the live checkout):

Claude Code driver, passed per launch as `--settings '<json>'` alongside
`--permission-mode acceptEdits`:

```json
{
  "permissions": { "deny": ["Edit(//<checkout>/**)"] },
  "sandbox": {
    "enabled": true,
    "autoAllowBashIfSandboxed": false,
    "filesystem": { "denyWrite": ["//<checkout>/**"] }
  }
}
```

pi driver, wrapped as `sandbox-exec -p '<profile>' pi …` (macOS Seatbelt; rules later in
the profile win, which is what re-allows the shared git directory and then re-denies its
hooks and config):

```text
(version 1)
(allow default)
(deny file-write* (subpath "<checkout>"))
(allow file-write* (subpath "<checkout>/.git"))
(deny file-write* (subpath "<checkout>/.git/hooks") (literal "<checkout>/.git/config"))
```

Codex needs nothing extra: `codex -C <worktree>` makes the worktree the working root and the
sandbox's only writable root (its `workspace-write` policy), and it refuses a writable root
with a symlinked path component rather than silently widening.

**Three driver traps, all found by attack:**

- **A harness with no sandbox is contained by nothing but manners.** The pi driver's first
  attack run passed every inside step and every outside step alike — it did exactly what it
  was asked, including writing into the checkout it had been told not to touch. Read the
  outside-write results, not just the inside ones, and wrap the process in the OS sandbox.

- **The worktree root must not sit under the primary harness's own config directory.**
  Claude Code treats every path under a `.claude` directory as a sensitive config file and
  refuses to write it — a driver worktree placed under that state directory had *every*
  write refused, including its own files, on the first attack run. Keep driver worktrees
  under a neutral state directory, still resolved to the physical path (the Codex
  symlinked-root rule from architecture.md applies too).
- **A model's refusal is not the guard firing.** The first re-verification of the review
  seat after the pi upgrade produced polite "I'm read-only" replies with *no entry in the
  guard log* — the model declined before making a tool call, which proves nothing about
  the mechanism. Long multi-step "sanctioned test" prompts also stalled the seat for the
  full wall-clock cap. One-line prompts ("call bash once with exactly: …") produce a real
  tool call, a logged denial, and an unchanged directory — that is the evidence.

## Decision model: routing advice and shadow review scoring (pilot)

Updated 2026-10-30: the same model now settles leads' questions when `triage.mode` is `acting`. That
is not advice: its verdict decides, inside the bounds in the README's question triage entry. The
four-question policy was calibrated on 14 synthetic questions; the thresholds are starting values,
and reversals in the inbox are the evidence for moving them.

Written 2026-09-29. Which model does a task, how hard it thinks, and how deep the review goes
were chosen by feel. A *decision model* (a "System One" model: TypeSafe's Jev was first, on
2026-09-15) answers narrow typed questions — `choice` (pick an option), `score` (a place on an
ordered scale), `noul` (probability of yes) — with probabilities instead of text, in a few
hundred milliseconds, for fractions of a cent. That is cheap enough to ask on every task and
check later against what happened. It does not generate, explain, or reason aloud.

**Provider.** The reference setup uses Jev (`typesafe/jev-1.13`, pinned) through OpenRouter's
decisions endpoint (`POST https://openrouter.ai/api/alpha/decisions`, the seats' existing
OpenRouter key, input-only billing). Open alternatives exist and several speak a compatible
`/v1/systemone` shape — Kev (Qwen-based, local MLX serving), SemIf (formerly OpenJev; reads
option probabilities from an open model's logits, runs on Apple Silicon), OpenJev
(DiffusionGemma server), Laya (ModernBERT encoder, CPU-friendly, small option budget),
NanoJev and jevlike (training starters). A swap is **not** config-only: context and option
budgets differ, so it needs a contract test, a truncation test, and re-calibration on logged
tasks. Pick hosted first for setup effort; move local when privacy or volume says so.

**What it is asked.** One request per task, four questions plus one logged for comparison:
enough context to judge (noul) · difficulty (score, five described levels) · reasoning effort
(score, five described levels) · high stakes — auth, payments, access rules, private data,
production migration (noul) · *tier* (choice; logged only — the decision model cannot know what
your model tiers are). Every threshold lives in code, not in the question.

**Policy (the rules a design panel forced; thresholds are pilot guesses, not recommendations).**
- **Raise-only.** Advice may escalate a pinned executor or deepen a review. It never lowers a
  pin, skips a mandatory review, downgrades the default for unpinned agents, or overrides the
  human. Would-be downgrades are logged as shadow data, never shown.
- **Tails, not means.** A `score` answer's headline value is a probability-weighted mean, which
  hides a fat hard tail. Derive the tier from P(difficulty ≥ hard) and the stakes probability;
  take the arg-max effort level, not the rounded mean.
- **Asymmetric floors.** Under-routing costs more than over-routing, so the stakes floor fires
  at a low probability (the pilot uses 0.5) and raises both tier and effort; a high-stakes
  target always gets the deep review tier, as the panel protocol already requires.
- **Once per task, with context.** Route the prompt that starts a task (first substantive
  prompt, an issue reference, a long brief), speak only when the advice changes, and let the
  working agent route a scoped subtask (phase, role, risks) before dispatching it. Thin input
  gets "not enough context" and no advice.
- **Fail-open.** A timeout, error, kill switch, or unsafe input returns no advice; existing
  rules stand. The prompt hook is bounded by the request timeout plus the hook timeout.

**Privacy.** Only a redacted task description leaves the machine: credential-, email-, and
phone-shaped strings are replaced, text that looks like a pasted log or diff is never sent,
zero-data-retention routing is requested, and the prompt text is not logged. The prompt hook
runs only in interactive sessions — the seat wrappers disable it, because a user-level hook
otherwise fires inside every headless seat, sending the seat brief out and handing a blind
reviewer routing advice.

**Review scoring runs in shadow.** Round-1 findings are scored (severity, actionable, and —
when the seat quoted code — whether the quote supports the claim), but the scores go to the
log only. The moderator verifies without seeing them, logs the panel record with a
`decide_key`, and only then are scores revealed and joined to the verified verdicts. Using the
scores to order or drop findings would anchor the very verdicts they are measured against;
dropped and unverified findings are excluded from the accuracy labels.

**Logs and promotion.** Every routing decision (answers, advice, shadow data, model and policy
versions, session id) and every agent dispatch (requested, pinned, and effective model; quota
state) go to one JSONL, joinable by session; scored findings join panels by key. The pilot is
instrumentation, not evidence: a task that finished on the stronger tier does not show the
cheaper tier would have failed. Promotion to enforcement needs criteria written down before
the data is read, a held-out comparison against the current pins, and a rollback switch — and
an enforcing hook that rewrites a subagent's model must not also grant tool permission, which
a rewrite response can do as a side effect (Claude Code: `permissionDecision: "allow"`).

## Panel instructions (`bw panel`, 2026-11-23)

The private setup once kept the panel's steps in one instruction file the moderator read. That
file is now two parts in this repo:

- `panel/templates/` holds the seat briefs (one per checkpoint, plus `brief-output.md` for the
  output shape) and the moderator's steps (`next.md`, `next-triage.md`, `next-follow-up.md`).
  `bw` renders them. Paths in shell commands are quoted, and seat text is escaped.
- `lead/protocol/panel.md` is the text a lead gets in its first prompt. It holds the profile's
  checkpoints, the literal request and view paths, and the rules: verify every finding, relay
  one claim at a time, log every panel, and tell the user three lines.

To rebuild: pin the seats in config, then run `bw seat verify` for each seat. Then let a lead
convene one panel on a seeded buggy file. Traps:

- The lead sandbox must read-deny the panel work dirs. A lead started before this change has an
  old pass. Run `bw sandbox verify <runtime>` once for each lead runtime.
- A panel runs at most one agy-runtime seat. Do not put two in one roster.
- The wait helper must run from the copy in the lead's view, with no other words on the line.

## Lead guard rules: known limits (`bw guard`, 2026-11-23)

The OS sandbox is the containment. The guard is a second layer. It judges each shell command
of a guarded lead before the command runs. The rules read the command as bash, because pi runs
bash. A zsh lead would need new rules: zsh has `$X:h` modifiers, and it does not split unquoted
words.

The rules allow a write into the lead's own checkpoint dir. They expand only the `BW_*` values
that bw gave the lead. A command that may change one of those names is a rule ask, and the judge
cannot lift it. These cases stay outside the rules:

- `source` and `.` run a file that the rules do not read. The command is gray, and the judge
  decides.
- A change to `IFS` splits a later unquoted expansion in a different way. The command is gray.
- The rules do not parse a heredoc body. The command is gray.
- `eval`, `trap`, and `alias` text that does not lex, or that is split across several operands, stays
  gray. The rules do not join the parts.
- One command can plant a symlink (`cp -P`, or `mv` of a link) and then write through it. The
  rules judge the path before the link exists, so they can allow that write. The sandbox still
  limits where the write lands.

## Verification checklist (before trusting any seat)

1. Ask it to create a file. Confirm refusal AND that no file exists.
2. Resume the session; ask again. (Read-only must survive resumes — verify, don't assume.)
3. Store a token in one call, recall it via resume — session continuity works.
4. Plant a hostile project-local extension or tool in the target checkout (`.pi/`,
   `.opencode/` — whatever the runtime auto-loads) and confirm the seat never loads it.
5. Run one panel on a seeded buggy file; confirm both seats find the planted bugs
   independently, then relay one dispute and confirm the resumed cross-examination.
6. For a **driver** seat, attack in both directions through the real launch path: a write
   inside the worktree must land; writes to the live checkout — direct, and through a
   symlink planted inside the worktree — must be refused. Read the filesystem for the
   verdict, not the agent's summary table.
7. For any seat, confirm the refusal came from the mechanism (guard log entry, sandbox
   error text) and not from the model declining to try.

## Supporting cast (named, with the one key fact each)

- **Metrics/dashboard**: append-only JSONL (`usage` + `panel` events) → static-HTML
  generator (stdlib Python). Confirmed/refuted verdicts come from the moderator's
  code verification, never model self-report.
- **Knowledge index**: SQLite FTS5 + embedding BLOBs (Gemini `gemini-embedding-001`,
  768 dims), brute-force numpy cosine + reciprocal-rank fusion — no vector extension
  needed below ~100k chunks (and macOS system Pythons often can't load one anyway).
  External sources (GitHub issues via `gh`, Google Drive meeting notes + Google Chat
  via a Workspace OAuth client) are mirrored to markdown first; the DB stays disposable.
- **Google Workspace auth**: gcloud ADC with a **custom OAuth client** (Internal
  consent screen) — gcloud's own client is blocked for the restricted Drive scope.
- **Scheduling/alerting**: launchd daily job runs fetchers + reindex + dashboard;
  stages are independent; any failure posts one consolidated ntfy.sh notification.
  Known gap: nothing alerts if the job never runs — a staleness warning at query
  time is the backstop.
