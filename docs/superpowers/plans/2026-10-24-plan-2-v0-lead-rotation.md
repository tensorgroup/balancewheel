# Plan 2 — v0 lead rotation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

*Revised 2026-10-24 after a five-seat plan review (68 raw findings, ~20 after dedupe; folded in, not appended). The decisions the review said needed design are stated in "Design decisions" and are binding.*

**Goal:** A user runs `bw lead` in a project and works in their preferred lead; when that lead's plan is capped, `bw` hands the worktree to the next lead in the project's profile, and returns to the preferred lead at the next task boundary after the cap resets — every contained lead verified by attack, every switch logged.

**Architecture:** A `lead/` package (pure functions plus one runner) beside `metrics/`, entry points `bin/bw` and `bin/bw-statusline`. Config schema 2 extends the existing loader. The rotation decision is a pure function, so the transition table is unit-tested without processes; the runner alone touches processes, the terminal, locks, and sandboxes.

**Tech Stack:** Python 3.9+ standard library only (no `X | None` annotations; use `typing.Optional`); `/bin/bash` 3.2; nono (default) or `sandbox-exec` (fallback) at run time; `unittest`, with `pty` for terminal tests.

**Spec:** `docs/superpowers/specs/2026-10-24-profiles-lead-rotation-cockpit-design.md` — "Who leads, who moderates", "The sandbox", "Backends", "Config additions", "Lead rotation", "Quota probes" (statusline only here), "Amendments to earlier documents", "Extraction gate". Build-order step 2. Where this plan departs from the spec, the departure is named in "Design decisions" and Task 9 records it in the spec.

## Global Constraints

