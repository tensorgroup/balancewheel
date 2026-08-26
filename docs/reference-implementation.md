# Reference implementation — the working example

*The concrete stack behind [architecture.md](architecture.md), with the software and
models named and the configuration that matters. Not a dump of the private setup —
a rebuild guide: each section names the tool, the settings that took debugging to
get right, and the traps. Written 2026-08-26 against the versions noted.*

## The cast

| Role | Software | Model | Billing |
|---|---|---|---|
| Moderator / primary | Claude Code (CLI) | Claude (Fable/Opus tier) | Anthropic subscription |
| Code-review seat ("sol") | Codex CLI ≥0.149 | `gpt-5.6-sol`, high reasoning | ChatGPT subscription (CLI OAuth) |
| Planning-lead seat ("ox") | OpenCode ≥1.18 | `stealth/ox-alpha` via OpenRouter | OpenRouter API key |
| Blind one-shot fallback | plain HTTPS (`chat/completions`) | same OpenRouter model | OpenRouter API key |

Why these: each vendor's *own* CLI gives the seat repo exploration, native session
storage, and subscription billing — no proxy, no prompt translation. Roles were
assigned from logged dispute outcomes (see architecture.md); the stealth model is a
config detail, not an interface commitment.

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
- Run from a scratch directory: the Rust binary extracts multi-MB temp `.dylib`s into
  its *cwd* (target dir is what `-C` points at, cwd can be anywhere).
- Auth survives ChatGPT-app removal (CLI holds its own OAuth in `~/.codex/auth.json`),
  but the desktop app manages `config.toml` — expect churn there; keep your profile in
  its own file.

## Seat: OpenCode (`stealth/ox-alpha` via OpenRouter)

Config — **`~/.config/opencode/opencode.jsonc`**. Two things stealth/preview models
need that catalog models don't: an explicit provider `apiKey` (env-substituted) and a
hand-registered model entry (they're absent from models.dev):

```jsonc
{
  "provider": {
    "openrouter": {
      "options": { "apiKey": "{env:OPENROUTER_API_KEY}" },
      "models": {
        "stealth/ox-alpha": {
          "reasoning": true, "tool_call": true,
          "limit": { "context": 1048576, "output": 131072 }  // from /api/v1/models
        }
      }
    }
  },
  "agent": {
    "ox": {
      "mode": "primary",
      "model": "openrouter/stealth/ox-alpha",
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
self-source credentials → absolutize `-d` → `cd` to a scratch dir → run the CLI →
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
