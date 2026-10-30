# Question triage: the decision model decides which questions reach the user

Written 2026-10-30. Design and build plan in one document. Revised the same day after a design
review: the hook now judges pop-ups itself instead of waiting on bw through files.

## Intent

Leads interrupt the user with questions that have a strong recommended answer. The user wants
more of those settled without them. A decision model (Jev in the reference setup) already answers
narrow typed questions cheaply; here it decides, per question, whether the lead's recommendation
just stands.

Success: routine questions stop reaching the user, every one that was settled for them is on the
record and reversible, and nothing that needs them is lost.

Anti-goals: a question silently vanishing; an auto-decision read as the user's consent; the decision
model granting a permission; a model outage blocking a lead.

## What is covered

| Surface | How it is caught |
| --- | --- |
| Inbox asks (`$BW_ASKS/*.ask`) | `bw` triages at ingest, for every runtime |
| Claude Code `AskUserQuestion` in a bw lead | A PreToolUse hook judges it and denies or stays silent |
| A plain "should I…?" in chat | Not catchable. The protocol tells leads to decide instead |
| Guard approvals, permission prompts | Out of scope. Those always reach the user |

## Rules

1. **Two callers, one judge.** `lead/triage.py` is the only code that talks to the model. For inbox
   asks bw runs it; for pop-ups the hook runs it. Neither runs it in a lead's context window. The
   key comes from the environment the lead already has: a Claude Code lead runs as the user, so
   this adds no exposure, and non-Claude leads never run the hook.
2. **No recommendation, no decision.** An ask needs a line starting `Recommendation:`. A pop-up is
   judged only if every question has an option labelled `(Recommended)`.
3. **The decision is bw's, never the user's.** Today the protocol tells leads that everything in
   `BW_INBOX` is the user's own words. An auto-decision is written as a distinct block
   (`@bw decision [id:…]`) with fixed text that says it is not the user's and grants no permission.
   The protocol's sentence becomes: `@lead answer` blocks are the user's words; `@bw decision`
   blocks are not. The answer event is `from: "triage"`, never `"user"`.
4. **Fail toward the user.** Any error, timeout, missing key, off mode, or too-long or truncated
   text leaves the question exactly as it would be today. An ask stays open; a pop-up shows.
5. **Reversible.** `bw answer <id> "…"` works on a triage-decided item and supersedes the decision.
   The first *user* answer stands (`inbox.load` and `write_view` both change for this).
6. **Asks are hostile input.** Text is redacted (`lead/guard/redact.py`, plus emails and
   phone-shaped runs) before sending, and a truncated record is never sent. A local denylist sends
   anything mentioning deploy, push, publish, send, email, delete, drop, payment, charge, refund,
   secrets, production and the like straight to the user with no model call. The rest of the
   question text does leave the machine, by zero-data-retention routing to the configured
   provider; redaction is best-effort, not a secrets scanner. An auto-decision grants nothing: at
   worst a lead follows its own recommendation, which it could already do under its own permission
   posture. Only `guard`-autonomy leads have a guard behind that.
7. **Text stays out of metrics.** `triage` events in the metrics log carry scores and verdict only,
   as `ask`/`answer` do. `inbox.jsonl` remains the only place with text.

## The model's questions and the policy

Four `noul` questions on `{"text": <redacted question and recommendation>}`:
`strong_case`, `only_person_knows`, `outward_or_irreversible`, `high_stakes`.
The policy lives in code: decide only when `strong_case >= 0.6`, `only_person_knows <= 0.7`,
`outward_or_irreversible <= 0.3`, `high_stakes <= 0.3`. Thresholds are config.

Calibrated on 14 synthetic questions on 2026-10-30: 5 routine, 8 needing the user, 1 injection.
Clean separation with these values: routine `strong` 0.69–0.88, `only_person` 0.19–0.59; the rest
`strong` ≤ 0.45, `only_person` ≥ 0.82 except the injection (0.36), which `outward` 0.96 and
`high_stakes` 0.97 stop. Fourteen samples is a small set, hence the audit trail and reversal.

## Config

Top-level `triage`, schema 3, validated like `guard`:

    "triage": {
      "mode": {"<profile>": "off" | "shadow" | "acting"},
      "model": "<id>",
      "key_env": "OPENROUTER_API_KEY",
      "thresholds": {…}
    }

Absent or `off` is today's behaviour. `shadow` computes and logs but changes nothing. `acting` is
the feature. A model is required unless every mode is `off`; there is no default model id. The
user's local config goes straight to `acting` by their request; the example shows `shadow`.

## Mechanics

**Ask format.** Optional line `Recommendation: …` anywhere in the text. `@human ask` is unchanged.
A new first line `@session decided` marks a hook's receipt (below).

