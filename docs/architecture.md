# Balancewheel — Architecture

*A multi-model review panel with a win-rate scoreboard. Written 2026-08-26 from a
working private implementation; paths and names below are the reference setup's.
Seat names refreshed 2026-09-11 when the Codex seat changed model generation; seat B's
runtime updated the same day (pi since 2026-09-03, OpenCode before). Driver mode added
2026-09-15, separating the read-only seat guarantee from writable vendor failover;
extended 2026-09-16 to same-vendor cheaper tiers, with the harness-billing and
first-party-harness containment notes. The OpenEscapement hand-off corrected the same
day: the two tools' scopes are stated, and the `model-seats` pack shipped in esc's
examples that evening, keyed by seat with the models as a dated roster; the same-vendor
blind seat and the on-demand seat pattern, in the reference roster since 2026-09-02,
are documented in the architecture table below for the first time.*

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
| Seat A ("astra") | Codex CLI headless (`codex exec -p review`), pinned model, read-only sandbox | Codex session store (resumable) |
| Seat B ("ox") | pi headless (`pi -p --tools read,grep,find,ls,bash -e guard.ts`), pinned OpenRouter model, in-process read-only guard | JSONL session file chosen by the wrapper (resumable) |
| Seat C ("fable") | the moderator's own vendor CLI, headless, in a fresh session with none of the moderator's conversation; write tools disallowed plus a pre-tool guard | the CLI's own session store (resumable) |

