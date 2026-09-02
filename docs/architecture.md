# Balancewheel — Architecture

*A multi-model review panel with a win-rate scoreboard. Written 2026-08-26 from a
working private implementation; paths and names below are the reference setup's.*

## The problem

Frontier coding agents are single-provider by session: Claude Code cannot route its
subagents to OpenAI or OpenRouter models (per-agent provider routing is a
[long-open feature request](https://github.com/anthropics/claude-code/issues/38698)).
But the most valuable second opinion is one from a *different* model family — different
training, different blind spots. Existing answers are unsatisfying: MCP middle layers
go stale, router proxies translate prompts lossily and fight vendor auth policies, and
switching your whole workflow to a multi-provider harness forfeits your primary
agent's ecosystem (skills, instruction files, subscription pricing).

The insight that unlocked it: **every major vendor now ships its own repo-aware CLI
agent.** You don't need one harness that speaks to every model. You need your primary
agent to treat the other vendors' agents as *peer seats* — and a protocol that turns
their disagreement into signal instead of noise.

## Architecture: three peers, one moderator

| Role | Runs as | State |
|---|---|---|
| Moderator / orchestrator | Primary agent (Claude Code session) | its own transcript + instruction files |
| Seat A ("sol") | Codex CLI headless (`codex exec -p review`), pinned model, read-only sandbox | Codex session store (resumable) |
| Seat B ("ox") | OpenCode headless (`opencode run --agent ox`), pinned OpenRouter model, deny-listed writes | OpenCode SQLite session store (resumable) |

Each seat is wrapped in a ~60-line shell script with a uniform interface:

```
seat.sh [-r] [-d dir] "prompt"     # -r resumes this directory's saved session
```

Wrapper responsibilities: self-source credentials, absolutize the target dir, run the
CLI with `TMPDIR` pointed at a private scratch directory (vendors extract temp native
libs into `$TMPDIR`, and some agent sandboxes set that to the checkout — never let
it litter one), recover and persist the session id per target directory, and
append a usage event to the metrics log. That's the whole integration surface — no
SDKs, no proxies.

**Read-only is load-bearing.** These are autonomous agents with write-capable tools
pointed at a live checkout. Codex gets `sandbox_mode = "read-only"`; the OpenCode
agent gets `edit: deny` plus a bash allow-list of read-only commands. Verify by
attack, not by docs: ask each seat to create a file, on both fresh and resumed
sessions, and confirm refusal before trusting it.

## The panel protocol

1. **One shared brief.** The moderator frames the target (a branch diff, a plan file,
   a design question), the stack, the focus dimensions, and the required output shape
   (findings with file:line and severity; "no significant findings" allowed).
2. **Round 1 — independent.** Both seats run in parallel with the same brief and no
   sight of each other. Independence first: agreement between blind reviewers is the
   strongest signal the panel produces.
3. **Round 2 — targeted cross-examination.** Only where seats disagree, or a
   load-bearing finding looks shaky, the moderator relays the *specific claim* to the
   other seat's resumed session: "the other reviewer claims X at file:line — verify
   against the code and agree or refute with evidence." Whole-transcript relays are
   banned; they invite rhetoric. Rounds are capped; convergence is not required.
4. **Moderation with a substance bar.** A challenge earns a rebuttal round only if it
   disputes facts, behavior, or severity with concrete evidence ("what breaks,
   where"). Wording, style, and degree-of-certainty objections are dismissed by the
   moderator without relay — and logged as disputes scored against the challenger.
   Per-seat conduct rules live in the instruction files and are tuned from the
   scoreboard (see below).
5. **Verification gate.** The moderator verifies every surviving finding against the
   actual code before reporting. Seats have no authority — they generate hypotheses;
   the code decides. Output: consensus findings (verified), disputed findings with
   the moderator's ruling and reasoning, and each seat's unique catches, attributed.
6. **Mandatory outcome logging.** The moderator appends a structured record (below).
   A panel that isn't logged didn't happen, as far as tuning is concerned.

## The metrics loop

Append-only JSONL, two event types:

```jsonc
{"type": "usage", "seat": "ox-agent", "mode": "resume", "dir": "...", "duration_s": 87, "ok": true}
{"type": "panel", "target": "PR #123 diff", "seats": ["sol", "ox"], "rounds": 2,
 "immediate_agreement": false,
 "findings": {"sol": {"total": 4, "confirmed": 3, "refuted": 1},
              "ox":  {"total": 5, "confirmed": 5, "refuted": 0}},
 "disputes": [{"summary": "...", "challenger": "sol",
               "proposals": {"ox": "...", "sol": "..."},
               "winner": "ox", "reason": "..."}]}
```

`confirmed`/`refuted` are the moderator's *code-verification verdicts*, not
self-reports — this is the ground-truth accuracy signal per model. A generator script
renders the log into a static dashboard: usage share per seat, immediate-agreement
rate, and a per-model scoreboard — findings, confirmed rate, challenges raised,
disputes won, win share — plus a table of recent disputes with both proposals and the
ruling.

**The loop closes as governance.** When a seat shows a high challenge count with a low
win share, its policy is tightened *as a versioned instruction change with cited
evidence and explicit re-promotion criteria*. Real example from the reference
implementation: one seat repeatedly challenged wording while conceding substance; it
was scoped to code review only (its confirmed-finding rate was perfect — the problem
was dispute conduct, not competence), its challenges now require a substance bar, and
the policy states the reversal condition (≥10 logged disputes with >50% win share).
That policy is exactly the kind of artifact
[OpenEscapement](https://github.com/tensorgroup/openescapement) ships as a signed rule
pack — see its `model-seats` example pack, which is this policy generalized.

## The knowledge layer

The panel gets sharper when seats and moderator can consult accumulated project
knowledge. Rules that keep that sane:

- **Markdown in git is the only canonical store** — decision records (ADRs), specs,
  and guides are ordinary reviewed files. Databases never hold the only copy of
  anything.
- **Search indexes are derived and disposable**: a local SQLite index (FTS5 keyword +
  embedding cosine, fused by reciprocal rank) over the repo docs plus mirrored
  external sources (issue trackers, meeting notes, chat) — each mirrored *as
  markdown* first, then indexed. Delete-and-rebuild is always safe.
- **A per-repo, dependency-free variant** (stdlib SQLite FTS5 only, no keys) ships in
  the repo itself so every contributor gets search without any of the private
  sources.

## Lessons learned (the expensive ones)

- **Session continuity beats context stuffing.** Resumable per-directory seat
  sessions let cross-examination reference earlier rounds without re-briefing, and
  vendors' own stores (Codex sessions, OpenCode SQLite) are more reliable than any
  bridge you'd build.
- **Stealth/preview models are load-bearing quicksand.** Pin them in one config line
  you expect to change; never bake them into interfaces.
- **Vendor CLIs churn.** A profile format broke mid-build; a config table silently
  became "legacy." Wrappers isolate the blast radius to one file.
- **Model personality is a governance problem, not a prompt problem.** Politely
  asking a model to stop litigating nits does little; a moderation rule that dismisses
  non-substantive challenges *and scores them* changes the economics.
- **Blind first rounds are non-negotiable.** The one time seats see each other's
  output before writing their own, you've built an echo chamber with extra steps.
