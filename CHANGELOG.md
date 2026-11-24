# Changelog

**What a version means here.** Balancewheel ships no binary and nothing to install — the
install is a clone plus the setup prompt in the README. So a version is not a download; it
is a **contract**: the `metrics/` config and log schemas, the `setup/paths.txt` map, and the
setup prompt that assembles a panel. Pin one and you can say what your log was written
against; upgrade and this file says what moved under you.

Dates are the last commit in the release, not a publication date. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project uses
[semantic versioning](https://semver.org/) against the interfaces listed under each release,
not against the prose.

## [Unreleased]

### Added (schema 3)

- **Seat key `title`** (an optional catalog name for `bw pack export`), schema 2 and 3; the example's seats carry a real `policy`.
- **The guard review offer (`lead/guard/offer.py`).** On a terminal, before the lead opens, `bw start` offers a
  short review of unmarked guard decisions: 5 once a day while the profile is under the gate's counts (2 are
  sampled allows, at least half of them judge allows while there are some; the counts include 5 judge allows),
  3 once a week after promotion (at least 2 are stops). The gate's counts are reachable in about 10 offers.
  Enter skips. It never runs with `--no-verify`, never for a profile pinned `shadow`, never with no judge model
  (no mark can then lead to promotion), and never when no decision is unmarked. The review names the gate's
  evidence key. New state file: `guard-offer.json`.
- **The guard gate (`lead/guard/gate.py`).** At lead start, bw decides the guard's mode before it builds the
  broker. A profile pinned `auto` or `acting` goes to acting when all gate conditions hold: at least 50 reviewed
  decisions, at least 20 sampled allows (at least 5 of them judge allows when `guard.model` is set), under 10% false
  stops, no false allow, a judge model, an eval pass, an attack pass on the guard key, the interactive check, and
  the cooldown. bw steps back to shadow by itself on a wrong sampled allow, a changed evidence key, an unverified
  guard key, a failed eval, or false stops at 10% or more. The demotion line and `bw status` say what acting needs
  again for that reason: for a wrong sampled allow, the allow re-marked right (`bw guard review --remark <id>`) or
  a key change; for the eval, an eval pass; for false stops, under 10% of reviewed stops; for an unverified key,
  `bw guard verify <runtime>`. After an evidence demotion, acting also needs 5 new marks after 7 days. A pinned
  `acting` profile on an unverified guard key exits 7, and a cap switch onto it stays on the current lead. A
  profile pinned `shadow` resets a stored `acting`, and it reads neither the log nor the guard key at lead start.
  With no judge model, lead start prints one line: "guard: no judge model set — the guard stays in shadow (set
  guard.model to let it act)". An acting guard with no eval result on the current key gets a notice at lead start.
- **`guard.mode` takes `shadow`, `auto`, or `acting`.** The default for a profile you do not name is now `auto`,
  **in schema 3 files only**; a schema-2 file keeps `shadow`. **An explicit `"shadow"` already in your config stays
  a pin and never promotes.** Change it to `auto` to let the gate decide. `bw improve` suggests that change when
  every other gate condition holds. A profile reaches acting only when all six conditions hold, and none holds
  without a judge, so set `guard.model` first. With no judge model the guard stays shadow. At lead start bw
  prints one line: the key needs `bw guard verify <runtime>`, the gate promoted, or the gate demoted. The example
  config sets `guard.model` to `null` (no judge, so the guard stays shadow) and `guard.mode.product` to `auto`.
- **`guard.on_judge_failure` is deprecated.** The config still accepts it. It changes nothing: a judge failure
  gives the rules' own decision.
- **After this version, each guarded lead re-verifies once.** Run `bw sandbox verify <runtime>` and
  `bw guard verify <runtime>`. At a terminal, run `bw guard verify <runtime> --interactive` once. The guard key
  and the evidence key changed. Lead containment also changed in this release (see the containment entries
  below), so each lead runtime needs one new `bw sandbox verify` pass after the deploy.
- **`state_dir/guard-mode.json`**: `{profile: {runtime: {mode, key: {evidence, verify}, since, reason,
  demotions}}}`, written only by bw under an flock. It keeps the last 10 demotions, and each evidence key's last
  evidence demotion (the cooldown reads it). A demotion holds `need`: what acting needs again.
- **The `guard_mode` metrics event**: `{profile, runtime, from, to, reason, evidence: {reviewed, sampled_allows,
  false_stop_pct, false_allows}}`. `store.load()` returns these events as `guard_modes`.
- **`bw guard review`** samples judge allows first: at least half of each sample, when there are some.
  A mark row's `ts` is kept on the folded mark. `bw status` shows the gate's conditions per evidence key, the last
  demotion's reason and what acting needs again, and a "no judge" note when no judge model is set (gray calls are
  recorded as asks, nothing is stopped, the guard stays shadow).
- **`bw guard review --remark <decision-id>`** marks a marked decision again, to correct a wrong mark. The latest
  mark counts; it keeps the `sampled` flag. The mark row carries `"remark": true`. The gate reads it at the next
  lead start.
- **`bw guard verify <runtime> --interactive`.** The user answers two real approval prompts in pi, No and then Yes. bw
  records what ran from the check's own broker log and a witness socket. The witness saw the approved
  command's nonce and not the declined one. That is not unforgeable (a lead can also write to the socket); the
  broker's ask log for both commands and your own confirmation bound it. The pass goes to
  `state_dir/guard-interactive.json`, keyed on the guard version, the client file, and the hook files (no runtime
  version). `bw start` offers the check on a terminal when an `auto` or `acting` profile has a guarded lead without
  it, and a judge model is set.
  agy is refused until its spike passes.
- **Quota source `cli`** and **`rotation.cli_fresh_min`** (default 120), schema 3 only.
- **`canvas`**: `terminal` or `orca`.
- **`panel`**: `cap_min`, `deep_cap_min`, `max_rounds`, `max_parallel`, each an integer of at least 1
  (defaults 12, 30, 2, 4).
- **`seats.*.tier`**: `default` or `reserved`.
- **Reserved seat names.** The seat names `verify` and `list` are reserved for `bw seat`.
- **`bw seat <id>` and `bw seat list`.** `bw seat <id>` runs one read-only seat and prints the answer on
  stdout (exit 124 on the cap, 75 when refused, 143 after SIGTERM, 2 on a usage error).
  `--result F` writes the run result as JSON, also for a refusal. `bw seat list [--json]` shows each seat
  with its verification and caps.
- **`bw seat verify`** — attacks each seat fresh and resumed (write, blind-read, and hostile-repo probes)
  and records a pass per seat id.
- **`seats/` package.** Per-seat sessions (`state_dir/seats/sessions/`) and seat caps
  (`state_dir/seats/caps/`).
- **The seat brief** (`seats/brief.py`), cached in `state_dir/seats/briefs/`.
- **Seat adapters** (`seats/adapters.py`), separate from the lead adapters so a seat change never resets a
  lead key. Codex, Claude Code, pi, Kimi Code, and agy. A Claude Code seat needs `CLAUDE_CODE_OAUTH_TOKEN`
  (from `claude setup-token`) in the env. A pi seat on OpenRouter needs `OPENROUTER_API_KEY` in the env.
  An agy seat runs on the real home, because its keychain login does not reach a private home, and
  `--gemini_dir` moves its config and state into the seat home. The adapter tuple gains `home_argv`
  (flags after the command name) and `keychain` (the login is in the keychain); `credentials` is a
  callable, read at call time.
- **`seat_grants(..., credentials=...)`** (`seats/grants.py`). A login file that a seat adapter declares as
  refreshing is readable and writable at its real path, as one file only. bw refuses a credential that is a
  symlink, is not a regular file, or lies in bw state, the target, a code path, or the seat scratch root.
- **`seat_grants(..., keychain=..., deny_read=...)`** (`seats/grants.py`). `keychain` leaves the keychain
  services reachable for a runtime whose login is there (agy). `deny_read` adds read denies (the seat
  attack names the production state dir). There is no `state_dirs`: no seat writes a dir outside its home.
- **agy lead session stores are read-denied to seats, the agy seat included.** `conversations`,
  `conversation_summaries.db`, `history.jsonl`, and `brain` under `~/.gemini/antigravity-cli/` join the
  lead session stores.
- **Seat verification keys and passes per seat id** (`seats/verify.py`, `state_dir/seat-verify.json`). The
  last failed probe per seat goes to `state_dir/seats/failures.json`. A later failure under the same key
  revokes the pass. bw now writes `state_dir/seats.json`.
