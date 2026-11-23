# Orca Optional Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Orca an optional canvas. bw opens a Terminal tab by default, makes zero `orca` calls unless the user opts in, and reads Codex quota from what the Codex CLI prints.

**Architecture:** A new top-level config key `canvas` (`terminal` or `orca`) and one predicate, `orca.in_use(config)`, gate every Orca call site. The example config and the docs move to Terminal first. A passive quota source `cli` comes last. It reads the `rate_limits` that a Codex seat run prints, so it depends on Project 1 (plan 5, `seats/`).

**Tech Stack:** Python 3.9+ standard library only; `unittest`; synthetic fixtures.

**Spec:** `docs/superpowers/specs/2026-11-22-seats-panel-guard-packs-design.md`, section "Project 3 — Orca becomes optional" (also "Build order", "Amendments to earlier documents", "Open risks"). Line references are to `main` at `80e5058`.

## Global Constraints

- Python 3.9+ standard library only. No YAML parser, no HTTP library.
- Tests: `python3 -m unittest discover -s tests -v` from the repo root. The whole suite passes at the end of every task.
- Fixtures in `tests/fixtures/` are synthetic. Never paste real Orca, Codex, or quota output.
- No preview model id in an interface. Models stay one config line.
- New config keys go in the schema (`metrics/config.py`), in `balancewheel.example.json`, and in `CHANGELOG.md` `[Unreleased]` in the same commit.
- Any change to a `bw` command, a module, an adapter, or a config key also runs `python3 docs/usage-map/generate.py` and commits `docs/usage-map/data.generated.js` in the same commit.
- Any change of tool updates `docs/stack.md` in the same commit.
- Docs carry a dated preamble. When you change a doc, add "Revised 2026-11-22: …" that says what changed.
- Commit summaries are lowercase with a `feat:` / `fix:` / `test:` / `docs:` prefix.
- `canvas` ∈ {`terminal`, `orca`} (schema 3, in `V3_TOP_KEYS`). When a schema-3 file has no `canvas` key, it resolves to `orca` if the file has `tools.orca` or any plan with `orca_provider` (the user runs Orca today), else `terminal`. A schema 1 or 2 file resolves to `orca`. The example sets `canvas` explicitly (moderator's call, plan panel: O-a).
- With `canvas: terminal` and no plan that sets `orca_provider`, bw makes **zero** `orca` calls. That includes a configured `tools.orca` update or version command (moderator's call, plan panel: O-b).
- With `canvas: orca`, behaviour is today's.
- The sandbox's Orca denies (`CANVAS_CONTROL_DIRS`) stay on in every case. Do not touch `lead/sandbox.py`.
- Quota source `cli`: `QUOTA_SOURCES` gains `"cli"`; `lead/quota.py` `SOURCES` gains `"cli"`; `ORDER["cli"] = ["cli", "orca"]`; `orca` applies only when the plan sets `orca_provider`.
- `rotation.cli_fresh_min`, default `120`. A `cli` reading older than that, or past its own window's reset, reads unknown.
- `quota: "cli"` and `rotation.cli_fresh_min` are accepted at schema 3 only (moderator's call, plan panel: O-h).
- Only real seat runs feed the `cli` source. A seat attack runs in an isolated state dir (plan 5, design decision 5), and its readings stay there. bw never copies a reading across state dirs (moderator's call, plan panel: O-f).
- Snapshot file for the `cli` source: `state_dir/quota/<plan>.cli.json`.
- Without Orca, Kimi plans use observed caps only (`quota: "none"`).
- Docs present Orca as one possibility, in the user's words: "If not, simply mention it as a possibility."

## Review Focus

1. **Orca is installed and running, but `canvas` is `terminal`.** The user expects a Terminal tab, no Orca pane, and no `orca status` call. Pinned by `test_terminal_canvas_ignores_a_running_orca` (Task 2).
2. **An existing config that uses Orca today and has no `canvas` key** (schema 2, or schema 3 with `tools.orca` or an `orca_provider`). The user expects today's Orca pane, not a silent change to Terminal. Pinned by `test_schema_2_keeps_the_orca_canvas` and `test_schema_3_without_canvas_infers_orca_from_tools_or_provider` (Task 1).
3. **`canvas: terminal` with one plan that sets `orca_provider`.** The user expects Orca's quota read for that plan and a Terminal tab, never an Orca pane. Pinned by `test_terminal_canvas_with_an_orca_plan_reads_quota_but_opens_terminal` (Task 2).
4. **A `rate_limits` object with a bad shape**: not an object; a bool, string, non-finite, or out-of-range `used_percent`; a missing `primary` window; an unknown `window_minutes`; a `resets_at` at or before now, or one in milliseconds. The user expects no snapshot write, no exception, and an existing good snapshot left byte-identical; the good reading ages out on its own (moderator's call, plan panel: O-c). Pinned by `test_malformed_rate_limits_write_nothing` and `test_bad_input_never_overwrites_a_good_snapshot` (Task 5).
5. **A `cli` reading judged by the probe window.** `probe_every_min` is 15, so a 40-minute-old `cli` reading must still be fresh (120 minutes), and a 121-minute-old one stale. Pinned by `test_cli_freshness_uses_cli_fresh_min_not_probe_interval` (Task 5).

---

## File structure

| File | Responsibility in this plan |
|---|---|
| `metrics/config.py` | `canvas` key (Task 1); `cli` source and `rotation.cli_fresh_min` (Task 5) |
| `lead/orca.py` | `canvas(config)` and `in_use(config)`: the one gate (Task 1) |
| `lead/start.py` | pane, dry run, `app_version`, checks gated (Task 2) |
| `lead/status.py` | `orca` checks gated (Task 2); passes `cli_fresh_min` to `arbitrate` (Task 5) |
| `lead/probes.py` | the Orca thread starts only for a mapped plan (Task 2) |
| `lead/quota.py` | `cli` source, freshness, `parse_cli_rate_limits`, `record_cli` (Task 5) |
| `lead/updates.py`, `lead/registry.py` | a configured `tools.orca` is skipped on the Terminal canvas (Task 2) |
| `bin/bw` | docstring: Terminal first, Orca optional (Task 2) |
| `seats/run.py` | step 8: a real Codex seat run records its `cli` snapshot before any stand-in (Task 6; file made by plan 5) |
| `balancewheel.example.json` | Terminal canvas, no Orca (Task 3); `chatgpt-plan` → `cli` (Task 5) |
| `README.md`, `docs/orca.md`, `docs/stack.md`, `docs/architecture.md`, `CLAUDE.md`, `docs/usage-map/infra.js` | Orca as a possibility (Task 4); the `cli` source (Tasks 5, 6) |

---

### Task 1: The `canvas` config key and the Orca gate

**Files:**
- Modify: `metrics/config.py:20` (`V3_TOP_KEYS`), `metrics/config.py:417-500` (`load_config`)
- Modify: `lead/orca.py` (three new functions near `support_dir`, `lead/orca.py:35`)
- Modify: `CHANGELOG.md` (`[Unreleased]`), `docs/usage-map/data.generated.js` (regenerated)
- Test: `tests/test_config_v3.py`, `tests/test_config_v2.py`, `tests/test_orca.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `metrics.config.CANVASES = {"terminal", "orca"}`.
  - Resolved config key `"canvas"`: always present after `load_config`, a str in `CANVASES`.
  - `lead.orca.canvas(config: dict) -> str` — `config.get("canvas", "terminal")`. Raw dicts in tests that lack the key read `"terminal"`.
  - `lead.orca.in_use(config: dict) -> bool` — `True` when `canvas(config) == "orca"` or any `config["plans"][*]["orca_provider"]` is truthy.
  - `lead.orca.active_tools(config: dict) -> dict` — `config.get("tools") or {}`, without the key `"orca"` when `canvas(config) == "terminal"` (moderator's call, plan panel: O-b). The Orca tool is the tool named `orca`, as in the example and `docs/orca.md`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_config_v3.py`, a new class `Canvas` with its own `rejects` (copy the one in `SchemaV3`). The fixture `v3()` (`tests/fixtures/config_v3.json`) has no `tools` and no `orca_provider`.

```python
def test_canvas_defaults_to_terminal_without_orca_config(self):
    self.assertEqual(load_obj(v3())["canvas"], "terminal")

def test_schema_3_without_canvas_infers_orca_from_tools_or_provider(self):
    o = v3(); o["tools"] = {"orca": {"self_updating": True}}
    self.assertEqual(load_obj(o)["canvas"], "orca")
    o = v3(); o.setdefault("plans", {})["pz"] = {"billing": "plan", "quota": "orca", "orca_provider": "codex"}
    self.assertEqual(load_obj(o)["canvas"], "orca")

def test_explicit_canvas_wins_over_the_inference(self):
    o = v3(); o["tools"] = {"orca": {"self_updating": True}}; o["canvas"] = "terminal"
    self.assertEqual(load_obj(o)["canvas"], "terminal")

def test_canvas_bad_value_rejected(self):
    for bad in ("Terminal", "nodeterm", "", 1, None, ["orca"]):
        o = v3(); o["canvas"] = bad
        self.rejects(o, "canvas: must be one of ['orca', 'terminal']")
```

In `tests/test_config_v2.py` (`v2()` and `rejects` exist there):

```python
def test_schema_2_keeps_the_orca_canvas(self):
    self.assertEqual(cfg.load_config(str(V2))["canvas"], "orca")

def test_canvas_needs_schema_3(self):
    o = v2(); o["canvas"] = "terminal"
    self.rejects(o, "canvas: unknown key (needs schema_version 3)")
```

In `tests/test_orca.py`, a new class `Gate`:

```python
def test_in_use_false_for_terminal_without_providers(self):
    c = {"canvas": "terminal", "plans": {"a": {"orca_provider": None}}}
    self.assertFalse(orca.in_use(c))

def test_in_use_true_for_orca_canvas(self):
    self.assertTrue(orca.in_use({"canvas": "orca", "plans": {}}))

def test_in_use_true_for_a_mapped_plan(self):
    self.assertTrue(orca.in_use({"canvas": "terminal", "plans": {"a": {"orca_provider": "codex"}}}))

def test_canvas_missing_reads_terminal(self):
    self.assertEqual(orca.canvas({"plans": {}}), "terminal")

def test_active_tools_drops_orca_on_the_terminal_canvas(self):
    tools = {"orca": {"self_updating": True}, "pi": {"update": ["pi", "up"]}}
    self.assertEqual(set(orca.active_tools({"canvas": "terminal", "tools": tools})), {"pi"})
    self.assertEqual(set(orca.active_tools({"canvas": "orca", "tools": tools})), {"orca", "pi"})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_config_v3 tests.test_config_v2 tests.test_orca -v`
Expected: FAIL — `KeyError: 'canvas'`, `canvas: unknown key`, and `AttributeError: module 'lead.orca' has no attribute 'in_use'`.

- [ ] **Step 3: Implement the key and the gate**

In `metrics/config.py`: add `"canvas"` to `V3_TOP_KEYS`; add `CANVASES`. In `load_config`, after plans and tools are validated:
- given `canvas`: check it with `_expect(isinstance(canvas, str) and canvas in CANVASES, f"canvas: must be one of {sorted(CANVASES)}")`;
- absent, schema 3: `"orca"` when `"orca" in obj.get("tools", {})` or any resolved plan has `orca_provider`, else `"terminal"`;
- absent, schema 1 or 2: `"orca"`, as `LEGACY_AUTONOMY` keeps a schema-2 lead on today's behaviour.
Return it as `"canvas"`.

In `lead/orca.py`: add `canvas(config)`, `in_use(config)`, and `active_tools(config)` as the Interfaces block says. None of them reads a file or runs a command.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS, every test.

- [ ] **Step 5: CHANGELOG and usage map**

Under `[Unreleased]`:
- `### Added`: "**`canvas` (schema 3).** `terminal` or `orca`."
- `### Changed` (moderator's call, plan panel: O-a): "**Orca is no longer the default canvas.** A schema-3 config without `canvas` keeps Orca panes when it has `tools.orca` or a plan with `orca_provider`; any other config opens Terminal tabs. Set `"canvas": "orca"` to keep Orca, or `"canvas": "terminal"` to stop using it. A schema 1 or 2 file reads `orca`."

Run `python3 docs/usage-map/generate.py`. Expected: it exits 0 and rewrites `docs/usage-map/data.generated.js`.

- [ ] **Step 6: Commit**

```bash
git add metrics/config.py lead/orca.py tests/test_config_v3.py tests/test_config_v2.py tests/test_orca.py CHANGELOG.md docs/usage-map/data.generated.js
git commit -m "feat: a canvas key that gates every orca call"
```

---

### Task 2: Zero `orca` calls on the Terminal canvas

**Files:**
- Modify: `lead/start.py:1-20` (docstring: steps 4, 6, 7 and the `--dry-run` line), `:129` (tools), `:179-184` (`app_version`), `:205-207` (checks), `:226-251` (`_workspace`), `:254-282` (`_dry_run`)
- Modify: `lead/status.py:121-127` (`facts`), `:166-168` (`render`)
- Modify: `lead/probes.py:219` (the Orca thread)
- Modify: `lead/updates.py:129` (`update_all` tools), `lead/registry.py:145` (tools for `tool_node`)
- Modify: `lead/runner.py:765-773` (comment only)
- Modify: `bin/bw:1-25` (docstring: the `bw start`, `bw status`, `bw probe` lines; the usage map reads it)
- Modify: `docs/usage-map/data.generated.js` (regenerated)
- Test: `tests/test_start.py`, `tests/test_status.py`, `tests/test_probes.py`, `tests/test_runner.py`, `tests/test_updates.py`, `tests/test_registry.py`

**Interfaces:**
- Consumes: `orca.canvas(config) -> str`, `orca.in_use(config) -> bool`, `orca.active_tools(config) -> dict` (Task 1).
- Produces:
  - `start._workspace(state, project, run, bw, env, say, canvas: str) -> int` — new last parameter.
  - `status.facts(...)["checks"]` has the key `"orca"` only when `orca.in_use(config)`. `status.render` prints the check titles that are present, in the order `orca`, `cc-switch`.
  - `updates.update_all`, `registry.build`, and `start.start` (its tool list and its dry-run tool lines) iterate `orca.active_tools(config)`, not `config["tools"]` (moderator's call, plan panel: O-b).

**Existing tests that need `canvas: "orca"`.** They use raw config dicts with no `canvas` key, so they read `terminal` after this task. Set `self.config["canvas"] = "orca"` (or `"canvas": "orca"` in the test's config) first. Do not delete them: they prove `canvas: orca` is today's path (moderator's call, plan panel: O-d).
- `tests/test_start.py`: `test_orca_running_opens_pane_with_exact_argv`, `test_orca_absent_writes_command_file_and_opens_terminal`, `test_relative_config_and_state_paths_are_forwarded_absolute`, `test_orca_create_failure_falls_back_to_terminal`, `test_neither_orca_nor_terminal_prints_the_command_and_exits_1`, `test_dry_run_runs_nothing_but_git_and_orca_status`, and `test_start_json_written` if it asserts the `orca_version` value.
- `tests/test_status.py`: `test_no_orca_dir_single_unknown_line_and_no_shell_out`, `test_orca_manual_running_is_ok`, `test_orca_full_access_lines`, `test_orca_not_running_shown_when_settings_ok`, `test_facts_keys_and_todays_lines` (it asserts `set(f["checks"]) == {"orca", "cc-switch"}`), and `test_bw_status_without_orca_or_ccswitch_exits_0` (it asserts `"orca: unknown — "` in stdout; that test writes a config file, so add `"canvas": "orca"` there).
- `tests/test_updates.py:182`: the `update_all` call with a `tools.orca` entry.
- Before Step 4, run `grep -n "orca" tests/*.py` and check each remaining hit against this list. Tests with an `orca_provider` (for example `tests/test_cockpit_views.py:46`, `tests/test_status.py:127` and `:148`, `tests/test_runner.py:920`) keep working, because `in_use` is true for them.

- [ ] **Step 1: Write the failing tests**

In `tests/test_start.py` (`StartCase`; `self.config` has no `canvas` key and no `orca_provider`; the profile `p` touches plans `pp`, `pk`, and `ps`). Use `FakeRun.argvs()` and the case's `self.ran(run, prefix)` (`tests/test_start.py:75-79`).

```python
def test_terminal_canvas_ignores_a_running_orca(self):
    os.environ["BW_ORCA_SUPPORT"] = str(self.root)  # an Orca support dir exists
    run = FakeRun(orca="running")
    code, out = self.start(run, verify=False, update=False)
    self.assertEqual(code, 0, out)
    self.assertEqual([a for a in run.argvs() if a[0] == "orca"], [])
    self.assertEqual(run.argvs()[-1][:3], ["open", "-a", "Terminal"])   # plan 5 Task 14 reads seat keys before
    self.assertNotIn("orca", out.lower())                                # _workspace and spawns verify by Popen (build ruling, 2026-11-23)

def test_terminal_canvas_dry_run_runs_only_git(self):
    run = FakeRun(orca="running")
    code, out = self.start(run, dry_run=True)
    self.assertEqual({a[0] for a in run.argvs()}, {"git"})   # plan 5 Task 14's dry run reads no seat keys (build ruling, 2026-11-23)
    self.assertIn("would run: open -a Terminal", out)

def test_terminal_canvas_never_reads_the_orca_app_version(self):
    with mock.patch.object(start.orca, "app_version", side_effect=AssertionError("read")):
        code, out = self.start(FakeRun(orca="running"), verify=False, update=False)
    self.assertEqual(code, 0, out)

def test_terminal_canvas_skips_a_configured_orca_tool(self):
    self.config["tools"]["orca"] = {"version": ["orca", "--version"], "update": ["orca", "self-update"]}
    for dry_run in (False, True):
        run = FakeRun(orca="running")
        self.start(run, dry_run=dry_run, verify=False, update=True)
        self.assertEqual([a for a in run.argvs() if a[0] == "orca"], [], dry_run)

def test_terminal_canvas_with_an_orca_plan_reads_quota_but_opens_terminal(self):
    self.config["plans"]["pp"]["orca_provider"] = "codex"  # pp is lead lp's plan, in profile p
    run = FakeRun(orca="running")
    self.start(run, verify=False, update=False)
    self.assertEqual(self.ran(run, CREATE), [])
    self.assertTrue(self.ran(run, ["orca", "account", "list"]))
    self.assertEqual(run.argvs()[-1][:3], ["open", "-a", "Terminal"])
```

Make `FakeRun` answer `orca account list --json` from `tests/fixtures/orca/account_list_ok.json` if it does not already.

In `tests/test_status.py`:

```python
def test_terminal_canvas_with_orca_installed_has_no_orca_line_and_no_shell_out(self):
    self.make_orca("settings_manual.json")
    run = fake_run("")
    lines = status.render(status.facts(self.config, self.state, time.time(), run=run))
    self.assertFalse([ln for ln in lines if ln.startswith("orca")], lines)
    self.assertTrue(any(ln.startswith("cc-switch: ") for ln in lines), lines)
    self.assertEqual(run.calls, [])
```

In `tests/test_probes.py` (`RefreshTest`):

```python
def test_no_orca_provider_starts_no_orca_read(self):
    with mock.patch.object(probes.orca, "refresh", side_effect=AssertionError("called")):
        res = probes.refresh(self.config_without_orca(), self.state, NOW, run=no_run, fetch=fake_fetch())
    self.assertEqual(res["notes"], [])
```

Build the config from the class's existing plan helpers, with no plan that sets `orca_provider`.

In `tests/test_updates.py` and `tests/test_registry.py`: a config with `canvas: "terminal"` and `tools.orca = {"version": ["orca", "--version"]}`. `update_all` and `registry.build` record no argv that starts with `"orca"`, and `"orca"` is not a key of the `update_all` result.

In `tests/test_runner.py`, a new class `NoOrcaProvider(RunnerCase)`. Its `config()` adds `cfg["plans"]["chatgpt"] = {"billing": "plan", "quota": "none"}` and sets `cfg["leads"]["la"]["plan"] = "chatgpt"`: the shape of `OrcaReRead` without `orca_provider`. The test must be able to fail. Pass `--orca-payload`, so that `tests/fixtures/run_runner.py:141-148` logs every `orca` argv to `orca-calls.jsonl`. Without the flag, the fake returns 127 before it logs.

```python
def test_session_without_orca_provider_makes_no_orca_call(self):
    payload = self.out.parent / "orca-payload.json"
    payload.write_text((HERE / "fixtures" / "orca" / "account_list_ok.json").read_text())
    p = self.start("--orca-payload", str(payload), env={"FAKE_MODE_a": "sleep", "BW_PROBE_SECONDS": "0"})
    self.assertTrue(wait_for(lambda: self.fake("a")))
    time.sleep(1.0)  # many poll boundaries at BW_PROBE_SECONDS=0; OrcaReRead sees 4+ calls in this time
    self.assertIsNone(p.poll(), "the runner exited")
    self.assertEqual(self.orca_calls(), [])
```

End the runner the way `OrcaReRead` does.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_start tests.test_status tests.test_probes tests.test_updates tests.test_registry tests.test_runner -v`
Expected:
- The new `test_start`, `test_status`, `test_updates`, and `test_registry` tests FAIL (an `orca` argv; an `orca:` line).
- The `test_probes` test FAILS too. The `bw-probe-orca` thread starts at `lead/probes.py:219`, the mock raises, and the note `orca read failed (AssertionError)` appears.
- The `test_runner` test can already PASS, because `orca.refresh` returns early with no mapped plan (`lead/orca.py:367-369`). Keep it as a pin.

- [ ] **Step 3: Gate the call sites**

- `lead/start.py`:
  - Tools (`:129`) and the dry-run tool lines (`:258`): iterate `orca.active_tools(config)`.
  - Step 4: `orca_version` is `None` unless `orca.in_use(config)`. Never call `orca.app_version()` otherwise.
  - Step 6: pass `orca.canvas(config)` to `_workspace`. With `"terminal"`, skip `orca.running` and the `orca:` line, and go straight to the command file and `open -a Terminal`.
  - Step 7: print the `orca` check lines only when `orca.in_use(config)`.
  - `_dry_run`: with `"terminal"`, skip `orca.running` and print `would write {path}:` without the `orca: {why};` prefix.
  - Docstring (moderator's call, plan panel: O-e). Step 4: "OpenRouter's key, one Orca read when a plan maps Orca". Step 6: "a Terminal tab through a `.command` file in the state dir; with `canvas: orca`, an Orca pane when Orca runs, else the Terminal tab". Step 7: "the cc-switch checks, and the Orca checks when Orca is in use". The last line: "`--dry-run` runs only git (and `orca status` when the canvas is Orca)".
- `lead/status.py`: `facts` adds `"orca"` to `checks` only when `orca.in_use(config)`. `render` loops over `[t for t in ("orca", "cc-switch") if t in f["checks"]]`.
- `lead/probes.py`: start the `bw-probe-orca` thread only when a chosen plan sets `orca_provider`.
- `lead/updates.py:129` and `lead/registry.py:145`: read `orca.active_tools(config)`.
- `lead/runner.py:765`: no behaviour change; `orcamod.refresh` already returns before any call. Add one comment line that says so.
- `bin/bw` docstring (O-e): `bw start` — "open it in a Terminal tab (an Orca pane with `canvas: orca`)"; `bw status` — "cc-switch checks, and Orca checks when Orca is in use"; `bw probe` — "read quota now (OpenRouter key; Orca for mapped plans; agy awaits its shape)".

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS, every test, the updated `canvas: orca` tests included.

- [ ] **Step 5: Regenerate the usage map**

Run: `python3 docs/usage-map/generate.py`
Expected: exits 0; `docs/usage-map/data.generated.js` carries the new `bin/bw` lines.

- [ ] **Step 6: Commit**

```bash
git add lead/start.py lead/status.py lead/probes.py lead/updates.py lead/registry.py lead/runner.py bin/bw tests/test_start.py tests/test_status.py tests/test_probes.py tests/test_updates.py tests/test_registry.py tests/test_runner.py docs/usage-map/data.generated.js
git commit -m "feat: the terminal canvas makes no orca calls"
```

---

### Task 3: The example config without Orca

**Files:**
- Modify: `balancewheel.example.json`
- Modify: `CHANGELOG.md` (`[Unreleased]`), `docs/usage-map/data.generated.js` (regenerated)
- Test: `tests/test_config_v3.py:240-250` (replace `test_example_has_orca_plans_without_warnings`), `tests/test_start.py`, `tests/test_status.py`

**Interfaces:**
- Consumes: `canvas` (Task 1), the gates (Task 2).
- Produces: an example config where `orca.in_use(config)` is `False`.

The example changes:
- Add `"canvas": "terminal"` at the top level. The key is explicit, so the Task 1 inference never applies to the example (moderator's call, plan panel: O-a).
- Remove `tools.orca`.
- Remove `orca_provider` and `orca_windows` from **every** plan. The spec names only `chatgpt-plan` and `kimi-plan`. But `claude-plan` (`orca_provider: claude`, the `fableWeekly` pool) and `google-plan` (`orca_provider: antigravity`) also make Orca calls, which breaks the zero-calls rule for the example.
- `chatgpt-plan`: `"quota": "none"` in this task. Task 5 changes it to `"cli"`, when that source exists.
- `kimi-plan`: `"quota": "none"`.
- `claude-plan` stays `statusline`. `google-plan` stays `probe`; its agy probe reads unknown until its shape is known, so the plan runs on observed caps.

What the example loses, stated in the CHANGELOG entry and in Task 4's docs (moderator's call, plan panel: O-e): the Codex and Kimi readings, the Claude pool (`fableWeekly`), the Gemini reading (Orca's `antigravity` entry was the only working one), and Orca's notifications.

- [ ] **Step 1: Write the failing tests**

In `tests/test_config_v3.py`, replace `test_example_has_orca_plans_without_warnings` with:

```python
def test_example_uses_the_terminal_canvas_and_no_orca(self):
    c = cfg.load_config(str(HERE.parent / "balancewheel.example.json"))
    self.assertEqual(c["warnings"], [])
    self.assertEqual(c["canvas"], "terminal")
    self.assertNotIn("orca", c["tools"])
    pl = c["plans"]
    self.assertEqual({n: p["orca_provider"] for n, p in pl.items()}, {n: None for n in pl})
    self.assertEqual(pl["kimi-plan"]["quota"], "none")
    self.assertEqual(pl["claude-plan"]["quota"], "statusline")
    self.assertEqual((pl["google-plan"]["quota"], pl["google-plan"]["probe"]), ("probe", "agy"))
    self.assertEqual(pl["openrouter"]["probe"], "openrouter")
```

In `tests/test_start.py`, a new test that loads the example (with `state_dir` pointed at the temp state dir) and runs `start(..., dry_run=True)` with `FakeRun(orca="running")` and `BW_ORCA_SUPPORT` at an existing dir. Assert that no argv starts with `"orca"`.

In `tests/test_status.py`, the same for `status.facts` on the example: `run.calls` holds no argv that starts with `"orca"`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_config_v3 tests.test_start tests.test_status -v`
Expected: FAIL. The example has `tools.orca` and `orca_provider` values and no `canvas` key, so Task 1 infers `canvas: "orca"`, and the start and status tests see `orca` argvs.

- [ ] **Step 3: Edit `balancewheel.example.json`** as the list above says. Keep its key order and two-space indent.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS, every test.

- [ ] **Step 5: CHANGELOG and usage map**

Under `[Unreleased]`, `### Changed`: "**The example config leaves Orca out.** `canvas: terminal`; no `tools.orca`; no plan sets `orca_provider` or `orca_windows`; `chatgpt-plan` and `kimi-plan` move to `quota: none` (observed caps only). Without Orca, the example has no Codex or Kimi reading, no `fableWeekly` pool, no Gemini reading (`google-plan` runs on observed caps until agy's probe lands), and no Orca notifications. `docs/orca.md` shows how to add Orca back." Run `python3 docs/usage-map/generate.py`.

- [ ] **Step 6: Commit**

```bash
git add balancewheel.example.json tests/test_config_v3.py tests/test_start.py tests/test_status.py CHANGELOG.md docs/usage-map/data.generated.js
git commit -m "feat: the example config uses terminal tabs and leaves orca out"
```

---

### Task 4: The docs present Orca as one possibility

**Files:**
- Modify: `README.md:132`, `:340`, `:344`, `:429`, `:439-449`, `:458-493`, `:523-534`, `:597`
- Modify: `docs/orca.md` (preamble, `:20`, "What we stopped building" `:49`, "Without Orca" `:223`)
- Modify: `docs/stack.md:27` (Orca's row) and its preamble
- Modify: `docs/architecture.md:19`, `:107`
- Modify: `CLAUDE.md:65`
- Modify: `docs/usage-map/infra.js:69-72`, `:126-127`, `:315`, `:325`; `docs/usage-map/data.generated.js` (regenerated)

**Interfaces:**
- Consumes: the behaviour of Tasks 1–3, as built.
- Produces: no code.

What each doc says after this task:
The loss list, used word for word wherever a doc says what bw loses without Orca (moderator's call, plan panel: O-e): **the canvas pane, Orca's notifications ("an agent is waiting"), the Codex and Kimi quota readings, the Claude per-model pool (`fableWeekly`), and the Gemini reading** (Orca's `antigravity` entry; agy's own probe still awaits its shape).

- **README.** `bw start` opens a Terminal tab by default. `canvas: "orca"` opens an Orca pane instead. A schema-3 config with no `canvas` key keeps Orca when it has `tools.orca` or an `orca_provider`. `--dry-run` runs only git (and `orca status` when the canvas is Orca). The quota sources list keeps `orca` as an opt-in source. The heading at `:528` becomes "Where you watch it: a Terminal tab (Orca is one option)". It gives the loss list as what Orca adds. Row `:132` stays, but "the recommended canvas" becomes "an optional canvas".
- **`docs/orca.md`.** Add to the preamble: "Revised 2026-11-22: Orca is optional, not the default canvas; `canvas` defaults to `terminal`." "Default canvas" (`:20`) becomes "an optional canvas". "What we stopped building" gives the loss list; Kimi and Gemini plans then use observed caps. "Without Orca" becomes the default path and moves above "How to integrate". "How to integrate" gains the three config lines that turn Orca on: `"canvas": "orca"`, `tools.orca`, and a plan's `orca_provider` (and `orca_windows` for a pool).
- **`docs/stack.md`.** Orca's row: "optional canvas", with "2026-11-22: no longer the default; see `docs/orca.md`". Add to the preamble: "Revised 2026-11-22: Orca is an optional canvas."
- **`docs/architecture.md`.** "Orca is the recommended one" (`:107`) becomes "a Terminal tab by default; Orca is one option". The dated line at `:19` keeps its history; add after it: "Revised 2026-11-22: Orca is optional; bw opens a Terminal tab unless `canvas` is `orca`."
- **`CLAUDE.md`.** "`docs/orca.md` — Orca, an optional canvas: …".
- **`infra.js`** (O-e). The `orca` node (`:69-72`): `sub: 'Optional canvas (off by default)'`; its `desc` says bw opens a Terminal tab unless `canvas` is `orca`, and that every sandbox still denies Orca's control channel. The tour step at `:315`: "The lead opens in a Terminal tab (or an Orca pane with canvas: orca)." The tour step at `:325`: "The statusline (or a probe, the CLI's output, Orca when mapped, or bw cap) records that the plan is capped." Task 5 adds `cli` to the quota node at `:126`.

Do not describe the `cli` source here. Tasks 5 and 6 add it.

- [ ] **Step 1: Edit the docs** as the list above says. Use ASD-STE100 style: short sentences, active voice, one fact per sentence.

- [ ] **Step 2: Check the words**

Run: `grep -n -i "recommended.*orca\|orca.*recommended\|recommended canvas\|default canvas" README.md CLAUDE.md docs/*.md docs/usage-map/infra.js`
Expected: exactly two matches, both in `docs/orca.md`: the preamble line "Revised 2026-11-22: Orca is optional, not the default canvas" and the 2026-10-24 amendment title "Orca as the default canvas" at `:51`. A match anywhere else is text this task missed: reword it.

Run: `grep -n "Revised 2026-11-22" docs/orca.md docs/stack.md docs/architecture.md`
Expected: exactly one match per file, the lines this task added.

Run: `grep -n -i "orca" docs/usage-map/infra.js`
Expected: every match says Orca is optional or names it as one choice; none says Orca is the default or the first choice.

- [ ] **Step 3: Regenerate the usage map and run the suite**

Run: `python3 docs/usage-map/generate.py && python3 -m unittest discover -s tests -v`
Expected: both exit 0.

- [ ] **Step 4: Commit**

```bash
git add README.md docs/orca.md docs/stack.md docs/architecture.md CLAUDE.md docs/usage-map/infra.js docs/usage-map/data.generated.js
git commit -m "docs: orca is one possible canvas, terminal tabs are the default"
```

---

### Task 5: The `cli` quota source

**Files:**
- Modify: `metrics/config.py:57` (`QUOTA_SOURCES`), `:66` (`ROTATION_DEFAULTS`), `_validate_plans` (`:134-183`, gains `version`), `load_config` (the schema and minimum checks)
- Modify: `lead/quota.py:26-28`, `:120-146` (`_observation_stale`, `_fresh`), `:148-175` (`arbitrate`), `:234-241` (`read_quota`), plus two new functions
- Modify: `lead/status.py:40-44` (the direct `arbitrate` call)
- Modify: `balancewheel.example.json` (`chatgpt-plan` → `"quota": "cli"`, `"rotation"` gains `"cli_fresh_min": 120`)
- Modify: `README.md` (quota sources, `:458-476`), `docs/usage-map/infra.js:126-127`, `CHANGELOG.md`, `docs/usage-map/data.generated.js`
- Create: `tests/fixtures/quota/codex_rate_limits_ok.json`, `tests/fixtures/quota/codex_rate_limits_relative.json` (synthetic)
- Test: `tests/test_quota.py`, `tests/test_config_v2.py:52-55`, `tests/test_config_v3.py` (`PlanOrcaAndProbeKeys`, `SchemaV3`)

**Interfaces:**
- Consumes: `quota.write_snapshot(state, plan, source, snap, now)` (exists).
- Produces:
  - `quota.SOURCES == ("statusline", "probe", "orca", "cli")`; `quota.ORDER["cli"] == ["cli", "orca"]`.
  - `quota.parse_cli_rate_limits(rate_limits, now: float) -> dict | None` — a snapshot `{"windows": [...], "observed_at": now}`, or `None` when the input breaks any rule below.
  - `quota.record_cli(state: str, plan: str, rate_limits, now: float) -> dict | None` — writes `quota/<plan>.cli.json` when the parse gives a snapshot. Otherwise it writes nothing, so an existing good snapshot stays byte-identical, and returns `None`. It never records, extends, or clears a cap (as `orca.refresh`).
  - `quota.arbitrate(sources, plan_cfg, now, probe_every_min, cli_fresh_min=120)`.
  - `quota.CLI_WINDOWS = {300: "five_hour", 10080: "seven_day"}`; `quota.CLI_MAX_HORIZON_S = 400 * 86400`.
  - Resolved `config["rotation"]["cli_fresh_min"]`, an int of at least 1.
  - `metrics.config._validate_plans(obj, version)` (was `_validate_plans(obj)`).

The input shape. Project 1's Codex adapter returns `usage(stdout_lines) -> {tokens_in, tokens_out, cost_usd, rate_limits}`, each optional (spec, "The adapter"). This plan assumes `rate_limits` is the Codex token-count event's `rate_limits` object, passed through unchanged. The spec marks that shape **to verify**: "primary and secondary windows, used percent, reset time". The parser reads:

```json
{"primary":   {"used_percent": 42.0, "window_minutes": 300,   "resets_at": 1900000000},
 "secondary": {"used_percent": 7.5,  "window_minutes": 10080, "resets_in_seconds": 86400}}
```

The rules. The parser rejects the whole input, and returns `None`, on any break (moderator's call, plan panel: O-c). It never keeps part of a reading.
- The input is a dict. Only the keys `primary` and `secondary` are read; other keys are ignored.
- `primary` is required. `secondary` is optional. Each window present is a dict.
- `window_minutes` is an int key of `CLI_WINDOWS` (300 or 10080). Any other value is an unknown unit: reject.
- `used_percent` is a non-bool, finite number (`math.isfinite`) with `0 <= used_percent <= 100`.
- Reset: `resets_at`, when present, is a non-bool finite number with `now < resets_at <= now + CLI_MAX_HORIZON_S`. Epoch milliseconds fail this bound. Else `resets_in_seconds`, when present, is a non-bool finite number with `0 < resets_in_seconds <= CLI_MAX_HORIZON_S`, and `resets_at = now + resets_in_seconds`. A window with neither key has `resets_at: None`. A key that is present but breaks its bound rejects the input.
- Two windows with the same name reject the input.

Review Focus 4 and the tests below match these rules.

- [ ] **Step 1: Write the failing tests**

In `tests/test_quota.py`, add a fixture reader beside `fx()` (`tests/test_quota.py:16`), which reads only `fixtures/statusline`: `QFX = HERE / "fixtures" / "quota"` and `def qfx(name): return json.loads((QFX / name).read_text())`. Then a new class `CliSource` with a temp `self.state` (as `Quota` makes one), using `NOW` (`1_899_990_000.0`), `M`, `known`, and `plan_cfg` from the module:

```python
def win(used=42.0, minutes=300, **reset):
    return dict({"used_percent": used, "window_minutes": minutes}, **(reset or {"resets_at": NOW + 3600}))

def test_parse_maps_windows_and_both_reset_spellings(self):
    s = quota.parse_cli_rate_limits(qfx("codex_rate_limits_ok.json"), NOW)
    self.assertEqual([w["name"] for w in s["windows"]], ["five_hour", "seven_day"])
    self.assertEqual((s["windows"][0]["used_pct"], s["windows"][0]["resets_at"]), (42.0, 1_900_000_000))
    self.assertEqual(s["observed_at"], NOW)
    r = quota.parse_cli_rate_limits(qfx("codex_rate_limits_relative.json"), NOW)
    self.assertEqual(r["windows"][1]["resets_at"], NOW + 86400)

def test_primary_alone_is_enough_and_no_reset_is_none(self):
    s = quota.parse_cli_rate_limits({"primary": {"used_percent": 5, "window_minutes": 300}}, NOW)
    self.assertEqual(s["windows"], [{"name": "five_hour", "used_pct": 5.0, "resets_at": None}])

def test_record_cli_writes_the_cli_snapshot_file(self):
    quota.record_cli(self.state, "chatgpt-plan", qfx("codex_rate_limits_ok.json"), NOW)
    self.assertTrue(os.path.exists(os.path.join(self.state, "quota", "chatgpt-plan.cli.json")))
    self.assertIn("cli", quota.read_sources(self.state)["chatgpt-plan"])

def test_no_rate_limits_writes_nothing(self):
    self.assertIsNone(quota.record_cli(self.state, "p", None, NOW))
    self.assertFalse(os.path.exists(os.path.join(self.state, "quota", "p.cli.json")))

BAD = [
    {}, [], "x", {"primary": "full"},
    {"secondary": win(minutes=10080)},                                   # missing required window
    {"primary": win(used=True)}, {"primary": win(used="42")},
    {"primary": win(used=float("nan"))}, {"primary": win(used=float("inf"))},
    {"primary": win(used=-1)}, {"primary": win(used=100.5)},
    {"primary": win(minutes=60)}, {"primary": win(minutes="300")},      # unknown units
    {"primary": win(resets_at=NOW)}, {"primary": win(resets_at=NOW - 60)},  # past reset
    {"primary": win(resets_at=(NOW + 3600) * 1000)},                     # epoch milliseconds
    {"primary": win(resets_in_seconds=0)}, {"primary": win(resets_in_seconds=-5)},
    {"primary": win(), "secondary": win(minutes=300)},                   # the same name twice
    {"primary": win(), "secondary": win(used=None, minutes=10080)},      # one bad window rejects all
]

def test_malformed_rate_limits_write_nothing(self):
    for bad in BAD:
        self.assertIsNone(quota.parse_cli_rate_limits(bad, NOW), bad)
        self.assertIsNone(quota.record_cli(self.state, "p", bad, NOW), bad)
    self.assertFalse(os.path.exists(os.path.join(self.state, "quota", "p.cli.json")))

def test_bad_input_never_overwrites_a_good_snapshot(self):
    quota.record_cli(self.state, "p", qfx("codex_rate_limits_ok.json"), NOW)
    path = os.path.join(self.state, "quota", "p.cli.json")
    before = open(path, "rb").read()
    for bad in BAD:
        quota.record_cli(self.state, "p", bad, NOW + 60)
    self.assertEqual(open(path, "rb").read(), before)

def test_record_cli_never_writes_a_cap(self):
    full = {"primary": {"used_percent": 100.0, "window_minutes": 300, "resets_at": NOW + 600}}
    quota.record_cli(self.state, "p", full, NOW)
    self.assertEqual(quota.read_caps(self.state, NOW), {})

def test_cli_freshness_uses_cli_fresh_min_not_probe_interval(self):
    cfg = plan_cfg("cli")
    fresh = quota.arbitrate({"cli": known(10, NOW - 40 * M)}, cfg, NOW, 15, cli_fresh_min=120)
    self.assertEqual(fresh["source"], "cli")
    stale = quota.arbitrate({"cli": known(10, NOW - 121 * M)}, cfg, NOW, 15, cli_fresh_min=120)
    self.assertTrue(stale["unknown"])
    self.assertIn("cli: stale", stale["reason"])

def test_cli_past_its_reset_reads_unknown(self):
    a = quota.arbitrate({"cli": known(10, NOW - 60, resets_in=-1)}, plan_cfg("cli"), NOW, 15)
    self.assertTrue(a["unknown"])

def test_cli_order_falls_to_orca_only_with_a_provider(self):
    stale = known(10, NOW - 121 * M)
    a = quota.arbitrate({"cli": stale, "orca": known(90, NOW - 60)}, plan_cfg("cli", "codex"), NOW, 15)
    self.assertEqual(a["source"], "orca")
    b = quota.arbitrate({"cli": stale, "orca": known(90, NOW - 60)}, plan_cfg("cli"), NOW, 15)
    self.assertTrue(b["unknown"])
```

The fixtures hold synthetic objects. `codex_rate_limits_ok.json` is the shape block above with `resets_at: 1900000000` (`NOW + 10000`) on `primary` and `resets_at: 1900500000` on `secondary`. `codex_rate_limits_relative.json` is the same with `resets_in_seconds: 86400` in place of `secondary`'s `resets_at`. Every absolute reset lies after `NOW` and within `CLI_MAX_HORIZON_S` of it.

In `tests/test_config_v3.py`, class `PlanOrcaAndProbeKeys` (it has `self.plan(...)` and `self.rejects(...)`, `tests/test_config_v3.py:179-185`):

```python
def test_cli_quota_source_accepted(self):
    self.assertEqual(load_obj(self.plan(quota="cli"))["plans"]["plan-b"]["quota"], "cli")
```

In class `SchemaV3` (it has `rejects`; the fixture is `v3()`):

```python
def test_cli_fresh_min_default_and_minimum(self):
    self.assertEqual(load_obj(v3())["rotation"]["cli_fresh_min"], 120)
    o = v3(); o["rotation"] = {"cli_fresh_min": 0}
    self.rejects(o, "rotation.cli_fresh_min: must be at least 1")

def test_example_chatgpt_plan_reads_cli(self):
    c = cfg.load_config(str(HERE.parent / "balancewheel.example.json"))
    self.assertEqual(c["plans"]["chatgpt-plan"]["quota"], "cli")
```

In `tests/test_config_v2.py` (moderator's call, plan panel: O-h):

```python
def test_cli_quota_needs_schema_3(self):
    o = v2(); o["plans"]["plan-a"]["quota"] = "cli"
    self.rejects(o, 'plans.plan-a.quota: "cli" needs schema_version 3')

def test_cli_fresh_min_needs_schema_3(self):
    o = v2(); o["rotation"]["cli_fresh_min"] = 120
    self.rejects(o, "rotation.cli_fresh_min: needs schema_version 3")
```

In `tests/test_config_v2.py:54` (`test_rotation_defaults_and_override`), the expected `rotation` dict gains `"cli_fresh_min": 120`: the resolved default is present at every schema; only setting the key needs schema 3.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_quota tests.test_config_v2 tests.test_config_v3 -v`
Expected: FAIL — `AttributeError: ... 'parse_cli_rate_limits'`, `plans.plan-b.quota: must be one of`, `KeyError: 'cli_fresh_min'`, and the two schema-2 tests (their messages differ).

- [ ] **Step 3: Implement the source**

- `metrics/config.py`: add `"cli"` to `QUOTA_SOURCES`; add `"cli_fresh_min": 120` to `ROTATION_DEFAULTS`. `_validate_plans(obj, version)`: `_expect(quota != "cli" or version >= 3, f'{kp}.quota: "cli" needs schema_version 3')`. Before `_validate_defaults(obj, "rotation", ...)`: `_expect(version >= 3 or "cli_fresh_min" not in (obj.get("rotation") or {}), "rotation.cli_fresh_min: needs schema_version 3")` (guard a non-dict `rotation` the way `_validate_defaults` reports it). After it: `_expect(rotation["cli_fresh_min"] >= 1, "rotation.cli_fresh_min: must be at least 1")`.
- `lead/quota.py`: `SOURCES` and `ORDER` as the Interfaces block says. `parse_cli_rate_limits` follows the rules above, with `math.isfinite`. `_observation_stale` and `_fresh` gain `cli_fresh_min=120`; for `src == "cli"` the age limit is `cli_fresh_min * 60`, for the other non-statusline sources it stays `2 * probe_every_min * 60`. `arbitrate` passes `cli_fresh_min` through. `read_quota` reads `rotation.cli_fresh_min` (default 120) and passes it. Add `parse_cli_rate_limits` and `record_cli` (`record_cli` calls `write_snapshot(state, plan, "cli", snap, now)`). Update the module docstring: the `cli` source records what a Codex seat run prints.
- `lead/status.py:43`: pass `cli_fresh_min=rot.get("cli_fresh_min", 120)`.
- `balancewheel.example.json`: `chatgpt-plan` `"quota": "cli"`; `"rotation"` gains `"cli_fresh_min": 120`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS, every test.

- [ ] **Step 5: Docs, CHANGELOG, usage map**

- `README.md` quota sources: add `cli` — "what a Codex seat run prints (`rate_limits`); fresh for `rotation.cli_fresh_min` minutes (120); a `cli` plan reads `cli`, then Orca when `orca_provider` is set". Say plainly: the reading updates only when a real Codex seat runs; a seat attack (`bw seat verify`, `bw start`) runs in its own state dir and feeds no quota; a Codex lead's TUI output is not read; between seat runs rotation uses observed caps. The source needs schema 3.
- `infra.js:126`: `sub: 'statusline · probe · cli · Orca'`.
- `CHANGELOG.md` `[Unreleased]`, `### Added`: "**Quota source `cli`** and **`rotation.cli_fresh_min`** (default 120), schema 3 only." `### Changed`: "`chatgpt-plan` in the example reads `cli`."
- Run `python3 docs/usage-map/generate.py`.

- [ ] **Step 6: Commit**

```bash
git add metrics/config.py lead/quota.py lead/status.py balancewheel.example.json tests/test_quota.py tests/test_config_v2.py tests/test_config_v3.py tests/fixtures/quota README.md docs/usage-map/infra.js docs/usage-map/data.generated.js CHANGELOG.md
git commit -m "feat: a passive cli quota source for codex plans"
```

---

### Task 6: A Codex seat run records its `cli` reading

**Depends on Project 1 (plan 5).** Start this task only after plan 5 has landed `seats/run.py` (its Task 11), `seats/adapters.py`, and `tests/fixtures/seats/codex/`. The contract this task consumes is plan 5's "(P2 contract)" blocks (moderator's call, plan panel: O-g):

- `run_seat(config, state, seat, prompt, *, target=None, brief=False, brief_file=None, resume=False, session_id=None, deep=False, fallback=True, out=None, transcript=None, attack=False, run=proc.run, now=time.time, say=None, _standin_for=None) -> SeatResult`. `now` is a **callable**; call `now()`. `say` takes one line; `None` means stderr. stdout carries only the seat's answer, so this task never prints.
- `SeatResult` has the field `rate_limits` (plan 5, design decision 11: "Codex `rate_limits` are parsed, not stored ... Writing the `cli` quota snapshot is Project 3").
- The Codex adapter's `usage(stdout_lines) -> dict` returns keys among `tokens_in`, `tokens_out`, `cost_usd`, `rate_limits`. This plan assumes `rate_limits` is the raw object from the last Codex token-count event (Task 5's shape block).
- The usage event row does **not** carry `rate_limits`. This task does not add it.
- Step 8 of `run_seat`: on a usage-limit error, the run arms the seat cap, appends its own `ran: true` row, and, when `fallback` and `_standin_for is None`, re-enters step 2 once for the stand-in.
- Attack isolation (plan 5, design decision 5): an attack run (`attack=True`) runs with an isolated state dir `<run>/state/`, never the user's `state_dir`.

Where the call goes. Record the reading for **each run that produced it**, in step 8, right after the adapter's `usage()` parse and before any stand-in re-entry. A capped Codex run then keeps its own reading (likely at or near 100 %), and the stand-in, which has its own runtime, never writes over it. Skip the call when `attack` is true (moderator's call, plan panel: O-f). Only real seat runs feed the `cli` source; a seat attack's reading stays in its isolated state dir, and bw never copies it across state dirs.

If plan 5 shipped another shape, adapt only the call site in Step 3. `quota.parse_cli_rate_limits` stays the one parser.

**Files:**
- Modify: `seats/run.py` (step 8, the parse)
- Create: `tests/fixtures/seats/codex/ok_rate_limits.{stdout,stderr}` and `tests/fixtures/seats/codex/usage_limit_rate_limits.{stdout,stderr}` (synthetic; in plan 5's per-runtime layout). The first is plan 5's `codex/ok` with one token-count event that carries Task 5's `codex_rate_limits_ok.json` object. The second is `codex/usage_limit` plus that event with `used_percent: 100` on `primary`. Do not assume plan 5's `codex/ok` carries `rate_limits` or lacks it; this task's "without" test uses a copy, `codex/ok_no_rate_limits.{stdout,stderr}`, with the event's `rate_limits` key removed.
- Test: `tests/test_seat_run.py` (plan 5's module)
- Modify: `docs/orca.md` ("What we stopped building"), `README.md` (one line in the `bw seat` section), `CHANGELOG.md`, `docs/usage-map/data.generated.js`

**Interfaces:**
- Consumes: `quota.record_cli(state, plan, rate_limits, now) -> dict | None` (Task 5); plan 5's `run_seat`, `SeatResult.rate_limits`, the adapter's `usage()` result, and the resolved seat (`config["seats"][seat]["runtime"]`, `["plan"]`).
- Produces: after a real (non-attack) Codex seat run whose output carries valid `rate_limits`, the file `state_dir/quota/<seat plan>.cli.json`.

- [ ] **Step 1: Write the failing tests**

With plan 5's `FakeRun` (it returns a fixture `Result` per argv), its config helper, and a fixed clock `now=lambda: NOW`, where `NOW` lies before the fixtures' resets. A codex seat `astra` on plan `chatgpt-plan`, with `fallback` seat `gem` on another runtime, as in plan 5's tests.

```python
def test_codex_run_with_rate_limits_writes_the_cli_snapshot(self):
    r = run_seat(self.config, self.state, "astra", "review", run=self.fake("codex/ok_rate_limits"),
                 now=lambda: NOW, say=self.lines.append)
    self.assertEqual(r.exit_code, 0)
    snap = read_json(os.path.join(self.state, "quota", "chatgpt-plan.cli.json"))
    self.assertEqual([w["name"] for w in snap["windows"]], ["five_hour", "seven_day"])
    self.assertEqual(snap["observed_at"], NOW)

def test_codex_run_without_rate_limits_writes_nothing(self):
    run_seat(self.config, self.state, "astra", "review", run=self.fake("codex/ok_no_rate_limits"), now=lambda: NOW)
    self.assertFalse(os.path.exists(os.path.join(self.state, "quota", "chatgpt-plan.cli.json")))

def test_seat_without_a_plan_writes_nothing(self):
    self.config["seats"]["astra"]["plan"] = None
    run_seat(self.config, self.state, "astra", "review", run=self.fake("codex/ok_rate_limits"), now=lambda: NOW)
    self.assertFalse(os.path.isdir(os.path.join(self.state, "quota")))

def test_a_capped_codex_run_records_its_reading_before_the_stand_in(self):
    r = run_seat(self.config, self.state, "astra", "review", now=lambda: NOW,
                 run=self.fake({"codex": "codex/usage_limit_rate_limits", "<gem runtime>": "<gem ok fixture>"}))
    self.assertEqual((r.seat, r.stand_in_for), ("gem", "astra"))
    snap = read_json(os.path.join(self.state, "quota", "chatgpt-plan.cli.json"))
    self.assertEqual(snap["windows"][0]["used_pct"], 100.0)

def test_an_attack_run_records_nothing(self):
    # through plan 5's attack path (attack=True with a valid token in the run's state dir)
    self.run_attack_seat("astra", fixture="codex/ok_rate_limits")
    self.assertFalse(os.path.exists(os.path.join(self.state, "quota", "chatgpt-plan.cli.json")))
    self.assertFalse(os.path.exists(os.path.join(self.attack_state, "quota", "chatgpt-plan.cli.json")))

def test_a_bad_quota_write_never_fails_the_run(self):
    with mock.patch.object(run_mod.quota, "record_cli", side_effect=OSError("disk")):
        r = run_seat(self.config, self.state, "astra", "review", run=self.fake("codex/ok_rate_limits"),
                     now=lambda: NOW, say=self.lines.append)
    self.assertEqual(r.exit_code, 0)
    self.assertIn("bw seat: cli quota not written (OSError)", self.lines)
    self.assertNotIn("disk", " ".join(self.lines))
```

Use plan 5's helper names for `self.fake(...)`, the per-runtime fixture map, the attack-token setup (`self.run_attack_seat`, `self.attack_state`), and the stand-in seat's fixture. The names above show the intent; the assertions are fixed.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_seat_run -v`
Expected: `test_codex_run_with_rate_limits_writes_the_cli_snapshot`, `test_a_capped_codex_run_records_its_reading_before_the_stand_in`, and `test_a_bad_quota_write_never_fails_the_run` FAIL (no snapshot; no `say` line). The other three can PASS; keep them as pins.

- [ ] **Step 3: Implement the call in `seats/run.py`**

In step 8, right after the adapter's `usage()` result is known for this run, and before the step-2 re-entry: when `not attack`, the seat's runtime is `codex`, the seat has a `plan`, and `usage.get("rate_limits")` is present, call `quota.record_cli(state, seat_cfg["plan"], usage["rate_limits"], now())`. Catch `OSError` and `ValueError`, and send one line through `say` (`bw seat: cli quota not written (<type name>)`), never the exception text. Keep the run's exit status and reason.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS, every test.

- [ ] **Step 5: Docs, CHANGELOG, usage map**

- `docs/orca.md`, "What we stopped building": "Without Orca, a Codex plan reads the `cli` source: what a real Codex seat run prints. Its numbers are only as fresh as the last Codex seat run. A seat attack runs in its own state dir and feeds no quota."
- `README.md`, `bw seat` section: one line that a real Codex seat run records the plan's `cli` quota, and a seat attack does not.
- `CHANGELOG.md` `[Unreleased]`: "A Codex seat run writes `quota/<plan>.cli.json` when its output carries valid `rate_limits`; attack runs write none."
- Run `python3 docs/usage-map/generate.py`.

- [ ] **Step 6: Commit**

```bash
git add seats/run.py tests/test_seat_run.py tests/fixtures/seats/codex docs/orca.md README.md CHANGELOG.md docs/usage-map/data.generated.js
git commit -m "feat: a codex seat run records its plan's cli quota"
```

---

## Verification after the last task

- `python3 -m unittest discover -s tests -v` passes.
- With the example config, a `bw start --dry-run`, `bw status`, and `bw probe` run make no `orca` call (Task 3 tests). With `canvas: terminal` and a configured `tools.orca`, `bw start` with updates and `bw registry` make none either (Task 2 tests).
- A schema-3 config without `canvas` that has `tools.orca` or an `orca_provider` still opens Orca panes (Task 1 tests).
- `grep -n "orca" tests/*.py` shows no test that expects an Orca pane or an `orca status` call without `canvas: "orca"` or an `orca_provider`.
- `git diff main --stat` touches no file in `lead/sandbox.py` and no fixture with real data.
- The live check of Codex `rate_limits` in `codex exec --json` is still open (spec, "Open risks"). If the field is absent on the installed version, Codex plans stay on observed caps, as Kimi plans do. Record the result in `docs/orca.md`.