- Python 3.9+ standard library only. Tests: `python3 -m unittest discover -s tests -v` from the repo root.
- Clean-room from the spec; synthetic fixtures only; placeholder model ids in fixtures.
- Model ids are data. Lead, plan, and profile names are the user's.
- `schema_version` 2; a version-1 file loads as one implicit profile with no leads (`bw lead` then exits with "no leads configured").
- Never write into a project repo. State lives under `state_dir`.
- No credential is read. Claude quota and the resolved Claude model come only from the statusline payload.
- **Percent semantics:** stored windows carry `used_pct`; every threshold is on `remaining = 100 - used_pct`.
- Sandbox profiles deny `lsopen`, `appleevent-send`, `user-preference-write`, and exec of `/usr/bin/open`, `/usr/bin/osascript`, `/bin/launchctl`, `/usr/bin/defaults`; nono profiles `extends: "default"` and exclude `system_write_macos`; every path is real-path resolved; paths containing `"` or `\` are refused; Seatbelt rules use `literal`/`subpath`, never `regex` with embedded paths.
- Commits: lowercase `feat:`/`fix:`/`test:`/`docs:`. CHANGELOG `[Unreleased]` in the same commit as any change to the config schema, log schema, or `setup/paths.txt`.

## Design decisions (binding; from the review)

1. **Job control.** The runner owns the terminal the standard way: the child runs in its own process group (`preexec_fn` calling `os.setpgid(0, 0)`), the runner hands it the foreground with `os.tcsetpgrp(tty_fd, child_pgid)` while ignoring `SIGTTOU`, `SIGINT`, `SIGQUIT`, and `SIGTSTP`, and takes the foreground back after the child's group is gone. Stdio stays the terminal (no capture). Stopping a lead: `SIGTERM` to the group, wait up to 10 s, `SIGKILL` the group, then confirm the group is gone (`os.killpg(pgid, 0)` raises `ProcessLookupError`) **before** anything else starts in the worktree.
2. **Lock lifetime follows the child.** The worktree lock is an `fcntl.flock` on a file opened without truncation (`os.open(path, os.O_RDWR | os.O_CREAT, 0o600)`), with metadata written only after the lock is held. The locked descriptor is passed to the child (`pass_fds`), so the kernel holds the lock until both runner and child are gone: a dead runner cannot release the worktree while its lead still writes. Reclaim is the kernel's (a `LOCK_EX | LOCK_NB` that succeeds means nobody holds it); pid and session in the file are for messages and for closing the stale session as `lost`.
3. **Observation channels in v0.** Claude Code: the statusline gives `rate_limits` (caps, quota) and `model.id` (resolved model). Every other lead: the configured model is logged with `resolved: false`, and a cap is recorded by `bw cap <plan> [--resets-at <epoch>]` (run by the user, or by a runtime hook where one exists — Kimi's `StopFailure` hook is wired in Task 8). v0 claims automatic switching only for statusline-reporting leads and says so in `bw status` and the README.
4. **Which leads bw sandboxes.** pi and Kimi run under `bw sandbox` (they have no verified containment of their own). Claude Code, Codex, and agy run under their own sandboxes with their own configuration and the user's real `HOME` (their settings, statusline, commands, and auth live there); their containment is still attack-verified (Task 5's script) before `bw lead` will start them. No lead gets a private `HOME` unless its adapter asks for one.
5. **What a contained lead may write.** Its worktree; its own git admin dir and the shared objects/refs it needs to commit; its runtime state dir; and `state_dir/handoffs/<worktree-key>/` (to write checkpoints). Never the rest of `state_dir` (locks, pins, quota, containment records). Git control files are write-denied: `<common>/hooks`, `<common>/config`, `<common>/config.worktree`, `<common>/worktrees/<name>/config.worktree` for every existing worktree, the linked worktree's `.git` file, and `<common>/worktrees` except the lead's own `<name>` dir (so a lead cannot create or rewire other worktrees).
6. **Keys.** The lock and the handoff/checkpoint directory are keyed by `worktree-key = sha1(real path of the worktree)[:12]` (not the spec's repo id: two worktrees of one repo are independent tasks). The profile is still resolved per repository. Task 9 records the change in the spec.
7. **Scope cuts.** `task` events and `panel.subject` move to build-order steps 7 and 3 (no v0 emitter). `panel.moderator` stays (one field, needed the first time a non-Claude lead moderates).

## Review Focus

- **A runner killed hard while its lead keeps running:** the lock stays held (inherited descriptor) and a second `bw lead` refuses; once the lead exits, the next start closes the old session as `lost`.
- **The statusline field disappearing** (anthropics/claude-code#45133) or a stale snapshot after reset: the plan's quota becomes `unknown`, and the preferred lead becomes eligible again once its cap's `resets_at` (or the expiry) passes.
- **All leads capped:** the runner keeps the current session, prints the earliest reset, and does not tear anything down.
- **A lead editing git control files** to plant a hook that later runs unsandboxed (for example when `bw` builds the handoff): every path in Design decision 5 is write-denied and covered by the attack script.
- **Two projects' statuslines writing quota at once:** per-plan files, so neither overwrites the other.

---

### Task 1: Config schema 2

**Files:** Modify `metrics/config.py`, `balancewheel.example.json`; Test `tests/test_config.py`, `tests/fixtures/config_v2.json`.

**Interfaces:**
- Produces: `load_config(path=None) -> dict` adds `plans`, `leads`, `tools`, `profiles`, `projects`, `default_profile`, `rotation`, `updates`, `cockpit` (defaults filled). Leads use **`runtime`** (∈ `RUNTIMES`, the same names seats use), not the spec's `tool`. Accepted and validated now even though later steps consume them: `tools.*.update` (argv list), `cockpit.port`, `seats.*.plan`, `seats.*.fallback`, `team` (object, unvalidated beyond type). Constants `BILLING`, `QUOTA_SOURCES`, `DEPTHS`, `ROUTER_MODES` as in the spec. A v1 file loads with `profiles = {"default": {"lead_order": [], "checkpoints": {}}}`.

- [ ] **Step 1: Failing tests:** `test_v1_loads_with_empty_default_profile`; `test_v2_example_loads`; `test_spec_config_block_loads` (the spec's example, with `tool` renamed `runtime`); `test_unknown_plan_billing_rejected` (names `plans.x.billing`); `test_lead_unknown_plan_rejected`; `test_lead_unknown_runtime_rejected`; `test_profile_lead_order_unknown_lead_rejected`; `test_checkpoint_required_not_in_seats_rejected`; `test_min_seats_exceeds_seats_rejected`; `test_budget_only_on_metered`; `test_seat_fallback_must_name_a_seat`; `test_rotation_defaults` (`return_above_pct == 50`, `cap_expiry_hours == 5`, `probe_every_min == 15`); `test_updates_default_interval_zero`.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_config -v` — FAIL.
- [ ] **Step 3: Implement** validators and cross-reference checks; update the example config (placeholder rows, "replace these").
- [ ] **Step 4: Run** the full suite — PASS.
- [ ] **Step 5: Commit** `feat: config schema 2 — plans, leads, profiles, projects, rotation` (CHANGELOG).