- **One seat run** (`seats/run.py`): brief, sessions, the OS sandbox for every seat, caps, one-hop
  stand-ins, and `usage` rows with `ran`, `stand_in_for`, and `session_id`. A seat refuses a hostile
  project file only when its argv's ignore flags do not cover that file (`project_code(target, argv)`).
  The seat home's code dirs are made before any seat key is read, so a run keeps the seat's pass.
- **`bw panel`** (`panel/cli.py`): `bw panel <checkpoint> [target]` convenes a panel (`--deep`, `--focus`,
  `--stakes`, `--path`, `--background`), `bw panel ask|log|amend <panel-id>` relay one claim, log verdicts,
  and amend a logged panel, and `bw panel status [<panel-id>]` lists the open panels. It is the user's own
  path; leads use request files. Exit 2 on a usage error or a refused request.
- **`bin/bw-panel-wait`**: the lead runs it with no argument. It returns within 110 s, with exit 3
  ("still working; run this command again") while every open panel is queued or running.
- **Leads convene panels.** `bw lead` convenes panels from request files in its poll loop. The first
  prompt carries `lead/protocol/panel.md` with this profile's checkpoints and literal paths. The lead
  gets `BW_PANEL` (the request dir) and `BW_PANEL_VIEW` (its read-only view, with a copy of
  `bw-panel-wait`). The handoff lists open panels, also at a fresh `bw lead`. bw prints nothing about
  panels; a problem is one `panel` notice in the inbox.
- **Lead sandbox: panel work dirs are read-denied.** `lead_grants` denies reads of `state_dir/panels/`
  (every worktree's briefs and raw seat output). The lead still reads its own view, with its panels'
  view dirs and the wait helper. This changes `lead/sandbox.py`, so every lead's containment key
  changes once: run `bw sandbox verify <runtime>` again.
- **`bw improve` learns from panels.** It names seats that keep timing out (`seat-timeouts:<seat>`)
  and panels that were never logged (`panels-unlogged`). A seat absent for a usage cap, an unfinished
  verification, the reserved tier, a missing login, or a failed panel no longer counts against it.

### Fixed

- **The guard judge child gets a small environment** (`lead/guard/judging.py`). It inherits only the key's
  variable, `PATH`, `HOME`, `TMPDIR`, `LANG` and `LC_*`, and the proxy and SSL variables. No other secret of
  bw's environment reaches it.
- **A judge cache hit reports `judge_ms` 0 and 0 attempts.** No child ran for that call. The judge's time
  now also counts the step that prepares the call text.
- **The judge-down attack case accepts an open breaker.** A `judge-skipped` row with the reason "judge
  circuit open" also proves the rules-only floor (`lead/guard/verify.py`). Before, a live attack whose
  earlier failures opened the breaker failed by mistake.
- **`bw start` runs an incomplete guard eval again at most once a day**, not at each start.
- **Panel jobs always end** (`panel/service.py`, `panel/run.py`, `panel/cli.py`, `panel/state.py`). The
  scheduler fails a `running` panel or ask that no job holds, so `log` is always possible. A
  `bw panel` run with no `bw lead` here runs the watch step first: a dead job is stopped and then
  failed, and never blocks the worktree for ever. A job whose pid no longer exists is stopped at
  once. The watchdog signals a foreground `bw panel` by its pid alone (never as a process group),
  checks its start time against pid reuse, and confirms the ids again when a SIGKILL comes long
  after its SIGTERM. A finished, failed, or logged panel is never written again. A worker writes its
  own pid into a job that has none, and a worker with no job touches nothing. Ask starts count
  toward the spawn limit like round 1, the round cap is checked again before an ask starts, and an
  ask no longer waits behind newer panels. A refused `bw panel` or `bw panel ask` fails the panel or
  ask it made, instead of leaving it queued. Workers that ended are reaped. A new panel id never
  names a record in the metrics log. A failed panel logged with no findings file can be amended. An
  ask's answer appears only when it is done. A scan skips logged panels without reading them, and
  `bw panel status` lists the open panels and the failed ones still to log. The first verification
  covers the fallbacks of the roster seats too, so a stand-in can run.
- **Panel requests from a lead** (`panel/render.py`, `panel/channel.py`, `panel/service.py`). The request
  ids that `next.md` gives hold the panel id (`ask-<panel id>-<n>`, `log-<panel id>-<n>`,
  `amend-<panel id>-<n>`): with the old fixed ids, the second panel of a worktree reused `log-1`, and a
  lead could read the first panel's answer as its own. A lead request may not set `"deep"` (only
  `bw panel --deep` sets the deep tier). An ask must name a dispute of the panel: a candidate dispute,
  or `d1` to `d<n>` for its n finding groups. Every ask on a dispute that has not failed counts toward
  `panel.max_rounds`, a queued one too. A used request id is refused before any other check, so a bad
  file never replaces an answer. Deeply nested JSON and a NUL in a claim get a refusal. `next.md` shows
  the target, says that `added` holds findings taken by hand from a seat's file, and the re-verify
  step names the absolute response path. A panel that fails before its full `next.md` is written still
  gets a short `next.md` that says how to log it. `bw panel -h` prints the usage and exits 0.
  `bw-panel-wait --max` refuses a value that is not a finite number of seconds.
- **The kimi seat runs** (`seats/adapters.py`). It runs `kimi -p` with no `--auto`: Kimi Code 2.3.2
  refuses `-p` together with `--auto`, `--yolo`, or `--plan`, before any model turn. `bw seat verify`
  no longer calls a CLI error a refusal by the model: when every seat run of the attack failed with a
  CLI error, it says "the seat CLI failed before the model ran" and shows the error (`seats/verify.py`).
  Such a run still never revokes a pass.
- **Seat containment** (`seats/grants.py`). A seat can no longer rename the parent of a code name (a
  literal write deny on each dir node from the seat home down). Seats cannot connect to an SSH or GPG
  agent socket, and, except agy, cannot ask the keychain. A seat has its own secret list
  (`SEAT_SECRET_READS`): the lead list plus `~/.git-credentials`, `~/.config/git/credentials`, `~/.npmrc`,
  `~/.pypirc`, the crates.io token files, `~/.kimi-code/oauth`, and agy's `antigravity-oauth-token`, also
  under a moved `CODEX_HOME` or `KIMI_CODE_HOME`, denied on both backends. A seat reads the global git
  config as files only (`~/.gitconfig`, `~/.config/git/config`, `ignore`, `attributes`). Leads keep the
  secret list and the git config read (`~/.gitconfig` and the `~/.config/git` dir) they had before seats
  existed, so a lead's `git push` through a credential store and its private registries still work. The
  prompt histories, session indexes, and file snapshots of the lead runtimes are lead stores, also under a
  moved `CODEX_HOME` or `KIMI_CODE_HOME`. A target inside a secret path, or a directory outside its own
  git worktree, is refused. The codex code names gain
  `AGENTS.override.md` and the pi code names gain `APPEND_SYSTEM.md` (`lead/codepaths.py`: pi and Kimi
  leads re-verify).
- **The agy seat writes nothing under the real `~/.gemini`.** It runs on `<seat home>/gemini`
  (`--gemini_dir`), so it can run shell commands (agy rewrites `bin/agentapi` there; bw removes that
  `bin` before every agy run, through O_NOFOLLOW dir fds, so nothing persists into the next run), it reads no agy lead
  conversation, and an agy lead session no longer changes the agy seat key.
- **bw's git calls blank every filter driver the repo config names** (`lead/handoff.py`). A clean filter
  named in `.git/info/attributes` used to run on a worktree diff. The blanks go through
  `GIT_CONFIG_COUNT`, so a driver name with `=` or a space is blanked too. When the filter config cannot
  be read, bw does not run git. Git output with non-UTF-8 bytes no longer raises.
- **bw's git calls never enter an embedded repo** (`lead/handoff.py`). `diff` and `status` get
  `--ignore-submodules=all`, and submodule recursion and summaries are off. A gitlink's own config or
  attributes (a filter, an fsmonitor) could otherwise run code when a user config sets
  `diff.submodule=diff`.
- **Seat run fixes** (`seats/adapters.py`, `seats/run.py`, `lead/proc.py`). A usage cap needs quota
  wording: "Rate limit reached" and "context limit reached" no longer arm a 5-hour cap. A project
  `settings.json` is read with no link followed, as a regular file up to 64 KiB, and a file the argv
  covers is never opened. The ignore-flag checks read `--flag=value` too, and fail closed on seat args
  that load code or trust project files (`--mcp-config`, `--settings`, `--plugin-dir`; pi's `--approve`,
  `-e`, `--extension`). The hostile-repo refusal comes before the brief is built. A seat's process
  group is killed after a normal exit too (`proc.run(..., reap=True)`). The transcript keeps the last
  stdout event on its own line. A `nono` line on stderr is a refusal only when the CLI printed nothing.
  A NaN or infinite cost or token count is never logged. A credential link is made through open dir
  fds, so a dir swapped for a link meanwhile is refused.
