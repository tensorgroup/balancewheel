# Handoff — 2026-11-24: seats, the panel, Orca optional, guard acting, and pack export

*Session record (point-in-time). The README, `CHANGELOG.md`, `docs/reference-implementation.md`,
and the 2026-11-22 spec own the facts. They win where they disagree with this record.*

## What changed

Five plans shipped on `main`, each built task by task with a review after every task, a final
whole-branch review, and a code-review panel. The spec is
`docs/superpowers/specs/2026-11-22-seats-panel-guard-packs-design.md`; the plans are in
`docs/superpowers/plans/2026-11-22-plan-{5..9}-*.md`.

- **Seats (plan 5).** `bw seat <id>` runs one review seat headless inside `bw sandbox`, for
  every runtime (Codex, Claude Code, pi, Kimi Code, agy). Each seat has a private home, an env
  allowlist, and grants that let it write only its home and its scratch dir. `bw seat verify`
  attacks each seat and records a pass per seat id; `bw start` checks changed seats in the
  background after the lead opens. Why: the panel must run seats itself, and a seat must be
  proven read-only and blind before it runs.
- **The panel (plan 6).** `bw panel` runs a panel's mechanics in a detached worker: a blind
  round 1, stand-ins for capped seats, one claim-only ask per dispute, `findings.json`, and
  `next.md` for the lead. A contained lead talks to bw only through request files in its view
  dir. The lead protocol (`lead/protocol/panel.md`) makes the lead convene a panel at each
  checkpoint by itself. Why: the user works only with the lead.
- **Orca optional (plan 7).** A `canvas` key: `terminal` is the default for new configs, and
  setups that use Orca today keep it. Every Orca call is gated. A passive `cli` quota source
  exists, but no CLI feeds it yet (Codex 0.164.0 prints no rate limits).
- **Guard acting (plan 8).** The guard can move from shadow to acting by itself when a six-part
  gate holds, and it steps back by itself. A judge model can lift a gray decision to allow; it
  can never make a decision weaker than the rules' decision. Any judge failure keeps the rules'
  decision. With no `guard.model`, the guard stays in shadow and says so once at lead start.
- **Pack export (plan 9).** `bw pack export --to <pack.yaml>` regenerates one marked catalog
  block and the version line in esc's `model-seats` pack, and prints the changelog bullet. It
  writes only on that explicit command.

## Live verification

- **Seat attacks.** The read-only attack passed live for the Codex (astra), Gemini/agy (neon),
  and pi (flash) seats. Each first run failed safely and found real startup problems (a Node
  shim, git, the system CA bundle, pyenv under nono), which are fixed. The Claude seats need a
  login token (below). Kimi has no live pass yet: its plan was capped, and a flag bug in its
  adapter was found and fixed.
- **A live panel.** One real panel on a scratch clone ran end to end: it waited about five
  minutes for the first seat verification, ran round 1, wrote `next.md` with five findings, and
  logged. Seat files appeared in the lead's view only as each seat finished.
- **Not run live:** the lead convening a panel by itself in a real `bw start` terminal, the
  guard's live attack and eval with a judge model, and the interactive guard check. Each needs
  you at a terminal or a judge model you have not set.

## What you need to do

1. **Deploy and re-verify once.** This branch changes every lead's containment and guard key.
   After the install clone pulls `main`, run `bw sandbox verify pi`, `bw sandbox verify kimi`,
   and `bw guard verify pi`. Until then, pi and Kimi leads do not start.
2. **Claude seats.** The keychain login does not reach a seat's private home. Run
   `claude setup-token` once and export `CLAUDE_CODE_OAUTH_TOKEN` in the shell that runs `bw`.
   Without it, the opus and fable seats are absent with a "no login" reason.
3. **Kimi seat.** After its plan cap lifts, run `bw seat verify kimi` once.
4. **Guard acting (optional).** Set `guard.model` to let the guard act. Then run
   `bw guard verify <runtime> --interactive` once at a terminal. Without a judge model, the guard
   stays in shadow, which is safe.
5. **Pack export (when you want it).** Run `bw pack export --to <esc>/examples/packs/model-seats`,
   paste the printed bullet into esc's `CHANGELOG.md`, re-sign the pack with esc, and commit both.

## Issues that cost time

- **Reviews found real holes before any code ran.** Examples: a seat write grant that could
  reach bw's verification records; a git clean filter that ran outside the sandbox; a guard rule
  bypass through `printf -v` and several other shell forms; secrets that could reach the judge;
  a panel that could stay stuck open. Each is fixed and has a regression test.
- **First live runs.** Every seat's first live attack failed on sandbox startup, not on
  containment. The fixes widened only read grants for language toolchains; secret files stay
  denied.
- **Plan drift.** Plans 6 to 9 were written against plan 5's planned interfaces; review fixes
  changed some of them. A drift scan before each later plan rewrote the plan text to the built
  names.

## Left alone on purpose

- **Guard rule limits** (documented in `lead/guard/rules.py` and the reference guide): `source`,
  IFS changes, heredoc bodies, and eval text that does not lex stay gray, so the judge decides
  them. The OS sandbox is the containment; the guard is the second layer.
- **Secret scan before the judge** is best-effort: a split, rebuilt, or encoded secret can pass.
  The judge request asks the provider to keep no data.
- **One agy seat per panel**, until a live run proves two agy seats side by side.
- **A queued panel with no running lead** stays queued until the next `bw lead` starts.

## Where to pick up

- Run the steps above, then a real day with `bw start`: watch the lead convene its first panel.
- The panel records and seat scoreboards for these reviews are in the metrics log; the dashboard
  and `bw cockpit` show them.
