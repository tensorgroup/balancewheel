# Balancewheel

**Moderated multi-model agent collaboration, with the receipts to tune it.**

In a mechanical watch, the escapement regulates — and the *balance wheel* is where
opposing forces oscillate into a steady beat. Balancewheel applies that idea to AI
coding agents: multiple frontier models from different vendors review, plan, and
debate the same work as **peer seats**, a moderator holds them to evidence, and every
dispute is **logged and scored** so seat assignments are tuned by win rates instead of
vibes.

Sibling project to [OpenEscapement](https://github.com/tensorgroup/openescapement):
esc *ships* the policy; balancewheel is the collaboration pattern that *generates and
justifies* the policy (see the model-seats pack).

## Status

**Private design repo — pre-extraction.** The reference implementation runs as
personal tooling (shell wrappers + Claude Code commands) and is being dogfooded on
real work. Code will be extracted here once the design stops moving. Until then this
repo holds the architecture, the decision log, and the extraction roadmap.

- [docs/architecture.md](docs/architecture.md) — the full design write-up
- [docs/reference-implementation.md](docs/reference-implementation.md) — the working
  example with the software and models named, the configuration that matters, and the
  verification checklist
- [docs/roadmap.md](docs/roadmap.md) — extraction criteria and plan

## The core ideas (one screen)

1. **Peer CLI seats, not API calls.** Each non-primary model runs as its vendor's own
   repo-aware CLI agent (headless, read-only, own session store). No prompt
   translation layers, no provider mixing hacks — the orchestrator shells out and the
   seat explores the repo itself.
2. **Independence before debate.** Seats review in parallel without seeing each
   other's output; only *disputed, substantive* claims get cross-examined, with the
   specific claim relayed — never the whole transcript.
3. **A moderator with a substance bar.** Challenges must state what breaks and where.
   Style and wording objections are dismissed without relay — but still logged and
   scored against the challenger.
4. **Verification is the only gate.** The moderator verifies every surviving finding
   against the actual code before anything is acted on. Seats advise; evidence decides.
5. **The scoreboard closes the loop.** Every seat invocation and every dispute
   (challenger, both proposals, winner, why) is appended to a log; a dashboard renders
   win shares and confirmed-finding rates; seat policy changes cite the numbers and
   carry re-promotion criteria.
6. **Knowledge stays markdown; indexes are derived.** All shared knowledge is
   markdown in git. Search layers (FTS, embeddings) are disposable local caches.