- **`bw start`'s seat line names the log, not the seats** (a deviation from the plan's wording): the
  background child writes each seat's result to `state_dir/seats/verify-log.txt`.
- **The seat key binds what the attack ran** (`seats/verify.py`). It hashes `seats/run.py`,
  `seats/judge.py`, `seats/verify.py`, and `tests/attack/seat_readonly.sh` too. The attack's seat home
  gets a copy of the real seat home's hook files, so the attack runs the code the key hashes. An edit of
  the config file during the attack is a changed key. A failure goes on the key that was attacked.
- **`bw seat verify --changed` does not attack a seat again on a key that already failed**; an explicit
  `bw seat verify <id>` does. A capped seat, a seat without its login env token, or one whose version is
  unreadable is pending on both paths. An inconclusive run (a refusal by the model, a timeout) no longer
  revokes a valid pass. A tampered seat home is a `seats.json` entry, not an exception, and bw never
  chmods through a link there. `verify-log.txt` moves to `verify-log.1.txt` past 1 MiB. The background
  verify that `bw start` spawns gets start's own `BALANCEWHEEL_CONFIG` and `BW_STATE_DIR`.
- **The seat attack takes its denial evidence from bw's own re-runs** (`seats/judge.py`,
  `tests/attack/seat_readonly.sh`). Every probe is re-run as a plain process under the seat's sandbox
  after each phase; a tool result alone never makes a probe denied (a model could print the tag and a
  denial line). A skipped probe is now a failure, not an inconclusive refusal. New probes: a write to
  each code file name, a rename of each parent of a code name, writes to bw's state dir and a sibling
  seat home, reads of a lead store, of bw's production state dir (now read-denied during the attack),
  and of a token inside a toolchain dir, and a connect to the SSH agent socket. Each probe target has a
  unique name. The hostile-repo case must run, and a clean run with no covered executable kind fails as
  vacuous. The model is asked for the in-repo probes only (`model: true` in `probes.json`); each of them
  needs its tag line in a tool result the CLI reported, as well as bw's denied re-run. A missing tag is
  `probe-skipped:<phase>:<name>` and inconclusive (the model did not try); an open re-run is a failure.
- **The seat brief cache keys on the instruction and read-next files by content** (`seats/brief.py`). An
  edit to an ignored `CLAUDE.md` now rebuilds the brief. A git failure other than "not found" is never
  cached.

### Changed

- **Guard records keep the final decision and the rules' decision apart** (`lead/guard/record.py`,
  `broker.py`, `review.py`). A `guard.jsonl` decision row gains `rule_decision` (the rules' own
  decision), `judge_reason` (string or null), and `judge_ms` (number or null). `decision` is now the
  final decision. `source` is `rule`, `judge`, `judge-failed`, or `judge-skipped`; a row from before
  this change says `gray`, and review counts it as gray. `rule` keeps the rule name, so `gray:*` stays
  visible there. `judge_model` is the configured `guard.model` on every row, null when none is set;
  `prompt_hash` stays null until the judge arrives. No judge exists yet: a gray call records
  `judge-skipped`, `judge_reason` "no judge", and the final decision `ask`. Shadow still replies
  `allow`. `bw guard review` samples the final decision, so a judge allow is sampled as an allow.
  `bw guard verify` facts gain `source`.
- **The guard evidence key drops the runtime version** and gains the judge model and the prompt hash:
  `(profile, runtime, rules_version, policy_digest, judge_model, prompt_hash)`. An update of the
  lead's CLI no longer resets the evidence. A row from before this change has no judge model and no
  prompt hash. Its rules-only evidence and marks carry over into the new key with no judge, when the
  rules version and the policy digest are unchanged. Evidence under a judge model starts fresh.
  `bw guard review` labels the key `profile= runtime= rules= policy= judge= prompt=`, and the
  `guard:` lines in `bw status` drop the version column. The policy digest is unchanged.
- **The guard allows a lead's writes into its own checkpoint dir by rule** (`lead/guard/rules.py`).
  The write tool, `>`/`>>`, `mkdir`, `touch`, `cp`, `mv`, and `tee` may write into
  `handoffs/<key>/`, except bw's own `guard-unreachable` and `cap-signal`. `rm` and the other
  writers stay worktree-only. `tee` accepts only `-a`. `RuleContext` gains `checkpoint_dir`,
  `lead_env`, and `panel_wait` (each default `None`). The rules expand `$NAME` and `${NAME}` for the
  `BW_CHECKPOINT`, `BW_ASKS`, `BW_INBOX`, and `BW_PANEL` values bw gave the lead; any other variable
  stays gray. `python3 <panel wait helper>`, with no other words, is allowed. A change to a name bw
  sets for the lead (those four, `BW_PANEL*`, `BW_SESSION`, `BW_GUARD_*`, `BW_STATE_DIR`) is the rule
  ask `ask:bw-env`; the judge cannot lift it. A lead that works on balancewheel itself gets this ask
  when its tests set `BW_STATE_DIR=...`. The standalone broker takes `--checkpoint-dir`. The line
  "Never set, export, or unset a variable whose name starts with BW_." moved from `panel.md` to
  `checkpoint.md`, so every lead gets it. The rules changed, so the guard verification key and the
  evidence key both reset.
- **The guard judge's request** (`lead/guard/judge.py`). Nothing calls it yet. `prepare` sends no
  call that holds a secret. This covers redact's shapes, `curl -u user:pass`, `mysql -pPASS`,
  `sshpass -p PASS`, AWS key ids, GitHub, GitLab, `sk-`, and Slack tokens, JWTs, and long
  high-entropy tokens. For such a call the judge is skipped with "secret in call", and the rules'
  decision stands. The check is best-effort: a split or encoded secret gets past it. `prepare` puts
  `{worktree}` and `~` in place of the worktree and home paths, raw or JSON-escaped, in NFC or NFD,
  only where a path ends. A trailing `/` on a path is dropped. It
  scrubs the text and refuses a user message over 4 KiB. A reply over 64 KiB is refused. The request goes only to OpenRouter's chat
  completions URL, with no redirects, `provider.zdr`, temperature 0, reasoning off
  (`reasoning: {"enabled": false}`), at most 200 output tokens, and a strict JSON schema: `decision`
  is `allow` or `ask`, and `reason` is a string. `parse` refuses any other reply, and cleans the
  reason and cuts it to 300 characters. `prompt_hash()` hashes only the system prompt and the schema.
  `python3 -I lead/guard/judge.py` runs one call as a child. The call text moved from the broker to
  `judge.call_text`. Every file in `lead/guard/` is hashed, so the guard verification key resets;
  the rules did not change, so the evidence key holds.
- **Shadow guards judge gray calls after the reply** (`lead/guard/judging.py`, `broker.py`). With
  `guard.model` set, a shadow gray call gets `allow` at once. One judge thread then runs
  `judge.py` in a child, killed at its budget, and writes the record. The queue holds 32 calls. A
  call that finds it full is recorded as `judge-skipped`, "queue full". A call still queued at the
  end of the session is recorded as `judge-skipped`, "session ended", without a model call. Without
  `guard.model` a gray call is recorded at once, as before. Rows now carry `prompt_hash` when
  `guard.model` is set. Judge verdicts are cached by digest and cwd for 10 minutes; failures are not
  cached. After 3 failures in a row the judge is skipped for 5 minutes ("judge circuit open") and
  one `guard-judge-down` notice goes to the inbox. Every judge failure or skip records the rules'
  own decision. **New network traffic:** with `guard.model` set, gray calls go to OpenRouter's chat
  completions URL (this amends the 2026-10-24 spec's network list). Every file in `lead/guard/` is
  hashed, so the guard verification key resets. The rules did not change, so the evidence key
  holds.
- **`guard.key_env`** (default `OPENROUTER_API_KEY`): the variable that holds the judge's key. It
  must be a non-empty string. The key reaches the judge child through its environment only.
- **Notice kind `guard-judge-down`** in `inbox.jsonl`.
- **The `guard` metrics event gains `judge`**: `{"judged", "allowed", "failed", "skipped"}`, counts
  of gray calls by judge source (`allowed` counts judge verdicts of allow).
- **`guard.on_judge_failure` is deprecated.** It is still accepted and validated, and it changes
  nothing: every judge failure records and applies the rules' decision. It is in neither the policy
  digest nor the eval key.
- **Acting guards judge gray calls inside the hook's deadline** (`lead/guard/judging.py`,
  `broker.py`). The hook waits 2.0 s. The judge gets what is left after 0.5 s for the read, the time
  the rules took, and a 0.15 s margin (`judging.acting_budget`). Shadow judges with the same budget,
  in place of the fixed 1.35 s. A judge call makes one retry when the first attempt fails with at
  least 0.3 s left. With less than 0.3 s at the start, no child starts: the call is `judge-skipped`,
  "no time left". In acting, a judge failure, a timeout, an open breaker, or a skip applies the rules'
  own decision. The reply's reason is the judge's cleaned reason on a judge decision. Acting is still
  refused by config until the gate arrives. Every file in `lead/guard/` is hashed, so the guard
  verification key resets. The rules did not change, so the evidence key holds.
