# Reference implementation — the working example

*The concrete stack behind [architecture.md](architecture.md), with the software and
models named and the configuration that matters. Not a dump of the private setup —
a rebuild guide: each section names the tool, the settings that took debugging to
get right, and the traps. Written 2026-08-26 against the versions noted; updated
2026-09-02 when the stealth seat model graduated (see the OpenCode section).*

## The cast

| Role | Software | Model | Billing |
|---|---|---|---|
| Moderator / primary | Claude Code (CLI) | Claude (Fable/Opus tier) | Anthropic subscription |
| Code-review seat ("sol") | Codex CLI ≥0.149 | `gpt-5.6-sol`, high reasoning | ChatGPT subscription (CLI OAuth) |
| Planning-lead seat ("ox") | OpenCode ≥1.18 | `z-ai/glm-5.3-flash` via OpenRouter (was `stealth/ox-alpha` until 2026-09-02) | OpenRouter API key (prepaid credits) |
| Blind one-shot fallback | plain HTTPS (`chat/completions`) | same OpenRouter model | OpenRouter API key |

Why these: each vendor's *own* CLI gives the seat repo exploration, native session
storage, and subscription billing — no proxy, no prompt translation. Roles were
assigned from logged dispute outcomes (see architecture.md); the seat model is a
config detail, not an interface commitment — which paid off within a week (below).

## Seat: Codex CLI (`gpt-5.6-sol`)

Profile file — **`~/.codex/review.config.toml`** (Codex ≥0.149 uses per-profile
*files*; a `[profiles.review]` table inside `config.toml` is rejected as legacy):

```toml
model = "gpt-5.6-sol"
model_reasoning_effort = "high"
approval_policy = "never"       # headless: no interactive prompts
sandbox_mode = "read-only"      # the load-bearing line
```

Invocation and traps:

- New session: `codex exec -p review -C <target-dir> --skip-git-repo-check "<prompt>"`.
  Session id appears on stderr as `session id: <uuid>` — capture it for resumes.
- Resume: `codex exec resume <uuid> "<prompt>"` — accepts **no `-p`/`-C`**; replicate
  the profile via `-c key=value` overrides and `cd` to the target first.
- Set `TMPDIR` to a private scratch dir: the Rust binary extracts multi-MB temp
  `.dylib`s into `$TMPDIR` — *not* cwd, as first assumed. Some agent sandboxes point
  `TMPDIR` at the project checkout, which litters it with `.<hash>-00000000.dylib`
  files; found 2026-09-02 after the wrappers had been "running from a scratch dir"
  for a week. Derive the scratch from the real system temp dir
  (`getconf DARWIN_USER_TEMP_DIR` on macOS), export it as `TMPDIR`, remove on exit.
- Auth survives ChatGPT-app removal (CLI holds its own OAuth in `~/.codex/auth.json`),
  but the desktop app manages `config.toml` — expect churn there; keep your profile in
  its own file.

## Seat: OpenCode (`z-ai/glm-5.3-flash` via OpenRouter)

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

Config — **`~/.config/opencode/opencode.jsonc`**. An explicit provider `apiKey`
(env-substituted) is required; the explicit model entry was mandatory for the stealth
model (absent from models.dev) and is kept for the catalog model so reasoning,
tool-calling, and limits stay pinned rather than inherited:

```jsonc
{
  "provider": {
    "openrouter": {
      "options": { "apiKey": "{env:OPENROUTER_API_KEY}" },
      "models": {
        "z-ai/glm-5.3-flash": {
          "reasoning": true, "tool_call": true,
          "limit": { "context": 1310720, "output": 131072 }  // from /api/v1/models
        }
      }
    }
  },
  "agent": {
    "ox": {
      "mode": "primary",
      "model": "openrouter/z-ai/glm-5.3-flash",
      "prompt": "<reviewer system prompt: read-only, evidence-first, severity-ranked>",
      "permission": {
        "edit": "deny",
        "bash": {
          "*": "deny",
          "git status*": "allow", "git log*": "allow", "git diff*": "allow",
          "git show*": "allow", "git blame*": "allow", "git branch*": "allow",
          "ls *": "allow", "cat *": "allow", "head *": "allow", "tail *": "allow", "wc *": "allow"
        }
      }
    }
  }
}
```

Invocation: `opencode run --dir <target> --agent ox [-s <session-id>] "<prompt>"`.
Sessions live in SQLite (`~/.local/share/opencode/opencode.db`); recover the newest
id for a directory from the `session` table (`id`, `directory`, `time_created`).
The env var must be **exported** — an unexported shell variable yields
"Missing Authentication header".

## Seat wrappers (the whole integration surface)

One ~60-line shell script per seat, uniform interface `seat.sh [-r] [-d dir] "prompt"`:
self-source credentials → absolutize `-d` → make a private scratch dir under the
system temp root and export it as `TMPDIR` → run the CLI from it →
persist session id per target directory (tsv) → append a `usage` event to the metrics
JSONL → exit with the CLI's status. The moderator (Claude Code) drives seats through
slash-command instructions that carry the panel protocol and the mandatory
outcome-logging step.

## Verification checklist (before trusting any seat)

1. Ask it to create a file. Confirm refusal AND that no file exists.
2. Resume the session; ask again. (Read-only must survive resumes — verify, don't assume.)
3. Store a token in one call, recall it via resume — session continuity works.
4. Run one panel on a seeded buggy file; confirm both seats find the planted bugs
   independently, then relay one dispute and confirm the resumed cross-examination.

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