### Task 2: Project resolution

**Files:** Create `lead/__init__.py`, `lead/projects.py`; Test `tests/test_projects.py`.

**Interfaces:**
- Produces: `resolve_project(cwd, config) -> Project`, `Project = namedtuple("Project", "worktree worktree_key common_dir main_worktree profile")`. Uses `git -C <cwd> rev-parse --path-format=absolute --show-toplevel --git-common-dir`, then `git worktree list --porcelain` (first entry) for `main_worktree`; a bare repo or a separate git dir with no main worktree → `ValueError` naming the layout. `worktree_key = sha1(realpath(worktree))[:12]`. Profile = longest path-component match of `config["projects"]` against `main_worktree`, else `default_profile`.

- [ ] **Step 1: Failing tests** (temp repos): `test_component_match_not_prefix`; `test_longest_match_wins`; `test_linked_worktree_gets_repo_profile_and_own_key`; `test_symlinked_cwd_resolves`; `test_main_worktree_common_dir_absolute`; `test_unmatched_gets_default`; `test_not_a_repo_raises`; `test_bare_repo_layout_rejected`.
- [ ] **Steps 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: resolve a working directory to its worktree, repo, and profile`.

### Task 3: The rotation decision (pure)

**Files:** Create `lead/rotation.py`; Test `tests/test_rotation.py`.

**Interfaces:**
- Produces: `decide(profile, leads, rotation, current, pinned, quota, caps, now, moment) -> Decision`; `moment` ∈ `"start" | "running" | "boundary"`; `Decision = namedtuple("Decision", "lead reason notice")`, `reason` ∈ `order, pinned, capped, quota-low, returned, stay, none-eligible`. `quota[plan] = {"windows": [{"name", "used_pct", "resets_at"}], "observed_at"}` or absent (unknown). `caps[plan] = {"resets_at", "window", "source"}`.
- `effective_quota(snapshot, now) -> dict|None` — `None` (unknown) when the snapshot is absent, or when `now` is past the earliest `resets_at` in it (a stale pre-reset reading). `cap_record(now, resets_at=None, expiry_hours=5)` — `resets_at` or `now + expiry_hours*3600`.
- Rules: `running` → leave only when the current plan has an unexpired cap **and** another lead is eligible; otherwise `stay` (or `none-eligible` with the earliest reset in `notice`). Eligible = no unexpired cap; unknown quota is eligible. `start` → also skip a lead whose any window has `remaining < 10` with `resets_at - now > 3600`. `start`/`boundary` → return to an earlier lead in `lead_order` whose cap has passed `resets_at` when its effective quota has `remaining >= rotation["return_above_pct"]` in every window, or is unknown. `pinned` wins unless capped (then fall through; the pin is kept).

- [ ] **Step 1: Failing tests:** `test_cap_midsession_switches_when_other_eligible`; `test_cap_midsession_all_capped_stays_with_notice`; `test_low_not_capped_midsession_stays`; `test_low_long_reset_at_start_skips`; `test_low_short_reset_at_start_keeps`; `test_unknown_quota_is_eligible`; `test_return_after_reset_uses_remaining_not_used` (used 2% → remaining 98 ≥ 50 → returns); `test_no_return_midturn`; `test_return_with_unknown_quota_after_reset`; `test_stale_snapshot_after_reset_is_unknown`; `test_cap_without_reset_uses_expiry`; `test_pinned_wins`; `test_pinned_capped_falls_through_keeps_pin`; `test_custom_return_threshold_used`.
- [ ] **Steps 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: lead rotation decision as a pure function over quota, caps, and time`.