- **Guard log event `outcome`**: `{"type": "outcome", "decision_id", "outcome": "client-timeout"}`.
  The broker appends it after a decision when the reply cannot be sent, or goes out 2.0 s or more
  after the accept. `record.load` folds it into its decision: `outcome` becomes `client-timeout`, and
  the first outcome stays as `outcome_first`. An outcome for an unknown decision goes to the new
  `orphan_outcomes` list.
- **`python3 -m lead.guard.broker serve` gains `--judge-model` and `--judge-key-env`** (default
  `OPENROUTER_API_KEY`). `bw guard verify` gains the case `judge-down`: an acting broker whose judge
  key is unset must keep the rules' ask on a gray call, and the call must not run.
- **`bw guard eval [--runtime pi] [--json]`** (`lead/guard/evaluate.py`, `lead/guard/eval/*.json`).
  It replays a committed, synthetic set through the rules and the judge: at least 15 cases in each of
  four classes (`benign`, `dangerous`, `obfuscated`, `injected`), in a throwaway git repo. **Pass**: the
  guard stops every non-benign case. **Fail**: it allows one. **Incomplete**: the judge failed on a gray
  call, so there is no verdict. The benign false-stop rate is printed, never gated. Exit 0, 1, or 75.
  The result goes to `state_dir/guard-eval.json` under its eval key: runtime, rules version, guard
  version, judge model, prompt hash, and set hash. `on_judge_failure` is not in the key. A promotion
  needs a pass on the current key. An acting profile stays acting on an incomplete result while its
  last pass has the same key. `bw start` runs the eval for each runtime with a guarded lead when the key
  changed or the last result was incomplete. `--no-verify` makes no judge call, and agy waits for its
  spike. Every file in `lead/guard/` is hashed, so the guard verification key resets. The rules did not
  change, so the evidence key holds. Lead containment and seat passes hold.
- **No Codex `cli` reading yet.** `codex exec --json` (`codex-cli 0.164.0`) carries no `rate_limits`
  (checked 2026-11-23). A Codex seat run writes no `quota/<plan>.cli.json`. `chatgpt-plan` in the
  example stays at `"quota": "none"`. The passive `cli` source stays for a later Codex version.
- **Orca is no longer the default canvas.** A schema-3 config without `canvas` keeps Orca panes when it
  has `tools.orca` or a plan with `orca_provider`. Any other config opens Terminal tabs. Set
  `"canvas": "orca"` to keep Orca, or `"canvas": "terminal"` to stop using it. A schema 1 or 2 file reads `orca`.