Seat C is the **same-vendor blind seat** (added to the reference roster 2026-09-02):
the moderator's model family run without the moderator's context, so its round-1
agreement is an independent signal rather than an echo. It is scored as a peer, and
the moderator must not favor it: its findings are verified with the same rigor and
logged under its own seat id, never folded into the moderator's verdict. A seat can
also be **on demand** rather than default: the reference roster dropped a second
same-vendor seat from the default panel on scoreboard evidence (one unique catch in its
first thirteen findings, and the log's only refuted finding) and convenes it only for
high-stakes targets, with a stated re-promotion criterion (five consecutive panels with
at least one confirmed unique catch). Cross-examination is capped at two rounds, and
each seat at a wall-clock cap (twelve minutes standard, thirty for a deep tier): a seat
that does not return is logged as absent and the panel proceeds; a timeout never
changes the roster.

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
pointed at a live checkout. Codex gets `sandbox_mode = "read-only"`; the pi seat
gets a tool allowlist with no write/edit tool plus an in-process `tool_call` guard
that admits only single read-only bash commands (a command-line glob allow-list, the
previous runtime's mechanism, let `git log | tee x` through). Verify by
attack, not by docs: ask each seat to create a file, on both fresh and resumed
sessions, and confirm refusal before trusting it.

### Seat mode and driver mode are different things

The read-only guarantee above is scoped to **seats**. The same vendor CLIs can also be
run as a **driver** — the agent actually doing the work — and that mode is deliberately
writable. Keeping the two labelled apart matters, because they are contained by
different mechanisms and only one of them is scored.

| | Seat mode | Driver mode |
|---|---|---|
| Purpose | Review, blind and independent | Do the work |
| Writes | Never; enforced by sandbox + guard | Yes, by design |
| Contained by | Tool allowlist and read-only sandbox | A dedicated worktree as the only writable root |
| Target | The live checkout, read in place | A throwaway branch off the integration branch |
| Metrics | Logged and scored against verified findings | Not scored — it is not making review claims |

Driver mode exists for one reason: **the moderator's capacity is a single point of
failure.** When the primary agent's budget runs out, the work stops even though two
capable peers are sitting right there, already repo-aware and already paid for. Vendor
failover keeps the pattern working on the day it would otherwise be most useless.

This is a fallback, not a promotion: the moderator stays primary, and a driver seat
never reviews its own output — that would collapse the blind-round rule the whole
protocol rests on. When a driver has written the code, the panel that reviews it
should run with that vendor's seat excluded, or with the finding treated as
self-assessment and marked accordingly.

Containment moves with the mode. A seat is safe because it *cannot* write; a driver is
safe because the only thing it can write is a scratch worktree, never the checkout the
author is working in. Two traps found by attacking it rather than reading docs: a
sandbox may refuse a writable root that merely *contains a symlinked path component*
(failing every command, not just the ones crossing the link), and basing the worktree
on a remote branch that the local one is far ahead of will hand the driver a stale tree
without any error at all. Test both directions — that writes inside the worktree
succeed, and that writes aimed at the real checkout, including through any symlink the
setup created, are refused.

A driver need not be a different vendor. The cheapest failover is often the primary
vendor's own smaller tier through the same first-party harness, tried before leaving the
vendor at all. Two things decide *which harness* runs a given model, and neither is about
capability. First, billing: a subscription that covers a vendor's own CLI at a flat rate
may bill the same models per token when they are reached through a third-party harness
(the Claude subscription does exactly this since April 2026, while the OpenAI one
explicitly endorses third-party use), so a peer harness that is the right choice for one
vendor's models is the API bill by another name for another's. Second, containment: a
first-party harness inherits the user's own permission grants, which are usually broad
enough to reach the live checkout from inside the worktree, and a harness with no sandbox
of its own will simply do what it is asked, checkout included. The first needs per-launch
deny rules on the checkout path plus the harness's own command sandbox; the second needs
the operating system's sandbox wrapped around the whole process. Only then is the
worktree a boundary rather than a suggestion — and, as with everything else here, the
proof is an attack in both directions, not the settings file.

## The panel protocol

**Where it sits.** The panel is not a separate workflow; it is a set of checkpoints inside
the one the moderator already follows. The reference setup uses the
[superpowers](https://github.com/obra/superpowers) skill lifecycle — brainstorm → worktree →
written plan → execute (often via subagents) → verify → request review → finish the branch
— and convenes the panel at four of its seams: when a design converges (before the spec),
when a plan is written (before approval), at code review (in the same parallel batch as
any single-model reviewers), and in debugging when the first hypothesis fails. Two
consequences shape the rest of the design: feature work already lives on a branch in its
own worktree and lands by pull request, so a driver's worktree is the same shape the
lifecycle already expects and its branch finishes the same way; and review targets are
usually a branch diff or a plan file, which is what the brief points at.

1. **One shared brief.** The moderator frames the target (a branch diff, a plan file,
   a design question), the stack, the focus dimensions, and the required output shape
   (findings with file:line and severity; "no significant findings" allowed).
2. **Round 1 — independent.** Both seats run in parallel with the same brief and no
   sight of each other. Independence first: agreement between blind reviewers is the
   strongest signal the panel produces. Each seat gets a hard wall-clock cap, enforced
   by its wrapper rather than by the moderator's tool timeout (the reference setup uses
   12 minutes by default and 30 in a *deep* tier that also raises every seat's reasoning
   effort — opted into for an unresolved dispute, a high-stakes area, or on request). A
   seat that misses the cap is recorded as *absent* on that panel and excluded from its
   per-seat counts, and the panel proceeds on the seats that returned. One slow run
   never blocks a review, and never changes the roster — that stays with the metrics
   loop below.
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
{"type": "panel", "target": "PR #123 diff", "seats": ["astra", "ox"], "rounds": 2,
 "immediate_agreement": false,
 "findings": {"astra": {"total": 4, "confirmed": 3, "refuted": 1},
              "ox":  {"total": 5, "confirmed": 5, "refuted": 0}},
 "disputes": [{"summary": "...", "challenger": "astra",
               "proposals": {"ox": "...", "astra": "..."},
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
pack, rendered into a repo's instruction files and versioned like any other policy
change. esc's `examples/packs/model-seats` is this policy generalized: seats named by
role, the substance bar, mandatory logging, evidence-cited demotion with re-promotion
criteria, and the models behind the seats as a dated catalog roster rather than a rule.
The roadmap's planned `packs/` is the export step that regenerates it from the log. The seam between the
two is scope, not files: balancewheel configures the user's own agent under the home
directory, esc governs the repo, and a pack rule wins over a moderator rule in the repo
it is synced into (the README's "Working with OpenEscapement" section).

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
  vendors' own stores (Codex sessions, pi session files) are more reliable than any
  bridge you'd build — and a store the wrapper can *address* (pi's `--session <path>`)
  beats one it has to scrape (OpenCode's SQLite by directory).
- **Stealth/preview models are load-bearing quicksand.** Pin them in one config line
  you expect to change; never bake them into interfaces.
- **Vendor CLIs churn.** A profile format broke mid-build; a config table silently
  became "legacy." Wrappers isolate the blast radius to one file.
- **Model personality is a governance problem, not a prompt problem.** Politely
  asking a model to stop litigating nits does little; a moderation rule that dismisses
  non-substantive challenges *and scores them* changes the economics.
- **Blind first rounds are non-negotiable.** The one time seats see each other's
  output before writing their own, you've built an echo chamber with extra steps.