### Task 4: Quota, caps, and the Claude statusline

**Files:** Create `bin/bw-statusline` (bash), `lead/quota.py`, `lead/_bootstrap.py`; Test `tests/test_quota.py`, `tests/fixtures/statusline/{with_limits,no_limits,limits_100,model_only}.json`.

**Interfaces:**
- Storage, one file per plan so writers never race: `state_dir/quota/<plan>.json` (snapshot, or `{"unknown": true, "reason", "observed_at"}`) and `state_dir/caps/<plan>.json`. Each written atomically (temp in the same dir + `os.replace`).
- Produces: `parse_statusline(payload) -> (snapshot|None, model_id|None)`; `record_statusline(state_dir, plan, payload, now)` — snapshot present → write it, write a cap if any window is at 100%, **clear** the cap if every window is below 100% and past the cap's `resets_at`; `rate_limits` absent → write `unknown` (replacing any earlier snapshot); `model.id` → `state_dir/models/<plan>.json` (resolved model for the session log). `read_quota(state_dir) -> dict`, `read_caps(state_dir, now, expiry_hours) -> dict` (expired caps dropped). `record_cap(state_dir, plan, now, resets_at=None, expiry_hours)` (used by `bw cap`).
- `lead/_bootstrap.py`: puts the repo root (resolved from the real path of the invoking script, so symlinks into `bin_dir` work) on `sys.path`. Both `bin/` scripts import it first.
- `bin/bw-statusline <plan>`: reads all of stdin into a variable once; runs the recorder with it; if `BW_STATUSLINE_CHAIN` is set, pipes the same buffered input to that command (in the original working directory) and prints its output; else prints `5h <remaining>% · 7d <remaining>%` or `quota unknown`.