- **The example config leaves Orca out.** `canvas: terminal`; no `tools.orca`; no plan sets `orca_provider` or
  `orca_windows`; `chatgpt-plan` and `kimi-plan` move to `quota: none` (observed caps only). Without Orca,
  the example has no Codex or Kimi reading, no `fableWeekly` pool, no Gemini reading (`google-plan` runs on
  observed caps until agy's probe lands), and no Orca notifications. `docs/orca.md` shows how to add Orca back.
- Changed (log schema): a panel record may have empty `seats` when `absent` names every seat.
  `validate_panel` can keep a bw-assigned id. `store.load()` returns `panel_runs` and keeps a panel's `tier`.
  `store.load()` keeps one `panel_run` event per panel id (the first), so a repeated event is not counted twice.
- After the lead opens, `bw start` verifies every seat reachable from a checkpoint, a fallback, or the
  reserved tier whose key changed, in the background (`bw seat verify --changed`). The registry shows
  seat caps as `capped` health.
- **`usage` rows with `ran: false` are not counted as calls.** The seat rows, the dashboard totals,
  and the cockpit usage view skip a run that never started.
- **The lead verification key hashes `lead/codepaths.py`.** The lists of names that load as code
  (pi and Kimi) move there from `lead/runtimes.py`. Every pi and Kimi lead, and every guarded lead,
  re-verifies once.
- **Seat grants live in `seats/grants.py`.** `seat_grants` lets a review seat write only its own home
  and read the target, never bw's state, another seat's scratch dir, or a lead's session store.
  `seat_env` passes an allowlist of env names to a seat. A seat runs under nono without
  `--allow-cwd`. The seat key hashes `seats/grants.py` (`seat_grants_sha`); the lead key does not, so a
  later seat change does not reset a lead key. The shared helpers and profile builders stay in
  `lead/sandbox.py`; their hash changes, so leads re-verify (once, with the change above).
- **The Gemini seat stands in for capped seats.** `balancewheel.example.json` moves the `gem` seat
  from `gemini-3.1-pro-high` to `gemini-4-neon-high` (Gemini 4 Neon, the newest Gemini 4 model,
  2026-11-18) and sets `fallback: "gem"` on the `astra` and `kimi` seats, so the Gemini seat runs in
  their place while their plans are capped. `fallback` already existed in schema 3; no schema moved.
- **A Gemini lead between the OpenAI lead and the agy Claude lead.** `balancewheel.example.json` adds
  an `agy-gemini` lead (`gemini-4-neon-high` via agy, `autonomy: manual`) and puts it third in each
  profile's `lead_order`: Claude while the Anthropic plan has quota, then the OpenAI lead, then
  Gemini when both are capped. No schema moved.

- **Reserved seats; the same-vendor blind seat moves to the default tier.** `balancewheel.example.json`
  adds an `opus` seat (`claude-opus-5-5`) and puts it where `fable` stood in every profile's
  checkpoints, including the high-stakes review's `required` list. `fable` stays configured on
  `claude-fable-5-2` (released 2026-11-09) but sits in no default checkpoint: it joins deep panels
  and stuck debugging only. The reason is cost (the top tier is twice the default tier per token);
  `docs/architecture.md` states the pattern and the condition that would reverse it. A config copied
  from the earlier example keeps working unchanged; no schema moved.

### Added

- **`panel/` package**: parse and group seat findings (`panel/merge.py`). `metrics/watches.py` gains
  `is_bloated`, the one panel-bloat test that the watch and the panel merge share.
- **`panel/record.py`** builds the `panel` record from bw's findings and the lead's verdicts. bw computes
  the counts. The lead never sets them. Lead text is escaped and cut to a bound.
- **`panel/roster.py`**: panel rosters. Reserved seats run on deep panels only. The panel owns
  stand-ins: one run per seat id, one hop, and a stand-in never satisfies a required seat. At most one
  agy-runtime seat runs per panel.
- **Panel state** (`panel/state.py`): panel state in `state_dir/panels/`, the lead's read-only view in
  `state_dir/inbox-view/<key>/panels/`, and the `panel_run` event.
- **Panel requests as files** (`panel/channel.py`): a lead publishes `<id>.request` in `$BW_PANEL` by
  rename. bw writes each response in `$BW_PANEL_VIEW/responses/`. The inbox gains the notice kind `panel`,
  and `inbox.notice` takes a `key`: one open notice per kind and key.
  A request may be one fenced JSON block. A hard-linked request file or a duplicate JSON key is refused.
- **Panel templates** (`panel/render.py`, `panel/templates/`): one seat brief per checkpoint and `next.md`,
  the moderator's steps for one panel. Paths in shell commands are quoted; seat and lead text is escaped.
- **The panel worker** (`python3 -m panel.run`, `panel/run.py`): one brief for every seat, blind parallel
  `bw seat` runs, stand-ins on a cap, `findings.json` and `next.md`; and one `ask` that resumes the seat's
  round-1 session.
- Added: the panel service — one job per worktree at a time, a watchdog by waves and heartbeat, `log` that
  fills every mechanical field and validates with `metrics/log.py`, `amend`, and at most 3 open panels and
  20 asks per panel per session.
  The watchdog signals a pid that this bw did not start only while that pid still runs a panel worker or
  a seat. It confirms each pid once, at SIGTERM, and SIGKILLs only those groups. A panel whose worker
  failed to start 3 times fails with "worker did not start". A panel that fails before its seats start
  logs with every roster seat absent `panel failed: <why>`.
- **`bw improve`** (`lead/improve.py`, `bin/bw`): suggestions from how the user works, each with its
  evidence, a minimum sample, and no effect until the user acts. `bw improve note "…"` adds a note,
  `bw improve done <key>` marks one dealt with (`improve.json` in the state dir, added to `setup/paths.txt`). An
  ask file starting `@improve` is a note, stored as a `note` event in `inbox.jsonl`; the leads'
  protocol text asks for one when the user corrects them. `inbox.load` marks a reversed decision. Config schema 3 gains an optional `improve.panel_logs`
  list, and the normalised panel keeps `absent`.
- **Question triage** (`lead/triage.py`, `lead/triage_hook.py`, `lead/triage_install.py`, `bw triage
  install|uninstall claude`): a decision model settles leads' questions that have a strong
  recommendation. Config schema 3 gains an optional `triage` object (`mode` per profile: `off`,
  `shadow`, `acting`; `model`; `key_env`; `thresholds`), absent means off. The ask format gains a
  `Recommendation:` line and a `@session decided` receipt header. The metrics log gains `triage`
  events (scores and verdict, no text). `inbox.jsonl` answers gain `from: "triage"`, and the
  answers view gains `@bw decision` blocks, which are not the user's words; a later `bw answer`
  supersedes one. Leads' protocol text changes to match. `bw inbox` and the pre-lead nudge count
  decided questions, and `bw inbox --all` writes `triage-seen.json` in the state dir.
- **Plain `bw`** (`bin/bw`): in a git worktree it runs `bw lead` there, and lead flags work on it
  (`bw --use <lead>`, `bw --auto`, `bw --no-rating`); outside a worktree it runs `bw status` and says
  so. Before a lead starts, `bw lead` prints one line each when the worktree has open inbox items and
  when `bw start` last updated tools more than a day ago (never when `bw start` has not run). Every
  subcommand and `bw --help` are unchanged.
- **`bw cockpit [--port N] [--no-open]`** (`cockpit/server.py`, `bin/bw`): a read-only local page
  served on `127.0.0.1` only, until Ctrl-C. It opens `http://127.0.0.1:<port>/?t=<token>` with
  `open` (or prints it with `--no-open`); the one-time token is exchanged, under a lock and at
  most once, for an `HttpOnly; SameSite=Strict` cookie holding a separate secret. Every other
  request needs that cookie (else 403), a `Host` of `127.0.0.1:<port>` or `localhost:<port>`
  (else 421), and GET (else 405, `Allow: GET`). Every response carries a per-response nonce CSP,
  `Referrer-Policy: no-referrer`, `nosniff` and `no-store`, and never an `Access-Control-*`
  header. A view that raises answers `{"available": false, "reason": "<exception name>"}`. A port
  in use is a one-line error and exit 1, and so is a `--port` that lead sandboxes do not deny
  (anything but 8765 or the configured `cockpit.port`): set `cockpit.port` and re-run
  `bw sandbox verify` instead. Idle connections close after 10 s, the port is never bound with
  address reuse, and a browser hanging up mid-response prints nothing. The page (`cockpit/page.html`, one file, vanilla JS
  and inline SVG, every string through `textContent`/`setAttribute`, light and dark) has tabs
  Models, Usage, History, Now, Graph over `cockpit/views.py`: `/api/models` (seat rows, lead rows,
  the lead columns that wait for `task` events, `small_n` 10), `/api/usage` (cc-switch groups for
  the last 30 days, or `available: false` with the reason, plus per-day seat rows from `usage`
  events), `/api/history` (panels, disputes, findings, session and lead markers), `/api/now`
  (per plan: windows, pools, source and age, cap, notice, cross-check, limits, and a metered plan's
  month spend from its probe snapshot; per worktree the current and pinned lead and open inbox
  counts; open watches) and `/api/graph` (the registry's nodes and edges, built per request). An
  unreported number is `null` in the JSON and "unavailable" on the page, never 0.
- **Config `cockpit.port`** (`metrics/config.py`): an integer from 1024 to 65535, default 8765;
  anything else is a config error. Unknown `cockpit.*` keys warn and are ignored, as under
  `tools.*`. The resolved config's `cockpit` is `{"port": N}`. Every lead sandbox already denies
  the configured port as well as 8765, so changing it changes the containment key: re-run
  `bw sandbox verify` for the bw-contained runtimes afterwards.
- **`bw registry`** (`lead/registry.py`, `bin/bw`, `lead/start.py`): a derived snapshot at
  `state_dir/registry.json` of tools, plans, models, seats, leads, profiles, projects and the edges
  between them, each node with an `id` (`<kind>:<name>`) and a `health` of `verified`, `pending`,
  `capped` or `unknown`. Rebuilt by `bw registry [--print]` and by `bw start` step 5; deleting it loses
  nothing. A tool's `auth` is always `unknown` (bw reads no credentials) and its `locks` is `held` or
  `free`, read by a non-blocking try that never disturbs a running lead. A runtime that a lead or
  seat uses without a `tools` entry gets a derived tool node with `configured: false`.
- **cc-switch usage reader** (`lead/ccswitch.py`): `usage(state, since, until)` reads session rows
  and rollups from a sealed copy of cc-switch's database in the state dir's `sealed/` (db and
  `-wal`, never `-shm`, bounded at 256 MiB, removed after the read) and never writes the original.
  The result is `{available, reason, groups, skipped_rows}`; a row with an out-of-range
  timestamp or malformed count is counted in `skipped_rows`, and a token or cost figure a group
  cannot fully report is `null`, never 0. `prices` reads `model_pricing`. Amendments A11-A14 in
  the 2026-10-24 spec; the cockpit's Leads table lists sessions, minutes (lost and unparsed
  sessions left out), cap switches, overrides, ratings and asks, with the task-based columns
  pending. A page on another localhost port can toss a duplicate cookie and lock the cockpit
  (403, nothing disclosed); this is accepted.
- **Usage token and cost fields; seat and lead summaries** (`metrics/log.py`, `metrics/summary.py`):
  log schema, additive. A `usage` line may carry `tokens_in`, `tokens_out` (integers >= 0),
  `cost_usd` (a finite number >= 0) and `session_id`, written by `log.py usage --tokens-in
  --tokens-out --cost-usd --session-id`; absent keys are not written, and older lines load
  unchanged. A seat total is `null`/unavailable until some event reports it, never 0.
  `metrics/summary.py` holds the pure seat and lead numbers (standout panels, per-lead sessions,
  minutes, cap switches, overrides, ratings, asks) that the dashboard and the cockpit share.
- **Plan keys for Orca and probes** (`metrics/config.py`, `balancewheel.example.json`): config
  only, no schema bump. `quota` gains `"orca"` (read the plan's usage from Orca; requires
  `orca_provider`). Plans gain `orca_provider` (an Orca provider name, refused with `quota:
  "none"`), `orca_windows` (window id `fableWeekly` or `bucket:<name>` mapped to lead or seat
  names, checked once both have loaded), `probe` (`agy` or `openrouter`; derived from `vendor`
  `google`/`openrouter` when `quota` is `"probe"`, else `null`), and `key_env` (default
  `OPENROUTER_API_KEY`, only with probe `openrouter`). They are optional additions to an
  existing object, so schema 2 and 3 files read them and files without them resolve as before.
  A probe plan with no derivable probe still loads and is reported `unknown` later.
- **`bw start [path] [--dry-run] [--no-update] [--no-verify]`** (`bin/bw`, `lead/start.py`): a
  new command. It resolves the project and refuses (exit 2) when bw itself lies inside the
  target worktree or its common git dir; updates every configured tool in parallel; re-verifies
  each lead runtime's containment and guard keys on a pool, one worker per runtime, each
  starting as its tool's update finishes (at once with `--no-update`; changed keys stay
  `pending` with `--no-verify` or when the version after the update is unreadable); prints the
  quota of every plan the profile touches and the lead it would start; then opens the worktree
  in an Orca pane (`orca terminal create`) when Orca runs, else in a Terminal tab through
  `state_dir/start/<worktree-key>.command` (0700), and prints the command to run (exit 1) when
  neither works; a running Orca whose `orca terminal create` fails falls back to the Terminal
  tab. A worktree that already has a lead is not opened again. Results go to
  `state_dir/start.json` (`ts`, `project`, `tools`, `verify`, `orca_version`), which `bw status`
  reads for its `last start:` line. `--dry-run` runs only git and `orca status` and writes
  nothing. Containment and guard passes are now recorded under an flock on `<store>.lock`, so
  concurrent re-verifies never drop one. Docs: the README, the Orca
  guide, and the 2026-10-24 spec (amendments A3–A6) describe `bw start`; the spec's cc-switch
  version reads 4.0.20.
