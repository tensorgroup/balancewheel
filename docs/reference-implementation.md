# Reference implementation — the working example

*The concrete stack behind [architecture.md](architecture.md), with the software and
models named and the configuration that matters. Not a dump of the private setup —
a rebuild guide: each section names the tool, the settings that took debugging to
get right, and the traps. Written 2026-08-26 against the versions noted; updated
2026-09-02 when the stealth seat model graduated (see the pi section); updated
2026-09-11 when the Codex seat moved to a new model generation and gained planning duties
(see the Codex section) and to describe the ox seat's runtime move from OpenCode to pi
(2026-09-03); updated 2026-09-16 with the driver-mode section (which harness may run
which model at flat rate, first-party-harness containment, two new traps) and the pi
version bump to 0.85.x it required.*

## The cast

| Role | Software | Model | Billing |
|---|---|---|---|
| Moderator / primary | Claude Code (CLI) | Claude (Fable/Opus tier) | Anthropic subscription |
| Planning + review seat ("astra") | Codex CLI ≥0.153 | `gpt-6-astra`, xhigh reasoning (was `gpt-5.6-sol`, seat "sol", until 2026-09-11) | OpenAI Pro subscription (CLI OAuth) |
| Planning seat ("ox") | pi 0.85.x (0.74.x until 2026-09-16; was OpenCode ≥1.18 until 2026-09-03) | `z-ai/glm-5.3-flash` via OpenRouter (was `stealth/ox-alpha` until 2026-09-02) | OpenRouter API key (prepaid credits) |
| Blind one-shot fallback | plain HTTPS (`chat/completions`) | same OpenRouter model | OpenRouter API key |

Why these: each vendor's *own* CLI gives the seat repo exploration, native session
storage, and subscription billing — no proxy, no prompt translation. Roles were
assigned from logged dispute outcomes (see architecture.md); the seat model is a
config detail, not an interface commitment — which paid off within a week (below).

## Seat: Codex CLI (`gpt-6-astra`)

Profile file — **`~/.codex/review.config.toml`** (Codex ≥0.149 uses per-profile
*files*; a `[profiles.review]` table inside `config.toml` is rejected as legacy):

```toml
model = "gpt-6-astra"
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
- Model swaps are seat swaps when the model changes generation: the reference setup
  retired seat "sol" and started "astra" with an empty scoreboard, because seat policy
  (roles, rebuttal caps) was earned by the old model's dispute record and the per-seat
  metrics would otherwise blend two models. A same-model re-pin (the stealth-id
  graduation below) keeps the seat id and its history.
- Set `TMPDIR` to a private scratch dir: the Rust binary extracts multi-MB temp
  `.dylib`s into `$TMPDIR` — *not* cwd, as first assumed. Some agent sandboxes point
  `TMPDIR` at the project checkout, which litters it with `.<hash>-00000000.dylib`
  files; found 2026-09-02 after the wrappers had been "running from a scratch dir"
  for a week. Derive the scratch from the real system temp dir
  (`getconf DARWIN_USER_TEMP_DIR` on macOS), export it as `TMPDIR`, remove on exit.
- Auth survives ChatGPT-app removal (CLI holds its own OAuth in `~/.codex/auth.json`),
  but the desktop app manages `config.toml` — expect churn there; keep your profile in
  its own file.

## Seat: pi (`z-ai/glm-5.3-flash` via OpenRouter)

Runtime: [pi](https://www.npmjs.com/package/@earendil-works/pi-coding-agent)
(`@earendil-works/pi-coding-agent`, 0.85.x since 2026-09-16 — 0.74.x before; `npm install
-g --ignore-scripts`). Re-verify the read-only layers by attack after every upgrade: the
0.74→0.85 jump crossed eleven minor versions and was re-checked the same day. This
seat ran on OpenCode until 2026-09-03; the move is described at the end of the section.

**The stealth-model lesson, lived (2026-09-02):** the seat originally ran on
`stealth/ox-alpha`, a free preview. OpenRouter ended the test period one week after
setup; every call then returned a 404 that also revealed the model's identity (ZAI's
GLM-5.3 Flash). Because the id was pinned in exactly one config line per component,
re-pinning to the graduated catalog model took minutes; the logged seat history and
policy carried over unchanged since the underlying model did not change. Two
operational notes: the successor is paid, so the OpenRouter account must hold
credits (a $0 balance fails identically to a missing model), and a per-key spend cap
is the cost control — at $0.075/M input, $0.25/M output a repo-aware review round is
about two cents.

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
  { "id": "z-ai/glm-5.3-flash", "name": "GLM-5.3 Flash (ox seat)",
    "reasoning": true, "input": ["text"],
    "contextWindow": 1310720, "maxTokens": 131072,
    "cost": { "input": 0.075, "output": 0.25, "cacheRead": 0, "cacheWrite": 0 } }
] } } }
```

Invocation (from the target directory, `OPENROUTER_API_KEY` **exported**):

```
pi -p --model openrouter/z-ai/glm-5.3-flash --thinking medium \
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

One ~60-line shell script per seat, uniform interface `seat.sh [-r] [-d dir] "prompt"`:
self-source credentials → absolutize `-d` → make a private scratch dir under the
system temp root and export it as `TMPDIR` → run the CLI from it →
persist session id per target directory (tsv) → append a `usage` event to the metrics
JSONL → exit with the CLI's status. The moderator (Claude Code) drives seats through
slash-command instructions that carry the panel protocol and the mandatory
outcome-logging step.

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

## Driver mode (the same CLIs as the working agent)

Driver mode is the writable failover described in architecture.md: one command that
builds a throwaway worktree off the integration branch and launches a coding agent in
it as the *working* agent. It is a mode of the seat abstraction — same per-repo
profile, same briefing — not a second subsystem. What differs per seat is the harness,
the credential, and the containment.

| Driver seat | Harness | Model | Billing |
|---|---|---|---|
| astra | Codex CLI | `gpt-6-astra` | OpenAI Pro, flat (CLI OAuth) |
| astra via pi | pi ≥0.85, built-in `openai-codex` provider | `gpt-6-astra` | Same subscription, flat — OpenAI endorses third-party use ("Codex for OSS") |
| ox | pi | `z-ai/glm-5.3-flash` via OpenRouter | ~$0.02 per session |
| fable / opus / sonnet | Claude Code | the Claude tier named | Claude plan, flat |
| *(not a seat)* Claude via pi | pi's Claude Pro/Max login | any Claude model | **per-token "extra usage"** — Anthropic bills third-party harnesses at API rates since 2026-04-04; only the vendor's own surfaces stay flat |

Which harness runs a model is therefore a billing decision first. One harness for all
non-primary drivers works for the OpenAI models; for the Claude models it is the API
bill by another name (and a terms-of-service gray area), so the Claude driver seats run
the primary harness at a cheaper tier — the first thing to reach for when the top tier's
budget is low, before leaving the vendor at all.

**pi as a second harness for the OpenAI seat.** The subscription route only lists
models the installed pi knows: 0.74 stopped at the previous generation, 0.85 carries the
current one — check the installed catalog, not the docs. pi keeps its own OAuth tokens in
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