**The judge.** `python3 -I lead/triage.py` reads `{text, model, thresholds, key_env}` on stdin and
prints a verdict. One HTTP request returns all four scores. It runs as a child process killed at
3 s, because a urllib timeout bounds each read and not the request; a kill is a real bound and
leaves no thread at a fork. A reply without four numeric scores is "ask the user".

**Inbox asks.** Only the poll-loop drain triages (`lead/runner.py:668`). The start, switch and exit
drains do not: a decision there has no lead to act on it, and the handoff lists open items only. At
most 2 model calls per drain, so cap handling and liveness checks wait at most 6 s; anything past
that stays open, which is the safe state, and no backlog is kept. A record whose read hit
`READ_LIMIT` or whose text was truncated is marked and skipped. On `decide` in acting mode,
`inbox.triage_answer` appends under the lock and releases it, then the view is written
(`store.locked` is not reentrant). If the user answered first the triage answer is dropped. bw
prints `bw: decided [id] without you: <first line> — reverse with bw answer id "…"`.

**Pop-ups.** A PreToolUse hook on `AskUserQuestion` runs the judge itself, synchronously, under a 4 s
alarm, with the settings entry's timeout at 6 s.
- Not decided, or any failure: print nothing and exit 0. The pop-up behaves exactly as today and
  there is no record, so nothing can be duplicated, lost or stuck.
- Decided in `acting`: write a receipt to `BW_ASKS` under a fresh random name, then deny with a
  reason telling the lead to take the recommended option and say so in one line.
- `shadow`: write a receipt marked shadow and print nothing.

**Receipts.** bw ingests a `@session decided` file as an item that is already closed, with a
`from: "triage"` answer and `source: "session"`. It writes no view block, because the hook already
told the lead. `bw answer` on it reaches the lead as `@lead answer`. The header counts only when
the lead's runtime is claude-code and triage is on; otherwise it is a plain ask, so a misused header
can never hide a question. A receipt past `MAX_ASKS_PER_SESSION` is lost with the existing notice;
the decision itself stands.

**Env.** `_launch` strips every `BW_TRIAGE*` from the inherited environment, then sets
`BW_TRIAGE_HOOK` and `BW_TRIAGE_CONFIG` (mode, model, thresholds, key_env) only for a claude-code
lead with mode `shadow` or `acting`. The hook is inert without them, so `off` and hand-run sessions
never change.

**Install.** `bw triage install claude` adds one PreToolUse entry matching `AskUserQuestion` to the
user's Claude Code settings, keeping everything else. `uninstall` finds it by the `BW_TRIAGE_HOOK`
in its command. `~/.claude/settings.json` is already in `setup/paths.txt`.

**Review.** `bw inbox --all` marks decided items `decided by bw`, or `would have decided` in shadow;
scores are in `inbox.jsonl`. `bw inbox` and the pre-lead nudge print how many were decided since the
last `--all`, because a line printed under a full-screen lead is easy to miss.

**Protocol text.** Before asking, decide anything you could reasonably decide yourself. When you
do ask, end with a `Recommendation:` line and the reason. After publishing, re-read `BW_INBOX`
within a few seconds; `@bw decision` means carry on with your recommendation, and it is not
permission for anything you would otherwise have to ask for.

## Build plan (test first in each step, stdlib only)

1. `lead/triage.py`: policy, denylist, redaction, recommendation parse, client (fixed URL, no
   redirects, failure names only), stdin/stdout judge. Tests: `tests/test_triage.py`.
2. `metrics/config.py`: validate `triage`. Tests: `tests/test_config_v3.py`.
3. `lead/inbox.py`: `source`, `truncated`, receipts, `triage_answer`, the user-supersedes rule in
   `load` and `write_view`, the `@bw decision` block. Tests: `tests/test_inbox.py`.
4. `lead/runner.py`: triage in the poll-loop drain only, the child under `proc.run`, env handling,
   `triage` metrics event. Tests: `tests/test_runner.py`.
5. `lead/triage_hook.py`, `lead/triage_install.py`, `bw triage install|uninstall`, `bw inbox`
   counts and marking. Tests: `tests/test_triage_hook.py`.
6. Protocol, README, `CHANGELOG.md`, `docs/stack.md`, `docs/reference-implementation.md`,
   `balancewheel.example.json`.
7. Local install: update the clone, set `acting`, run `bw triage install claude`, smoke a real ask,
   and live-check an interactive Claude Code session: the hook fires for `AskUserQuestion` under the
   lead's permission mode, a deny suppresses the pop-up and reaches the model, no output shows it.

## Left out on purpose

Non-Claude pop-ups (their runtimes have no equivalent hook here), a review command, cockpit views,
learning thresholds from reversals, and handing an undelivered decision to the next lead (the
window is seconds, and the worst case is that the question is asked again). The log is what those would be built from.