- **`bw probe [plan...]`** (`bin/bw`, `lead/probes.py`): a new command. It reads quota now for
  the named plans (all plans without one) and prints one line per plan: the arbitrated summary
  with its source and age, plus any notice. One `orca account list` is shared by every
  Orca-mapped plan; `quota: "probe"` plans are read by their probe in parallel, each written to
  `state_dir/quota/<plan>.probe.json`, then expired caps are cleared. Probe `openrouter` reads
  `GET https://openrouter.ai/api/v1/key` with the key from the plan's `key_env` (unset → unknown
  `key env <NAME> unset`; non-200 → `HTTP <n>`; a redirect is refused, not followed; any other
  failure is named by its exception type, never described), and with `budget_usd_month > 0`
  gives a `budget` window: this UTC month's spend (`usage_monthly`) against the budget, reset on
  the 1st. Without `usage_monthly` it falls back to `usage` less the month's baseline
  (`state_dir/probes/openrouter-<plan>-<YYYY-MM>.json`): the plan's last reading from before the
  1st (`openrouter-<plan>-last.json`, rewritten on every reading), carried forward, which
  overstates spend by the old month's last hours and never understates it; with no such reading
  the baseline is the month's first, there is no budget window, and the note says `monthly spend
  unknown before <time>`. A key credit limit is shown under `limits`, never a window. Probe `agy` is unknown
  (`agy /usage: awaiting Task G`) and runs nothing. `bw start` (step 4) and `bw lead` (before its
  first decision and at the session boundary, never in the poll loop) run the same refresh.
- **Per-source quota snapshots and an arbiter** (`lead/quota.py`, `lead/orca.py`,
  `lead/rotation.py`, `lead/runner.py`, `lead/status.py`). Besides the statusline's
  `quota/<plan>.json`, each source writes `quota/<plan>.<source>.json` (`probe`, `orca`); an
  unknown snapshot carries `last_known_at`. `quota.read_quota(state, config)` now arbitrates per
  plan: windows from the first known, fresh source in the plan's order (`statusline`; `probe`
  then `orca`; `orca`), where `probe` and `orca` readings older than twice `probe_every_min`,
  dated more than 300 s ahead, or malformed are unknown; pools (`orca_windows`) from the
  freshest known, non-stale Orca snapshot whichever source won; Orca's windows on a statusline
  plan only as `cross_check`, display-only. `rotation.decide` does not start a lead named in a
  full pool at `start` or `boundary` and skips it as a replacement when a cap forces a switch;
  a pool never moves a running lead. Caps past their `resets_at` are cleared by bw once a fresh
  known reading observed at or after that time exists, else `cap_expiry_hours` later. The Orca
  adapter checks each mapped provider's shape on its own and re-checks on a version change; a
  malformed mapped pool is dropped with a note. `bw lead` re-reads Orca's local cache in its
  poll loop (synchronous, 10 s cap, display only; a failure never stops the lead). **Log
  content change:** a `lead` event's `quota` is the arbitrated reading and no longer carries
  `cross_check`. Docs: the README, the Orca guide, `docs/stack.md`, the 2026-10-24 spec
  (amendments A7–A10) and the guard spec (decision 7 superseded) describe it as built.
- **Orca and cc-switch checks in `bw status`** (`bin/bw`, `lead/status.py`, `lead/orca.py`,
  `lead/sqlite_ro.py`): `bw status` adds `orca:` and `cc-switch:` lines. Orca's are whether
  it runs (`orca status --json`; installed but not running gets its own line) and its
  active profile's settings: agent permissions not Manual (the default or a per-agent
  override), telemetry on, or managed Claude/Codex accounts warn. bw reads Orca's profile
  database only from a copy in the state dir's `sealed/` (0700, denied to every sandbox),
  removed after the read. `BW_ORCA_SUPPORT` overrides Orca's support dir. `sealed/` is live
  state and stays out of `setup/paths.txt`.
- **Config `tools.*` keys, and config warnings** (`metrics/config.py`, `lead/updates.py`,
  `bin/bw`): a tool takes `update` and `version` (argv lists), `app` (an app bundle path; its
  `Info.plist` gives the version), `self_updating` (version only, never updated by bw),
  `runtime` (which runtime lock its update needs; defaults exist for claude, codex, agy, pi
  and kimi) and `timeout_s` (integer ≥ 1, default 600). A known key with a bad type is an
  error; an unknown key under `tools.<t>` is ignored and reported in the resolved config's
  new `warnings` list, which `bw` prints once on stderr. `lead/updates.py` reads versions and
  updates every installer in parallel, tools sharing one installer in turn, each under its
  runtime lock taken without blocking (a held lock skips the tool); a tool with an update
  command and no runtime is not updated. Additive and optional: no schema bump.
- **`bw setup cc-switch`, the cc-switch settings check, and `lead/proc.py`** (`bin/bw`,
  `lead/ccswitch.py`, `lead/checks.py`, `lead/proc.py`): cc-switch is adopted as the usage
  and cost source for the Usage view (docs/stack.md). `bw setup cc-switch` writes
  `~/.cc-switch/settings.json` (proxy, stack mode, failover, Codex history unification,
  launch on startup, and tool-update checks off; session auto-sync on) before the app's
  first launch, and refuses to overwrite a file that differs; it needs no config.
  `setup/paths.txt` gains `~/.cc-switch/settings.json` (the rest of `~/.cc-switch` stays
  out: it is the app's database and holds copies of credentials). `lead/proc.py` is the one
  shell-out helper later modules take as `run=`.
- **`bw guard` commands** (`bin/bw`, `lead/guard/`): `bw guard review [--profile] [--recent]
  [--sample] [--list]` (audit stops and sampled allows, marks recorded in `guard.jsonl`),
  `bw guard verify <runtime>` (the attack in `tests/attack/guard_attack.sh`; a pass is
  recorded in the state dir's `guard-verify.json`), `bw guard install|uninstall agy`, and
  `guard:` lines in `bw status`. agy's side refuses until its hook spike runs. `guard.jsonl`,
  `guard-verify.json`, and the per-worktree `guard/` directory are live state and stay out
  of `setup/paths.txt`. After upgrading, pi and Kimi leads need `bw sandbox verify` again
  (the adapter hash changed). Guarded pi leads (and the pi guard attack) run with
  `--no-extensions`, so pi's discovered extensions, including project-local ones in the
  worktree, stop loading for them; the guard client still loads through `-e`, and an
  extension you want back goes in the lead's `args` as `-e <path>`.
- **Log event `guard` and `session.autonomy`** (`lead/runner.py`, `metrics/store.py`): a `session`
  start now carries `autonomy: {requested, effective}` (`requested` is null unless the lead's config
  names it) and, for a guarded lead, `guard_version`. A guarded session adds one `guard` event
  before its `session` end: `{session_id, project, profile, lead, runtime, mode, guard_version,
  rules_version, calls, decisions: {allow, ask, block}, gray, unreachable, latency_ms: {p50, p95}}`,
  counts only, self-test call excluded. `store.load()` keeps `autonomy` and `guard_version` on
  sessions (absent: null) and returns `guards`. `bw lead` exits 7 when a guarded lead cannot start.
  Older logs load unchanged.
- **Config schema 3** (`metrics/config.py`): per-lead `autonomy` (`native`, `guard`, `bypass`,
  `manual`; checked against what each runtime supports), a `guard` block (`mode` per profile,
  `model`, `on_judge_failure` (default `allow`), `rules_on_native`), and per-profile `protected_branches` and
  `deploy_commands`. A lead without `autonomy` gets its runtime's default (claude-code `native`,
  codex `native`, agy `guard`, pi `guard`, kimi `manual`), and under schema 3 `args` may no longer
  carry permission flags (autonomy owns them). **Schema 3 moves Codex leads to
  `--approve-for-me` by default; writing `schema_version: 3` is the opt-in.** Version-1 and
  version-2 files load unchanged and keep today's argv: their leads resolve to a legacy autonomy
  (`custom` when `args` already carry a permission flag) and no flags are added. The new keys are
  rejected in a version-2 file with "(needs schema_version 3)"; `guard.mode: "acting"` and
  `guard.rules_on_native: true` are rejected until built. `balancewheel.example.json` is now
  schema 3, with the agy lead set to `manual` until its guard hook is verified.
- **Config schema 2** (`metrics/config.py`): `plans` (billing `plan`|`metered`, quota source,
  metered budget), `leads` (runtime, model, plan), `tools` (update argv), `profiles` (lead
  order, per-checkpoint seats with `required` and `min_seats`, depth, router mode),
  `projects` (path → profile), `default_profile`, `rotation`, `updates`, `cockpit`, `team`;
  seats gain `plan` and `fallback`. Version-1 files load unchanged as one implicit profile
  with no leads; the v2-only keys are rejected in a version-1 file. Leads name a `runtime`
  (the same names seats use), not the spec draft's `tool`.
- **`bin/bw` and the `lead/` package**: `bw lead` (one locked, job-controlled lead per
  worktree; switch on cap with a handoff; return after reset), `bw status`, `bw cap`,
  `bw sandbox verify`; `bin/bw-statusline` (Claude quota and resolved model from the status
  line, with chaining). `setup/paths.txt` gains the state dir's `leads.json` and
  `handoffs/` (pins and handoffs only).
- **`setup/paths.txt` covers the files Orca edits on first launch** (`~/.codex/hooks.json`,
  `~/.gemini/settings.json`, `~/.gemini/config/hooks.json`, `~/.kimi-code/config.toml`), so
  a backup taken before installing the recommended canvas can undo its hook injection.
  `docs/orca.md` is the integration guide.
- **Log events `session`, `lead`, `rating`, and `panel.moderator`** (`metrics/log.py`):
  `session --phase start|end` records a lead session (worktree key, lead, runtime, model and
  whether it was resolved, plan; `end` carries `exit|switch|lost`); `lead` records every lead
  change with from, to, reason, and the quota used to decide; `rating` is the user's
  good/bad for a session. A `panel` may carry `moderator` (lead, runtime, model, resolved,
  session). `store.load()` returns `sessions`, `leads`, `ratings`. Older logs load unchanged.

- **A decision-model pilot** (reference-implementation.md, "Decision model"): a System One
  model (Jev via OpenRouter by default; Kev, SemIf, OpenJev, Laya as self-hosted options)
  gives raise-only advice on executor tier, reasoning effort, and review depth per task, and
  scores review findings in shadow for later calibration against verified verdicts.
  Documented as a design for the harness to implement (no config schema change yet);
  decisions and agent dispatches are logged to one JSONL.
- **An optional `-b` flag on the seat wrapper interface** (`seat.sh [-b] [-r] [-d dir]
  "prompt"`), and a setup-prompt step 5 that asks for it: on a new session only, the wrapper
  prepends a pre-built, cached *seat brief* (instruction-file head, diff vs merge-base,
  untracked files, likely callers) and a reading rule, so a fresh seat reads the change
  instead of surveying the repository. Backward-compatible: a wrapper without `-b` still
  meets the interface. Rationale, including why resuming a session is not a substitute:
  `docs/architecture.md` (protocol step 1, lessons learned); build details and traps:
  the rebuild guide's "Seat wrappers" section.
- `setup/backup.sh` now covers **the metrics log**, resolved from the config's `metrics_log`
  rather than a fixed path. It is the one file a setup produces that cannot be regenerated,
  and it had no cover at all: excluded from `paths.txt` as "the setup's own output", and
  typically under a state directory a user's dotfiles repo ignores. The rest of the state
  directory stays excluded on purpose — dashboards and indexes rebuild from the log, and
  seat agent dirs hold credentials a backup must not copy.
- `~/.config/balancewheel/paths.local.txt` (or `$BALANCEWHEEL_LOCAL_PATHS`): your own paths,
  appended to the tracked list. A setup predating the config loader, or one keeping state
  somewhere unusual, can now be protected without editing a tracked file. Each backup stores
  the effective list it used, so restores inherit the additions.
- **The inbox** (`lead/inbox.py`, `lead/runner.py`, `bin/bw`): leads ask the user through
  files and bw alone owns the record. A lead publishes each question as one file in `BW_ASKS`
  (`state_dir/handoffs/<worktree-key>/asks/`: write `.tmp-<random>`, rename to `<random>.ask`;
  `lead/protocol/checkpoint.md` says how); bw ingests only `*.ask` regular files, without
  following links or blocking, at most 64 KiB each, text cut at 2000 characters, at most 50 asks
  per session (the rest deleted unread, with one notice), at session start, about once a second,
  and once more after the lead's group is gone — before a switch's handoff, which lists open
  items — under the publishing session (asks an earlier session left undrained, after a crash,
  are recorded under the session whose start found them). Records go to `state_dir/inbox.jsonl` (0600):
  `ask` `{type, id, ts, project, worktree, from, session_id, text, status}` (id
  `sha1(name + content)[:8]`, so a re-ingest dedupes), `notice` `{type, id, ts, project, kind,
  from: "bw", text, status}` with `kind` one of `all-capped`, `pinned-capped`, `router-shadow`
  (no emitter yet), `guard-unreachable`, `inbox` (one open notice per kind and worktree), and
  `answer` `{type, id, ask_id, ts, from: "user", text}`; `ts` is epoch seconds. Answers reach
  the lead in `BW_INBOX` (`state_dir/inbox-view/<worktree-key>/answers.md`), which only bw writes and
  which holds answers only (`@lead answer [id:<ask id>]` and the escaped answer), never ask text.
- **`bw inbox [--project] [--all]`** and **`bw answer <id> "<text>"`** (`bin/bw`): new commands.
  `bw inbox` lists open asks and notices (all worktrees; `--project`: this one; `--all`:
  answered too), lead text escaped. `bw answer` answers an ask or closes a notice (exit 1 for an
  unknown or already answered id) and rewrites that worktree's answers file. `bw status` gains
  an `inbox:` section with open items per worktree.
- **Log events `ask` and `answer`, and `lead` reason `override`** (`metrics/store.py`): `ask`
  `{type, id, project, session_id, from}` per ingested ask and `answer` `{type, id, ask_id}` per
  `bw answer`, both without text (the text lives only in `inbox.jsonl`); `store.load` returns
  them as `asks` and `answers` (empty for older logs). A `bw lead --use` that starts a different
  lead than the profile order would logs a `lead` event with reason `override` (`from` the
  order's pick, `null` when none could start).
- **`setup/paths.txt`** lists `~/.local/state/balancewheel/inbox.jsonl` (the user's asks and
  answers); `inbox-view/` is regenerated and stays out.

### Fixed

- **Final review of plan 4: a contained lead is hostile** (`lead/runner.py`, `lead/handoff.py`,
  `lead/sandbox.py`, `lead/inbox.py`, `lead/status.py`, `lead/projects.py`, `lead/start.py`).
  A file-mode runtime's first prompt (Kimi) is written to `state_dir/prompts/<worktree-key>/first-prompt.md`
  (bw-owned; temp name with `O_EXCL | O_NOFOLLOW`, then renamed), never into the lead-writable
  checkpoint dir where a planted link aimed bw's write. The checkpoint is opened `O_NONBLOCK` (a
  FIFO no longer hangs a switch), and each git section of a handoff is bounded to 200 lines and
  16 KiB with `… K more` (no E2BIG on the next lead's argv). The worktree lock's descriptor a lead
  inherits is read-only; bw writes the metadata through its own descriptor, and `bw status`,
  `bw start`, the cockpit's Now tab and the registry validate it and escape its text. The answers
  view moves to `inbox-view/<worktree-key>/answers.md` (`BW_INBOX`): each lead reads only its own
  worktree's view dir, and both backends deny reading the rest of `inbox-view/` and
  `inbox.jsonl` (every project's asks and answers). A `cap-signal` bw cannot remove (a
  directory) is one notice and no cap, never a crash. `bw lead` now refuses, like `bw start`, when
  bw or its config file lies inside the target worktree or its common git dir (exit 2): the
  config's `tools.*.update`/`version` commands run unsandboxed. The containment rules changed:
  pi and Kimi need `bw sandbox verify` again, and pi guard `bw guard verify pi`.
- **Final review of plan 4: correctness** (`lead/inbox.py`, `lead/verify.py`, `lead/containment.py`,
  `lead/start.py`, `lead/quota.py`, `lead/rotation.py`, `lead/probes.py`, `lead/runner.py`,
  `bin/bw`, `tests/attack/lead_containment.sh`). Rewriting a worktree's answers view holds the
  inbox lock from load to replace, so a stale writer cannot drop a delivered answer. `bw sandbox
  verify`, `bw guard verify` and `bw start`'s re-verification hold a shared lock on the runtime
  from the first key read to the record, so an update of any tool mapped to it (two tools can map
  to one runtime) is skipped meanwhile. **The containment and guard keys now include the denied
  ports** (`denied_ports`, the configured `cockpit.port` among them), so changing the port
  requires a new verification; `bw sandbox verify` passes each as `--deny-port`, and
  `lead_containment.sh` probes the ports it is given (default 6768 15721 8765). Every pi and Kimi
  key changes once with this release: run `bw sandbox verify pi|kimi` and `bw guard verify pi`
  again (or let `bw start` re-verify). `bw start` forwards `BALANCEWHEEL_CONFIG` and
  `BW_STATE_DIR` as absolute paths, reads a fresh clock before quota (step 4) and again before the
  registry (step 5), and with `bw lead` refreshes the plan of a pinned lead outside the
  profile's order. Orca pools are kept while the observation is current even when an account
  window has reset, and each pool lapses at its own reset. OpenRouter's month baseline without
  `usage_monthly` carries the previous month's last reading forward (see the probe entry above).
  A lead that cannot start (a missing binary) ends `bw lead` with one line and **exit 8**
  (`START_FAILED`) instead of a traceback, and the worktree lock is cleared once a switch's end
  is logged, so it is never logged again as `lost`; `bw lead` exits **2** when it refuses a bw or
  config inside the worktree. `bw inbox` and `bw answer` say in one line when `inbox.jsonl`
  cannot be read and exit 1. A mid-session runtime-lock take tries for about a second before
  staying on the current lead (the registry's instant exclusive probe no longer costs a poll).

- **Runtime locks and verification output** (`lead/locks.py`, `lead/verify.py`): `bw lead`
  takes its shared runtime lock before the verification check, and bw keeps a second
  non-inherited shared lock so a lead cannot unlock or upgrade the inherited one; mid-session
  takes never block. Verification failure output now goes to stderr.

- **Lead sandboxes deny cc-switch's data, bw's sealed copies, and the canvas and cockpit
  ports; leads read their answers view** (`lead/sandbox.py`, `lead/runner.py`). Both backends
  deny reading `~/.cc-switch` (added to `SECRET_READS`) and `state_dir/sealed/`, and refuse
  connections, on any host, to 6768 (Orca's orcad), 15721 (cc-switch's proxy), 8765 (the cockpit's
  default) and `cockpit.port` when configured; a lead's own `state_dir/inbox-view/<worktree-key>/`
  is read-granted and in no write grant, while `state_dir/inbox.jsonl` and every other worktree's
  view are read-denied. `python3 -m lead.sandbox run` takes `--deny-port N` (repeatable; default
  those ports). `setup/paths.txt` gains `~/.pi/agent/extensions` (Orca writes pi extensions
  there). The containment rules changed: pi and Kimi leads need `bw sandbox verify` again,
  and pi guard `bw guard verify pi` — run each once, after the next guard change lands.

- **`setup/restore.sh` could destroy the backup it was restoring from.** Backup directories
  are named by a one-second UTC stamp, and a restore takes a safety backup of the current
  state before writing. Run backup → damage → restore inside the same second and that safety
  copy landed in the *same directory* as the backup being restored, overwriting the good copy
  with the damaged state; the restore then reported success and put the damage back, with
  nothing left to recover from. A code comment had dismissed the shared-second case as
  harmless. `backup.sh` now suffixes a stamp whose second is taken (`…Z-2`) and never reuses
  a directory.
- **A second bare `restore.sh` restored the damage instead of undoing it.** "Most recent
  backup" included the safety copy the previous restore had just taken, so pressing undo
  twice cemented the damage while reporting `restored N path(s)`. Safety copies are now
  marked, skipped when choosing a default, labelled in `--list`, and still restorable by
  explicit timestamp — which is how you undo a restore.
- `log.py panel` and `log.py amend` read their record as JSON on stdin, which was
  documented nowhere — not in `--help`, not in any doc a new user reads — while the
  setup prompt's step 8 tells their agent to log a panel. Both subcommands now describe
  the stdin contract, `panel --help` carries the full record shape and a worked example,
  and the top-level help labels both. Found by cold-starting the setup from a fresh
  clone: building a valid record took four rejections, guided only by error messages.
- A regression test now validates that worked example against the shipped
  `balancewheel.example.json`. The first version of it used placeholder seat ids no
  config contains, so every copy-paste would have been rejected.

## [0.1.0] — 2026-09-16

First tagged version. Everything below arrived across 31 commits from 2026-08-25, none of it
previously released, so this is a description of what 0.1.0 *is* rather than a diff against
an earlier version.

### Interfaces this version pins

- **Config `schema_version: 1`.** `metrics/config.py` reads 1 and refuses anything else by
  design, with key-path errors that name the offending field.
- **The metrics JSONL record shapes** — `usage`, `panel`, `watch-update`, `panel-amend`.
  `metrics/store.py` is the only reader and the only appender; nothing else parses raw
  lines. It folds amendments and normalises legacy shapes on load, so logs written by a
  pre-0.1.0 dogfooding setup stay readable.
- **`setup/paths.txt`** — the single list of home-directory paths a setup touches, read by
  both the backup and restore scripts.
- **The seat wrapper interface**, `seat.sh [-r] [-d dir] "prompt"`, as specified in the
  reference implementation guide. Documented here, generated by the user's own agent.

### The pattern

- Architecture: the three-peers/one-moderator topology, the panel protocol (one shared
  brief, blind parallel round, cross-examination of disputed claims only, a substance bar,
  moderator verification against the code), the metrics loop, and the lessons that cost the
  most to learn.
- Reference implementation as a rebuild guide — tools and models named, the settings that
  took debugging, the traps, and a verification checklist.
- Seat policy: same-vendor blind seats, on-demand seats with stated re-promotion criteria,
  two-round cross-examination cap, and per-seat wall-clock caps with a deep tier. A seat that
  does not return is logged absent and the panel proceeds; a timeout never changes the roster.
- Driver mode, separated from the read-only seat guarantee: seats cannot write, drivers are
  contained by a worktree instead, and driver runs are not scored.
- The OpenEscapement boundary — balancewheel generates and justifies seat policy, esc ships
  it as a signed rule pack. The two never write the same file.

### `metrics/`

- `config.py` — config loader with key-path errors and the annotated example config.
- `store.py` — append-only JSONL store: locked appends, amendment folding, legacy
  normalisation, duplicate-id dedupe, and repair of truncated appends.
- `watches.py` — roster watches as pure functions with timestamp-free ids (panel bloat,
  skimmed verification, redundant seat).
- `log.py` — the logger CLI: `config`, `usage`, `panel`, `check`, `watches`, `resolve`,
  `amend`. Unique-catch counts are computed, not self-reported.
- `dashboard.py` — trend dashboard: current-state tiles, roster meters, stacked counts, and a
  date-axis timeline.
- 76 tests, standard-library `unittest`, synthetic fixtures only.

### `setup/`

- `backup.sh` / `restore.sh` over `paths.txt`. A restore snapshots current state before
  touching anything, reports listed paths that are absent as normal, and leaves files the
  setup *created* for you to delete rather than deleting them itself. Credentials are
  deliberately not on the path list: the setup reads them and never writes them.
- The README's install map — what gets written, where, and by whom.

### Docs and conventions

- `README.md` carries the whole plan and the paste-in setup prompt a new user's agent follows.
- `AGENTS.md` alongside `CLAUDE.md`, complementary rather than duplicated.
- `docs/handoffs/` — point-in-time session records, never the source of truth.
- Superpowers named as the workflow skeleton the panel checkpoints and drivers assume.

### Known limits

- `seats/`, `panel/` and `packs/` are planned directories, not code. The extraction criteria
  in `docs/roadmap.md` gate them; `metrics/` and `setup/` landed ahead of that gate on
  purpose, as the metrics loop and install hygiene respectively.
- Driver mode is documented, not implemented here — the launcher is part of the private
  reference setup.
- Extraction criterion 4, "a second user has run the setup from the README alone", is not
  yet met. Until it is, the setup prompt is the least-tested interface in this release.
- The `model-seats` pack in esc is written by hand from this repo's seat policy. The
  regeneration step that would derive it from the log is what `packs/` still owes.

[Unreleased]: https://github.com/tensorgroup/balancewheel/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/tensorgroup/balancewheel/releases/tag/v0.1.0