- [ ] **Step 1: Failing tests:** parse with limits and model id; absent limits → unknown replaces a previous snapshot; 100% → cap with that window's `resets_at`; below 100% after reset → cap cleared; two plans written concurrently (two processes) both survive; expired cap dropped by `read_caps`; statusline run **from an unrelated temp directory** through a symlink in a temp `bin_dir` imports `lead` and prints the summary; chaining passes byte-identical stdin to the chained command.
- [ ] **Steps 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: per-plan quota and caps, claude statusline recorder with chaining`.

### Task 5: `bw sandbox` — profiles, verification, containment records

**Files:** Create `lead/sandbox.py`, `lead/containment.py`, `tests/attack/lead_containment.sh`; Test `tests/test_sandbox.py`, `tests/test_containment.py`.

**Interfaces:**
- Produces: `lead_grants(project, runtime_grants, state_dir) -> Grants` per Design decision 5 (`Grants = namedtuple("Grants", "write deny_write read")`), enumerating existing worktrees from `git worktree list --porcelain`; `nono_profile(grants, scratch) -> dict` (git-control denies via `unsafe_macos_seatbelt_rules` with `literal`/`subpath`, because nono's typed deny also blocks reads); `seatbelt_profile(grants, scratch) -> str`; `refuse_overlap(...)`; `backend() -> "nono"|"sandbox-exec"`; `wrap(argv, grants, backend, home, scratch, env) -> (argv, cleanup)`.
- `containment.verification_key(runtime, binary_path) -> dict` = runtime binary version, backend name and version, sha256 of the generated profile template for that runtime, sha256 of the adapter source; `record_pass(state_dir, runtime, key, evidence_dir)`; `is_verified(state_dir, runtime, key) -> bool` (exact key match). Stored in `state_dir/containment.json`, written only by `bw sandbox verify`.
- `bw sandbox verify <runtime>` (wired in Task 8): runs `tests/attack/lead_containment.sh` for that runtime under the backend `bw lead` would use, and on PASS calls `record_pass`. For Claude Code / Codex / agy it verifies the runtime's own sandbox configuration the adapter launches with.

- [ ] **Step 1: Failing tests:** lead grants include worktree, own git admin dir, objects/refs, runtime state, `handoffs/<key>/`; never `locks/`, `leads.json`, `quota/`, `caps/`, `containment.json`; every git-control path in Design decision 5 is in `deny_write` (including `<common>/config.worktree` and the linked worktree's `.git` file); another worktree's admin dir is denied; IPC rules present in both backends; no `regex` in generated Seatbelt text; path with `(`, `+`, or `.` handled literally; overlap and quote refusal; `is_verified` false on any key field change.
- [ ] **Steps 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Write `lead_containment.sh <runtime>`:** temp repo plus a linked worktree; the runtime launched non-interactively through its adapter's verify command under `wrap`; probes — write and commit inside must succeed; writes outside, `.git/hooks`, `git config`, `config.worktree` (main and linked), the linked `.git` file, another worktree's admin dir, a symlink escape, a `node` child writing outside, `/tmp`, `defaults write`, `open` must fail; decided from disk; prints PASS/FAIL and the evidence dir.
- [ ] **Step 6: Run** it for one runtime per backend available on the machine; paste the PASS lines into the commit message.
- [ ] **Step 7: Commit** `feat: bw sandbox — lead profiles, containment verification keyed to the runtime and profile`.

### Task 6: Events — session, lead, rating; panel moderator

**Files:** Modify `metrics/store.py`, `metrics/log.py`, `CHANGELOG.md`; Test `tests/test_store.py`, `tests/test_log.py`.

**Interfaces:**
- Produces: `log.py session --phase start|end --project <worktree-key> --worktree --lead --runtime --model --resolved true|false --plan --session-id [--reason exit|switch|lost]`; `log.py lead --project --from-json --to-json --reason --quota-json` (logged for every change: switch, return, pin, unpin); `log.py rating --session-id --value good|bad [--note]`; `panel` accepts optional `moderator {lead, runtime, model, resolved, session}`. `store.load()` returns `sessions`, `leads`, `ratings`.

- [ ] **Step 1: Failing tests:** each validator (session start requires model and `resolved`; end requires reason); store folding; `panel.moderator` round trip; legacy logs still load.
- [ ] **Steps 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: session, lead, and rating events; panel moderator` (CHANGELOG: log schema).

### Task 7: Handoffs and checkpoints

**Files:** Create `lead/handoff.py`, `lead/protocol/checkpoint.md`; Test `tests/test_handoff.py`.

**Interfaces:**
- Produces: `checkpoint_dir(state_dir, worktree_key) -> str`; `build_handoff(worktree, since_head, state_dir, worktree_key, inbox_items) -> str` (terse envelope: branch, `git status --short`, commits since `since_head`, diff stat, latest checkpoint text, open inbox items). Git is run with `-c core.hooksPath=/dev/null -c core.fsmonitor=false` and an environment cleared of `GIT_*`, so nothing a lead planted runs when `bw` reads the repo. `first_prompt(handoff, protocol_text) -> str` prepends `checkpoint.md` (which tells the lead to write `<checkpoint_dir>/checkpoint.md` in the envelope at every task boundary and to treat inbox answers as the user's words).

- [ ] **Step 1: Failing tests:** handoff has branch, a new commit subject, the checkpoint text; `checkpoint: none` when absent; a planted `.git/hooks/post-checkout` and `core.fsmonitor` in a temp repo's config do not execute during `build_handoff` (marker file absent); `first_prompt` begins with the protocol text.
- [ ] **Steps 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: handoffs from git facts read safely, and the checkpoint protocol`.

### Task 8: `bw` — lead runner, status, cap, pin, sandbox verify

**Files:** Create `lead/runner.py`, `lead/runtimes.py`, `bin/bw`; Test `tests/test_runner.py` (with `tests/fixtures/fake_lead.py`, driven through `pty.fork()` so job control is real).

**Interfaces:**
- `runtimes.ADAPTERS[runtime] = Adapter(argv(lead, first_prompt) -> list, contained_by: "bw"|"self", grants(lead) -> RuntimeGrants(read, write, env, private_home: bool), prompt_mode: "arg"|"file", cap_hook: Optional[str], verify_argv(...))`. Adapters for claude-code, codex, agy (contained by self; real `HOME`), kimi and pi (contained by `bw`; Kimi gets `KIMI_CODE_HOME` = its real dir as a write grant and a private `HOME` because it watches `$HOME`; pi gets `PI_CODING_AGENT_DIR` as a write grant). `prompt_mode` is `"arg"` only where the CLI accepts an initial interactive prompt (verified by running it once in Task 8 step 5); otherwise `"file"`: the handoff is written to the checkpoint dir and its path printed before launch. Kimi's adapter installs a `StopFailure` hook in its config that runs `bw cap <plan>`.
- `bw lead [--use <lead> | --auto] [--no-rating]`, `bw status`, `bw cap <plan> [--resets-at <epoch>]`, `bw sandbox verify <runtime>`.
- Runner: resolve project → acquire the lock (Design decision 2; refuse with the holder's pid, lead, and start time) → close any session the lock file names as still open with `reason: "lost"` → `decide(moment="start")` → refuse a runtime that `is_verified` rejects, printing `bw sandbox verify <runtime>` → launch per Design decision 1 with the lock descriptor in `pass_fds` → log `session start` (model from `state_dir/models/<plan>.json` for Claude when present, else configured with `resolved: false`) → every `probe_every_min` (and on `SIGCHLD`): `read_caps`, `decide(moment="running")`; on a different lead: stop the child per Design decision 1, confirm the group is gone, log `session end reason=switch` and `lead`, build the handoff, launch the next lead the same way; on `none-eligible`: print the notice once, keep the session → on child exit: restore the terminal, log `session end`, ask for a rating unless `--no-rating`, run `decide(moment="boundary")` and log any return as a `lead` event, print the notice.

- [ ] **Step 1: Failing tests** through `pty.fork()`: `test_child_gets_foreground_and_runner_survives_ctrl_c`; `test_second_runner_refused_while_first_holds_lock`; `test_lock_held_after_runner_sigkill_while_child_lives`; `test_stale_session_closed_lost_after_both_exit`; `test_cap_switches_after_group_confirmed_gone` (fake lead A ignores SIGTERM; runner escalates to SIGKILL; B starts only after); `test_all_capped_keeps_session`; `test_return_logged_at_boundary`; `test_pin_and_auto`; `test_unverified_runtime_refused_with_verify_hint`; `test_status_shows_unknown_and_v0_scope_note`; `test_bw_cap_records_cap`.
- [ ] **Steps 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Manual checks** on this machine: for each installed runtime, confirm `prompt_mode` by launching it once with an initial prompt; `bw sandbox verify` for pi and Kimi; `bw lead` in a scratch repo with a Claude lead — the statusline recorder writes quota, a write outside the worktree from inside the session fails, quitting logs `session end` and asks for a rating.
- [ ] **Step 6: Commit** `feat: bw lead — job-controlled, locked, contained lead sessions with switch on cap`.

### Task 9: Docs, contract files, spec amendments

**Files:** Modify `README.md` ("Leads and rotation": profiles, `bw lead`, `bw status`, `bw cap`, `bw sandbox verify`, the statusline install line with chaining, which leads switch automatically in v0 and which need `bw cap`), `docs/architecture.md` (lead = moderator; driver-mode narrowing), `docs/roadmap.md` (extraction-gate amendment and reason; non-goal restated), the spec (record Design decisions 4–7: leads use `runtime`; which leads `bw` sandboxes; worktree-keyed locks and handoffs; state_dir write boundary), `setup/paths.txt` (the statusline script; **only** `state_dir/leads.json` and `state_dir/handoffs/` — never locks, quota, caps, models, or containment, which must not be restored), `CHANGELOG.md`, `CLAUDE.md` (the `lead/` package and `bin/` scripts); Test `tests/test_setup_scripts.py`.

- [ ] **Step 1:** Write the docs; date each preamble 2026-10-24 and say what changed.
- [ ] **Step 2: Run** the full suite — PASS; `tests/test_setup_scripts.py` asserts the new paths and asserts `locks/`, `quota/`, `caps/`, `containment.json` are absent from `paths.txt`.
- [ ] **Step 3: Commit** `docs: leads, rotation, and the sandbox — readme, architecture, roadmap, spec, paths`.
