# Live Pane, Run Events, and the Local Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

*Written 2026-11-27. Line references are to `feat/live-pane` at `09a3408`. Where this plan departs from
the spec, the departure is a numbered Plan decision (PD-n) below.*

**Goal:** Every seat run writes one start row and one end row; `bw live --json` reports what runs now and
what ran in a window; a `local` config section makes a user's own seats first-class; and a clean-room
Claude Code plugin, installed by bw, draws all of it beside the chat.

**Architecture:** `metrics/store.py` gains `run` rows and a backward chunked reader. Run rows are built in
one module (`lead/runs.py`) and written by `bw seat` (`seats/run.py`), by `bw run` (`lead/run_cli.py`), and,
for a contained lead, by the panel service from request files. `bw live` (`lead/live.py`) is a read-only join
of the logs, panel state, pending requests, and seat session files (`seats/live.py`); the plugin in
`claude/live-pane/` polls it, and `lead/claude_pane.py` installs and updates the plugin.

**Tech Stack:** Python 3.9+ standard library only (`typing.Optional`, no `X | None`), `unittest`; the plugin
is TSX on Claude Code's plugin API with no run-time npm dependency, tested with `claude plugin test`.

**Spec:** `docs/superpowers/specs/2026-11-27-live-pane-design.md`

## Global Constraints

- Clean-room: the builder writes from the spec, this plan, the code in this repo, and public Claude Code and seat-CLI documentation only. The builder must not open, copy, or paraphrase anything outside this repo, including any private pane, wrapper, or the author's user-level tooling.
- Python 3.9+ standard library only. The plugin has no run-time npm dependency.
- `metrics/store.py` stays the only appender (`store.append`). New rows and fields are additive; existing rows keep their fields.
- Config is strict (`metrics/config.py`): every new key is validated, refused when unknown, and tested.
- Registry health stays in `{verified, pending, capped, unknown}` (`lead/registry.py:33` `HEALTH`).
- `bw start --dry-run` writes nothing and calls no tool beyond what it calls today. It only prints "would ensure the live pane plugin".
- No model id in an interface, schema, or name. Tests use ids such as `"model-a"`. The pane colors by family words found in the model string, never by id.
- Docs move with behavior in the same commit: `CHANGELOG.md [Unreleased]`, `docs/usage-map/data.generated.js` (run `python3 docs/usage-map/generate.py`), `docs/leads.md`, `docs/stack.md`, and `docs/start/` plus the README Quick start where shown output changes. `setup/paths.txt` gains nothing.
- Fixtures are synthetic and hand-written: no real path, account, session id, project name, or seat output.
- No change to `lead/sandbox.py`, `lead/runtimes.py`, `lead/codepaths.py`, `seats/adapters.py`, or `seats/grants.py`: each is hashed into a containment or seat key (PD-1).
- Tests are hermetic: they set `BW_STATE_DIR`, `BALANCEWHEEL_METRICS_LOG`, `BALANCEWHEEL_CONFIG`, and `BW_CLAUDE_CMD` to temp values. No test installs into the user's real Claude config.
- Commit summaries are lowercase with a `feat:` / `fix:` / `test:` / `docs:` prefix and no AI trailer.

## Plan decisions

- **PD-1. Keys.** `seats/run.py` is in `SEAT_CODE` (`seats/verify.py:32`), so Task 4 resets every seat pass once. `live_usage` goes in a new, unhashed `seats/live.py`, not in `seats/adapters.py` (spec §4), so the reset happens once, not twice. After merge the user runs `bw seat verify --changed`.
- **PD-2. `live_usage` signature.** A new seat session's id is known only after the run, and seat session files sit under `state/seats/homes/<seat>`. So: `live_usage(runtime, home, session_id, dir, start, seat_cfg=None)`; the file is found by dir and start time when `session_id` is None; `home=None` means the CLI's own folder under the real home (local seats).
- **PD-3. `bw run start` records no `pid`.** Its process exits at once, so a pid would make the lane `unknown` at once. Liveness for that form is the `run_max_s` rule.
- **PD-4. `open[].ask`** comes from panel state: a running ask of the same seat in the same panel.
- **PD-5. Panel stand-ins.** The panel passes `--no-fallback` and launches stand-ins itself (`panel/roster.py:76`). `seat_argv` passes a hidden `--stand-in-for SEAT`, which fills only the run row's `stand_in_for`.
- **PD-6. Lead session flag.** `--session` already means seat resume. The lead's id goes in a hidden `--lead-session ID` (12 hex, like `Session.id`, `lead/runner.py:154`).
- **PD-7. Labels.** Round 1: `"<checkpoint> round 1"`. Ask: `"ask <ask id>"`. Plain `bw seat`: `--label TEXT` when given, else the prompt's first line. Every label goes through `safe()` and is cut to 80 characters.
- **PD-8. Schema.** `claude_pane` and `local` join `V3_TOP_KEYS`. A schema 1 or 2 file gets the defaults (`claude_pane: true`, empty `local`).
- **PD-9. `local.seats[].runtime`** (spec §4 uses it; §3 omits it) is an optional key in `RUNTIMES`.
- **PD-10. Version.** New `lead/version.py` `VERSION = "0.2.0-dev.1"`. `plugin.json` and the marketplace entry equal it (tested). Bump the prerelease number in the same commit as any change under `claude/live-pane/`, or installed panes do not update.
- **PD-11. Plugin types.** `claude/live-pane/types/index.d.ts` is the pane's own `bw live` v1 contract (hand-written, committed). The folder the engine generates, `claude/live-pane/.claude-plugin/types/`, is git-ignored.
- **PD-13. Heartbeat (spec §1 gains phase `beat`).** A `bw run` wrap writes `{"type": "run", "phase": "beat", "run_id", "t", "seat", "dir"}` every `run_max_s / 2` while its child runs. So a long run always has a row inside `bw live`'s read window, and the `run_max_s` rule counts from the newest beat, on any host. The start/end form writes no beats.
- **PD-14. Sink order.** `BW_CHECKPOINT` is set for every lead bw starts (`lead/runner.py:168`). `bw run` appends to the log first and writes a request file only when that fails.
- **PD-12. `bw live` on a config error** prints a full v1 document with empty lists, `"complete": false`, and `"error": "config error: …"`, and exits 0. `--dir` outside git resolves to its real path.

## Review Focus

1. A wrapped command that cannot start or dies by a signal (`bw run --seat s -- nosuchcmd`, a child killed by SIGTERM): exit 127 or 128+n, one end row with `reason` `"exit 127"` or `"signal 15"` (Task 5).
2. Tracking that cannot work (no config, an unwritable log dir): the command still runs, its exit status passes through, and stderr gets exactly one `bw run:` line (Task 5).
3. Log edges in the backward reader: a line cut by a chunk edge, a cut last line, a line longer than a chunk, blank lines: `read_tail` gives the same rows as the forward reader for every chunk size from 1 to 64 (Task 1).
4. Runs with old or foreign liveness facts: no `pid`/`host` (older rows), another host, a dead pid: `live` until `run_max_s` then `stale`, `live` while the pid lives, `unknown` when it is dead (Task 7).
5. Config that would make the pane misfire: an empty `match` (it would match every Bash call), a local seat with a bw seat's name, `local.logs` that names the main log or one file twice: refused or de-duplicated (Task 2).

---

### Task 1: Store — `run` rows and the backward chunked reader

**Files:**
- Modify: `metrics/store.py:1-7` (docstring names `run` rows), `:165-234` (`load` adds `"runs"`), add the reader after `read_events` (`:57-79`)
- Test: `tests/test_store.py`

**Interfaces:**
- Produces: `TAIL_CHUNK = 256 * 1024`, `TAIL_CAP = 64 * 1024 * 1024`, `Tail = namedtuple("Tail", "events complete error")`.
- Produces: `row_time(event: dict) -> Optional[float]` — `t` when it is a finite int or float (not bool), else `ts` parsed as `%Y-%m-%dT%H:%M:%SZ` UTC, else None.
- Produces: `written_time(event: dict) -> Optional[float]` — `ts` parsed (the append time), else `row_time`. The reader stops on it, because a row ingested late (Task 6) carries an old `t` at the end of the file.
- Produces: `read_tail(path, since: float, cap: int = TAIL_CAP, chunk: int = TAIL_CHUNK) -> Tail` — event dicts with a string `type` and `row_time >= since`, in file order. It never reads more than `cap` bytes, also when `cap` is not a multiple of `chunk`. `complete` is False only when the cap stopped the read before the cover point and before the start of the file; a file smaller than `cap` is read whole and is `complete`. `t == 0.0` is a valid time. A missing file gives `Tail([], True, None)`; any other `OSError` gives `Tail([], False, "<strerror>")`.
- Produces: `load(path)["runs"]` — every `type == "run"` event, in file order.

- [ ] **Step 1: Write the failing tests** (class `Tail` in `tests/test_store.py`)

```python
def test_read_tail_matches_forward_read_for_every_chunk_size(self):  # Review Focus 3
    rows = [{"type": "run", "phase": "start", "run_id": f"r{i:012x}", "t": 1000.0 + i, "seat": "s"} for i in range(40)]
    rows[15]["label"] = "x" * 300                     # in range, and longer than every small chunk
    body = "\n".join(json.dumps(r) for r in rows) + "\n\n" + '{"type": "run", "t": 99'  # blank line + cut tail
    Path(self.log).write_text(body)
    want = [r for r in rows if r["t"] >= 1010.0]
    for chunk in range(1, 65):
        t = store.read_tail(self.log, 1010.0, chunk=chunk)
        self.assertEqual((t.events, t.complete, t.error), (want, True, None), chunk)

def test_a_late_row_with_an_old_t_does_not_stop_the_read(self):
    rows = [{"type": "run", "t": 1000.0 + i, "ts": store_ts(1000 + i)} for i in range(30)]
    rows.append({"type": "run", "t": 900.0, "ts": store_ts(1030)})   # ingested late: old t, new ts
    # store_ts(n): datetime.fromtimestamp(n, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    Path(self.log).write_text("".join(json.dumps(r) + "\n" for r in rows))
    t = store.read_tail(self.log, 1010.0, chunk=40)
    self.assertEqual([e["t"] for e in t.events], [1000.0 + i for i in range(10, 30)])

def test_read_tail_stops_at_the_cap(self):
    lines = [json.dumps({"type": "usage", "t": 1000.0 + i}) + "\n" for i in range(100)]   # every line is 31 bytes
    Path(self.log).write_text("".join(lines))
    t = store.read_tail(self.log, 0.0, cap=230, chunk=100)   # 230 is not a multiple of 100
    self.assertFalse(t.complete)
    self.assertEqual([e["t"] for e in t.events], [1093.0 + i for i in range(7)])   # only lines whole in the last 230 bytes

def test_a_file_smaller_than_the_cap_is_complete(self):
    Path(self.log).write_text(json.dumps({"type": "usage", "t": 0.0}) + "\n")   # t == 0.0 is a valid time
    t = store.read_tail(self.log, 0.0, cap=10_000, chunk=7)
    self.assertEqual((len(t.events), t.complete), (1, True))

def test_read_tail_missing_and_unreadable(self):
    self.assertEqual(store.read_tail(self.log + ".nope", 0.0), store.Tail([], True, None))
    os.mkdir(self.log)                                 # a directory: open() fails
    t = store.read_tail(self.log, 0.0); self.assertEqual((t.events, t.complete), ([], False)); self.assertTrue(t.error)

def test_row_time_prefers_t_then_ts(self):
    self.assertEqual(store.row_time({"t": 5.5, "ts": "2026-11-27T00:00:00Z"}), 5.5)
    self.assertEqual(store.row_time({"ts": "1970-01-01T00:00:10Z"}), 10.0)
    self.assertIsNone(store.row_time({"t": True})); self.assertIsNone(store.row_time({"ts": "bad"}))

def test_load_keeps_run_rows(self):
    store.append(self.log, {"type": "run", "phase": "start", "run_id": "r0123456789ab", "t": 1.0})
    self.assertEqual([e["run_id"] for e in store.load(self.log)["runs"]], ["r0123456789ab"])
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests.test_store -v`
Expected: FAIL (`AttributeError: module 'store' has no attribute 'read_tail'`).

- [ ] **Step 3: Implement `row_time`, `read_tail`, and the `runs` key in `metrics/store.py`**

The reader carries the cut first piece of each chunk into the next read. Only the outer edges of the whole
read can drop a line (a cut last line fails to parse; a first line before the cover point is out of range).

```python
def read_tail(path, since, cap=TAIL_CAP, chunk=TAIL_CHUNK):
    try:
        f = open(path, "rb")
    except FileNotFoundError:
        return Tail([], True, None)
    except OSError as e:
        return Tail([], False, e.strerror or type(e).__name__)
    out, carry, read, complete = [], b"", 0, True
    with f:
        pos = os.fstat(f.fileno()).st_size
        while pos > 0:
            if read >= cap:
                complete = False                # the cap stopped us before the cover point and the file start
                break
            n = min(chunk, pos, cap - read); pos -= n; f.seek(pos)
            buf = f.read(n) + carry; read += n
            parts = buf.split(b"\n")
            carry, body = (parts[0], parts[1:]) if pos > 0 else (b"", parts)
            oldest = None
            for raw in reversed(body):
                e = _parse_line(raw)            # None for blank, cut, or non-event lines
                if e is None:
                    continue
                out.append(e)
                w = written_time(e)             # append time: the file is in this order, `t` is not
                if w is not None:
                    oldest = w if oldest is None else min(oldest, w)
            if oldest is not None and oldest < since - 1:   # `ts` has 1 s precision
                break
    out.reverse()
    kept = [e for e in out if row_time(e) is not None and row_time(e) >= since]   # 0.0 is a time
    return Tail(kept, complete, None)
```

`_parse_line(raw: bytes) -> Optional[dict]` decodes with `errors="replace"` and returns the dict only when
`json.loads` gives a dict with a string `type` (the same rule as `read_events`).

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m unittest tests.test_store -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
python3 docs/usage-map/generate.py
git add metrics/store.py tests/test_store.py CHANGELOG.md docs/usage-map/data.generated.js
git commit -m "feat: run rows and a backward chunked log reader in the store"
```

CHANGELOG `[Unreleased]` line: the `run` row type (fields as in spec §1) and `store.read_tail`.

---

### Task 2: Config — the `local` section, `claude_pane`, and one list of extra logs

**Files:**
- Modify: `metrics/config.py:19-20` (`V3_TOP_KEYS` gains `"claude_pane"`, `"local"`), add `_validate_local` after `_validate_improve` (`:409-418`), `load_config` return dict (`:518-544`)
- Modify: `bin/bw:334-352` (`_panel_data` reads `metrics_config.extra_logs(config, exclude=store.log_path(config))`; import `config as metrics_config` beside `import store`, `bin/bw:69`)
- Test: `tests/test_config_v3.py`, `tests/test_bw_default.py`

**Interfaces:**
- Produces: `LOCAL_KEYS = {"seats", "logs", "run_max_s", "families"}`, `LOCAL_SEAT_KEYS = {"name", "model", "match", "metric", "icon", "color", "runtime"}` (named `*_KEYS` so the usage map lists them), `RUN_MAX_S_DEFAULT = 2700`, `MATCH_MAX = 200`, `ICON_MAX = 8`, `LABEL_MAX = 80`, `COLOR = re.compile(r"#[0-9a-fA-F]{6}")`, `FAMILY_WORD = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")`.
- Produces: resolved `config["claude_pane"]: bool` (default True) and `config["local"] = {"seats": [{"name", "model", "match", "metric", "icon", "color", "runtime"}], "logs": [abs paths], "run_max_s": int, "families": {word: color}}`. Absent optional seat keys are None; `metric` defaults to `name`.
- Produces: `extra_logs(config: dict, exclude: Optional[str] = None) -> list` — `local.logs` then `improve.panel_logs`, de-duplicated by `os.path.realpath` (the first spelling is kept), without `exclude`. It accepts a partial dict (`{"improve": {...}}`), as `_suggestions` tests pass one.

- [ ] **Step 1: Write the failing tests** (new class `LocalSection(unittest.TestCase)` in `tests/test_config_v3.py`. It has its own `setUp` (`self.tmp = os.path.realpath(tempfile.mkdtemp())`, removed in `addCleanup`) and its own `rejects(obj, needle)`, copied from `SchemaV3.rejects`. The file already imports `json, os, tempfile, Path`; it uses `load_obj(v3())`.)

```python
def test_defaults(self):
    c = load_obj(v3())
    self.assertIs(c["claude_pane"], True)
    self.assertEqual(c["local"], {"seats": [], "logs": [], "run_max_s": 2700, "families": {}})

def test_full_section_resolves(self):
    o = v3(); o["claude_pane"] = False
    o["local"] = {"seats": [{"name": "my-seat", "model": "model-a", "match": "my-seat.sh", "icon": "🦉", "color": "#2dd4bf"}],
                  "logs": ["~/x/m.jsonl"], "run_max_s": 60, "families": {"opus": "#aabbcc"}}
    c = load_obj(o)
    self.assertIs(c["claude_pane"], False)
    self.assertEqual(c["local"]["seats"][0], {"name": "my-seat", "model": "model-a", "match": "my-seat.sh",
                                             "metric": "my-seat", "icon": "🦉", "color": "#2dd4bf", "runtime": None})
    self.assertEqual(c["local"]["logs"], [os.path.expanduser("~/x/m.jsonl")])

def test_bad_input_is_refused(self):  # Review Focus 5
    bw_seat = next(iter(v3()["seats"]))
    for local, needle in (({"seats": [{"name": "a", "match": ""}]}, "local.seats[0].match"),
                          ({"seats": [{"name": "a", "match": "x" * 201}]}, "local.seats[0].match"),
                          ({"seats": [{"name": bw_seat, "match": "x"}]}, "is a bw seat"),
                          ({"seats": [{"name": "a", "match": "x"}, {"name": "a", "match": "y"}]}, "local.seats[1].name"),
                          ({"seats": [{"name": "a", "match": "x", "color": "red"}]}, "local.seats[0].color"),
                          ({"seats": [{"name": "a", "match": "x", "runtime": "nope"}]}, "local.seats[0].runtime"),
                          ({"seats": [{"name": "a", "match": "x", "extra": 1}]}, "local.seats[0].extra: unknown key"),
                          ({"run_max_s": 0}, "local.run_max_s"), ({"families": {"Bad Word": "#000000"}}, "local.families"),
                          ({"logs": [""]}, "local.logs"), ({"nope": 1}, "local.nope: unknown key")):
        o = v3(); o["local"] = local
        self.rejects(o, needle)
    o = v3(); o["claude_pane"] = "yes"; self.rejects(o, "claude_pane: must be true or false")

def test_needs_schema_3(self):
    o = v2(); o["local"] = {}
    self.rejects(o, "local: unknown key (needs schema_version 3)")

def test_extra_logs_merge_and_dedupe(self):  # Review Focus 5
    a = os.path.join(self.tmp, "a.jsonl"); b = os.path.join(self.tmp, "b.jsonl"); Path(a).touch(); Path(b).touch()
    link = os.path.join(self.tmp, "a-link.jsonl"); os.symlink(a, link)
    c = {"local": {"logs": [a, link]}, "improve": {"panel_logs": [b, a]}}
    self.assertEqual(cfg.extra_logs(c), [a, b])
    self.assertEqual(cfg.extra_logs(c, exclude=link), [b])
    self.assertEqual(cfg.extra_logs({"improve": {"panel_logs": [b]}}), [b])

def test_extra_logs_drop_the_main_log(self):  # Review Focus 5
    main = os.path.join(self.tmp, "metrics.jsonl"); Path(main).touch()
    o = v3(); o["metrics_log"] = main; o["local"] = {"logs": [main]}
    c = load_obj(o)
    self.assertEqual(cfg.extra_logs(c, exclude=c["metrics_log"]), [])
```

In `tests/test_bw_default.py` add `test_panel_data_reads_local_logs_too`: a panel row in a file named only by
`local.logs` reaches `self.bw._panel_data(cfg)["panels"]`.

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests.test_config_v3 tests.test_bw_default -v`
Expected: FAIL (`KeyError: 'claude_pane'`).

- [ ] **Step 3: Implement `_validate_local(obj: dict, seats: dict) -> dict`, `extra_logs`, and the two resolved keys in `metrics/config.py`; switch `bin/bw` `_panel_data` to `extra_logs`**

Error messages name the key path: `local.seats[<i>].<key>: …`, `local.run_max_s: must be an integer of at least 1`,
`claude_pane: must be true or false`. `match` and `model` are single-line strings; `icon` is at most
`ICON_MAX` characters; a `name` must `SEAT_NAME.fullmatch`, be unique, and not be a key of `seats`.

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m unittest tests.test_config tests.test_config_v2 tests.test_config_v3 tests.test_bw_default tests.test_pack_export -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
python3 docs/usage-map/generate.py
git add metrics/config.py bin/bw tests/test_config_v3.py tests/test_bw_default.py CHANGELOG.md docs/usage-map/data.generated.js
git commit -m "feat: local config section, claude_pane, and one merged list of extra logs"
```

---

### Task 3: `seats/live.py` — live context and cost from a seat CLI's session file

**Files:**
- Create: `seats/live.py`
- Create: `tests/fixtures/live/codex/sessions/2026/11/27/rollout-x.jsonl`, `tests/fixtures/live/claude-code/session.jsonl`, `tests/fixtures/live/pi/session.jsonl`, `tests/fixtures/live/kimi/session.jsonl`, `tests/fixtures/live/changed.jsonl` (hand-written, synthetic)
- Test: `tests/test_seat_live.py`

**Interfaces:**
- Produces: `TAIL_BYTES = 1024 * 1024`, `MAX_CANDIDATES = 200`, `EMPTY = {"ctx": None, "window": None, "ctx_peak": None, "tokens_in": None, "tokens_out": None, "cost_usd": None}`.
- Produces: `seat_home_path(state: str, seat: str) -> str` — `os.path.join(state, "seats", "homes", seat)`. It makes nothing (never `verify.safe_home`, which runs mkdir and chmod).
- Produces: `session_root(runtime: str, home: Optional[str], dir: str) -> Optional[str]` — with a seat home: codex `<home>/sessions`; claude-code `<home>/.claude/projects/<slug>` where `slug = re.sub(r"[^A-Za-z0-9]", "-", dir)`; pi `<home>/sessions/--<dir without leading "/", "/" to "-">--`; kimi `<home>/sessions`; agy None. With `home=None`: the same under `$CODEX_HOME` or `~/.codex`, `$CLAUDE_CONFIG_DIR` or `~/.claude` (then `projects/<slug>`), `~/.pi/agent`, `$KIMI_CODE_HOME` or `~/.kimi-code`.
- Produces: `live_usage(runtime: str, home: Optional[str], session_id: Optional[str], dir: str, start: float, seat_cfg: Optional[dict] = None) -> dict` — keys of `EMPTY`. Never raises; a missing file, a link, or a changed format gives `EMPTY` values.

Event shapes (spec §4 names). The builder may confirm key names only from the CLI's public docs or from a
session the builder makes in a scratch `HOME`. Never open the user's own session stores or bw's seat homes.
The four shapes below were checked on 2026-11-27 against real session files of each CLI (key names
and nesting only; no content). Use them as given. Fixtures are written by hand:
- codex: last `{"type": "event_msg", "payload": {"type": "token_count", "info": {"last_token_usage": {"input_tokens"}, "total_token_usage": {"input_tokens", "output_tokens"}, "model_context_window"}}}` → `ctx` = last `input_tokens`, `window` = `model_context_window`, tokens from `total_token_usage`.
- claude-code: `{"type": "assistant", "message": {"usage": {...}}}` → `ctx` = `input_tokens + cache_creation_input_tokens + cache_read_input_tokens` of the last line; `tokens_out` = sum of `output_tokens`; `window` None.
- pi: `{"type": "message", "message": {"role": "assistant", "usage": {"input", "output", "cacheRead", "cacheWrite", "cost": {"total"}}}}` → `ctx` = `input + cacheRead + cacheWrite` of the last; `cost_usd` = sum of `cost.total`; `window` = `seat_cfg["provider"]["contextWindow"]` when an int.
- kimi: `{"type": "token_counting.measured", "tokens": <int>}` (in `sessions/<wd>/session_*/agents/main/wire.jsonl`) → `ctx` = `tokens` of the last such line.
- `ctx_peak` = the largest `ctx` in the lines read.

- [ ] **Step 1: Write the failing tests**

```python
def test_codex_from_the_seat_home(self):
    u = live.live_usage("codex", str(FIX / "codex"), None, "/w", 0.0)
    self.assertEqual((u["ctx"], u["window"], u["tokens_in"], u["tokens_out"], u["ctx_peak"]), (41000, 258000, 90000, 1200, 41000))

def test_claude_slug_and_sum(self):
    home = self.copy_to_home("claude-code/session.jsonl", ".claude/projects/-w-a-b/s1.jsonl")
    u = live.live_usage("claude-code", home, None, "/w/a.b", 0.0)
    self.assertEqual((u["ctx"], u["tokens_out"], u["window"]), (113000, 900, None))

def test_pi_cost_and_window_from_config(self):
    home = self.copy_to_home("pi/session.jsonl", "sessions/--w--/x.jsonl")
    u = live.live_usage("pi", home, None, "/w", 0.0, {"provider": {"contextWindow": 1310720}})
    self.assertEqual((u["ctx"], u["cost_usd"], u["window"]), (5000, 0.02, 1310720))

def test_two_worktrees_never_mix(self):
    # codex, pi, and kimi homes each hold a newer session for /w2 and an older one for /w1
    for rt in ("codex", "pi", "kimi"):
        home = self.two_worktree_home(rt)            # fixture builder: /w1 ctx 1000, /w2 ctx 2000 (newer)
        self.assertEqual(live.live_usage(rt, home, None, "/w1", 0.0)["ctx"], 1000, rt)
        self.assertEqual(live.live_usage(rt, home, None, "/w2", 0.0)["ctx"], 2000, rt)

def test_session_id_picks_its_file_else_newest_since_start(self): ...
def test_file_older_than_start_is_ignored(self): ...           # mtime < start - 1 -> EMPTY
def test_agy_missing_link_and_changed_format_give_empty(self):
    self.assertEqual(live.live_usage("agy", self.home, None, "/w", 0.0), live.EMPTY)
    self.assertEqual(live.live_usage("codex", "/nonexistent", None, "/w", 0.0), live.EMPTY)
    # a symlinked session file is not followed; a file of other JSON gives EMPTY
def test_reads_only_the_tail(self): ...                       # a 3 MiB file: ctx from the last line, no MemoryError
def test_seat_home_path_makes_nothing(self):
    p = live.seat_home_path(self.state, "astra"); self.assertFalse(os.path.exists(p))
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests.test_seat_live -v`
Expected: FAIL (`ImportError: cannot import name 'live'`).

- [ ] **Step 3: Implement `seats/live.py`**

Candidates: files `*.jsonl` under `session_root`, at most `MAX_CANDIDATES` by newest mtime, and only files
whose session ran in `dir` (the session's working directory, never the newest file alone):
- codex `sessions/*/*/*/rollout-*.jsonl`: the first line `{"type": "session_meta", "payload": {"cwd"}}` must equal `dir`;
- claude-code: the project slug already names `dir`;
- pi: the first line, the session header `{"type": "session", "cwd"}`, must equal `dir`;
- kimi: `<root>/session_index.jsonl` lines `{"sessionId", "sessionDir", "workDir"}`, newest last; take the
  `sessionDir` entries whose `workDir` equals `dir`, then their `agents/main/wire.jsonl`.
Of these, a name or `sessionId` that holds a `valid_session` id wins; else the newest with `mtime >= start - 1`.
Reading the first line uses the same bounded, no-follow open (at most 64 KiB). Open with `O_RDONLY | O_NOFOLLOW | O_NONBLOCK`, require `S_ISREG`, read the last
`TAIL_BYTES`, and drop the first line when the read did not start at offset 0.

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m unittest tests.test_seat_live -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
python3 docs/usage-map/generate.py
git add seats/live.py tests/test_seat_live.py tests/fixtures/live CHANGELOG.md docs/usage-map/data.generated.js
git commit -m "feat: live context and cost from seat session files"
```

---

### Task 4: Run rows from `bw seat` and `bw panel`

**Files:**
- Create: `lead/runs.py` (row builders; Tasks 5 and 6 extend it)
- Modify: `seats/run.py:26-28` (`SeatResult` gains `run_id`, default None), `:40` (`ROW_OPTIONAL` gains `"run_id"`), `:157-185` (`_Run`), `:267-332` (`execute`, `parse`), `:335-372` (`run_seat` kwargs; the inner `stand_in()` call at `:357-359` passes `panel`, `lead_session`, and `label` on)
- Modify: `seats/cli.py:33-50` (hidden flags), `:183-186` (pass them)
- Modify: `panel/run.py:70-79` (`seat_argv` kwargs), `:379` (`_round(w, runs, seats, brief_path, worktree, tier, panel)`; its caller in `round1`, `:527`, passes `panel`), `:390-391` (round 1 launch), `:584-585` (ask launch)
- Modify: `tests/fixtures/panel/fake_seat.py:22-32` (its strict parser, `allow_abbrev=False`, gains `--panel`, `--lead-session`, `--label`, `--stand-in-for`; `test_panel_run`, `test_panel_cli`, `test_panel_service`, and `test_runner` launch it)
- Modify: `tests/test_seat_run.py:141-144` (`usage()` keeps only `type == "usage"` rows; `tests/test_seat_cli.py` inherits it)
- Test: `tests/test_runs.py`, `tests/test_seat_run.py`, `tests/test_seat_cli.py`, `tests/test_panel_run.py`

**Interfaces:**
- Consumes: `store.append`, `seats.live.live_usage`, `seats.live.seat_home_path` (Task 3).
- Produces (`lead/runs.py`): `RUN_ID = re.compile(r"r[0-9a-f]{12}")`, `LEAD_SESSION = re.compile(r"[0-9a-f]{12}")`, `LABEL_CHARS = 80`, `END_LOOKBACK_S = 7 * 86400`, `new_run_id() -> str` (`"r" + secrets.token_hex(6)`), `label(text: str) -> str` (first line, `safe(redact(...))`, cut to 80), `start_row(seat, model, dir, label, source, *, pid=None, host=None, panel=None, session_id=None, stand_in_for=None, t=None) -> dict`, `end_row(run_id, seat, dir, ok, reason, duration_s, *, tokens_in=None, tokens_out=None, cost_usd=None, ctx_peak=None, t=None) -> dict`. Both set `type: "run"`, `phase`, and `t` (default `time.time()`). `start_row` sets `run_id` from `new_run_id()` and omits None optionals; `end_row` always holds every spec field (null when unknown).
- Produces: `run_seat(..., panel=None, lead_session=None, label=None, stand_in_label=None)`; `SeatResult.run_id`; the usage row's optional `run_id`.
- Produces: `seat_argv(seat, prompt, *, worktree, out, result, brief_file=None, session=None, deep=False, panel_id=None, lead_session=None, label=None, stand_in_for=None) -> list` — each tracking flag is added only when its value is present and valid (`PANEL_ID`, `LEAD_SESSION`, a non-empty label, `SEAT_NAME`), after `--deep` and before `--`, in this order: `--panel`, `--lead-session`, `--label`, `--stand-in-for`.
- Produces: hidden `bw seat` flags (help `argparse.SUPPRESS`): `--panel ID`, `--lead-session ID`, `--label TEXT`, `--stand-in-for SEAT`. **A tracking flag never stops a seat:** a bad value is dropped with one stderr line, `bw seat: ignored bad --<flag> value`, and the seat runs.

Lifecycle in `_Run.execute`: after `_contained(...)` succeeds and before `self.run(...)`, append the start row
(`source: "bw"`, `pid=os.getpid()`, `host=socket.gethostname()`, `stand_in_for = base["stand_in_for"] or stand_in_label`).
A failed start append prints `bw seat: run start not recorded (<ErrorName>)` and the seat still runs. From
there one `try/finally` writes exactly one end row: `refuse` and `finish` record `(ok, reason, duration, usage)`
on `self`, and the `finally` writes `end_row` from it, or `reason: "error"` when nothing was recorded (an
exception). The end reason is null for an ok run; else the result's reason (`"refused"` for a refusal
found after the run, `"timeout"`, `"interrupted"`, `"usage cap"`, `"error: …"`). `ctx_peak` comes from
`live_usage(runtime, seat_home_path(state, seat), new_sid, wt, start, s)`, wrapped so it never raises.
A refusal before `execute` (cap, login, lock, hostile repo) writes no run row.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_seat_run.py — usage() now keeps only type == "usage"; add runs() = store.load(self.log)["runs"]
def test_ok_run_writes_one_start_and_one_end(self):
    r = self.seat()
    s, e = self.runs()
    self.assertEqual((s["phase"], e["phase"], e["run_id"], r.run_id), ("start", "end", s["run_id"], s["run_id"]))
    self.assertTrue(runs.RUN_ID.fullmatch(s["run_id"]))
    self.assertEqual((s["seat"], s["source"], s["pid"], s["host"], s["dir"]), ("astra", "bw", os.getpid(), socket.gethostname(), self.wt))
    self.assertEqual((e["ok"], e["reason"], e["seat"], e["dir"]), (True, None, "astra", self.wt))
    u = self.usage()[-1]
    self.assertEqual((u["type"], u["run_id"]), ("usage", s["run_id"]))   # the usage row, not the end row

# One test per path, so a usage-cap case never leaves a cap armed for a later case (each test has a fresh state).
def test_timeout_ends_once(self):     self.assert_one_end(Result(124, "", "", True), "timeout")
def test_usage_cap_ends_once(self):   self.assert_one_end("usage_limit", "usage cap")
def test_error_ends_once(self):       self.assert_one_end("error", lambda r: r.startswith("error: "))
def test_interrupt_ends_once(self):
    self.fake.raise_ = KeyboardInterrupt
    with self.assertRaises(KeyboardInterrupt): self.seat()
    self.assertEqual([r["phase"] for r in self.runs()], ["start", "end"]); self.assertEqual(self.runs()[-1]["reason"], "interrupted")
def test_exception_ends_once(self):
    self.fake.raise_ = RuntimeError("boom")
    with self.assertRaises(RuntimeError): self.seat()
    self.assertEqual(self.runs()[-1]["reason"], "error")
# assert_one_end(queued, reason): queue it, self.seat(fallback=False), then phases == ["start", "end"],
# end ok False, and the end reason equals `reason` (or passes it when it is a callable).

def test_sandbox_refusal_after_the_run_ends_refused(self):
    self.fake.queue("codex", Result(1, "", "sandbox-exec: denied\n", False))
    self.seat(); self.assertEqual(self.runs()[-1]["reason"], "refused")

def test_refusal_before_execute_writes_no_run_row(self):
    os.makedirs(os.path.join(self.wt, ".claude", "commands")); self.seat("opus"); self.assertEqual(self.runs(), [])

def test_start_append_failure_still_runs(self):
    with mock.patch("lead.runs.store.append", side_effect=[OSError("ro")] + [None] * 5):
        self.assertTrue(self.seat().ok)
    self.assertTrue(any("run start not recorded" in s for s in self.said))

def test_stand_in_is_its_own_run_and_keeps_panel_fields(self):   # cap astra -> gem stands in
    caps.arm(self.state, "astra", time.time(), 5)
    self.seat(panel="p0123abc", lead_session="0123456789ab", label="design round 1")
    s = [r for r in self.runs() if r["phase"] == "start"][-1]
    self.assertEqual((s["seat"], s["stand_in_for"], s["panel"], s["session_id"], s["label"]),
                     ("gem", "astra", "p0123abc", "0123456789ab", "design round 1"))

def test_panel_fields_reach_the_start_row(self):
    self.seat(panel="p0123abc", lead_session="0123456789ab", label="design \x1b[2J round 1", stand_in_label="astra")
    s = self.runs()[0]
    self.assertEqual((s["panel"], s["session_id"], s["stand_in_for"]), ("p0123abc", "0123456789ab", "astra"))
    self.assertNotIn("\x1b", s["label"]); self.assertTrue(s["label"].startswith("design"))

# tests/test_seat_cli.py
def test_bad_tracking_flags_are_dropped_and_the_seat_runs(self):
    for flag, bad in (("--panel", "x1"), ("--lead-session", "zz"), ("--stand-in-for", "nope")):
        rc, err = self.main_err(["astra", flag, bad, "p"])
        self.assertEqual(rc, 0); self.assertEqual(err.count(f"ignored bad {flag} value"), 1)
    self.assertNotIn("--lead-session", self.help_text())

# tests/test_panel_run.py (real helpers: argvs(), last_argv(), lines 106-111)
def test_seat_argv_passes_valid_run_fields_only(self):
    with mock.patch.dict(os.environ, {"BW_PANEL_SEAT_CMD": json.dumps(["x"])}):
        self.assertEqual(run.seat_argv("o", "q", worktree="/w", out="/o", result="/r", panel_id="p0123abc",
                                       lead_session="0123456789ab", label="ask a1", stand_in_for="b")[-10:],
                         ["--panel", "p0123abc", "--lead-session", "0123456789ab", "--label", "ask a1",
                          "--stand-in-for", "b", "--", "q"])
        bare = run.seat_argv("o", "q", worktree="/w", out="/o", result="/r", panel_id="bad", lead_session=None, label="")
        self.assertFalse({"--panel", "--lead-session", "--label"} & set(bare))
def test_round1_and_ask_argv_carry_panel_and_session(self):
    doc = self.round1({})
    for argv in self.argvs():
        self.assertEqual(argv[argv.index("--panel") + 1], doc["panel_id"])
    # then run one ask with this file's ask helper:
    self.assertIn("ask ", self.last_argv("a")[self.last_argv("a").index("--label") + 1])
```

`tests/test_runs.py`: `start_row` omits None optionals; `end_row` keeps all spec keys; `label("a\nb" + "x"*200)`
is `"a"`; `label("\x1b[2J" + "x")` holds no ESC.

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests.test_runs tests.test_seat_run tests.test_seat_cli tests.test_panel_run -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'lead.runs'`).

- [ ] **Step 3: Implement `lead/runs.py`, the `_Run` lifecycle, the CLI flags, `seat_argv`, and the fake seat's flags**

`_round` passes `panel_id=w.pid`, `lead_session=panel.get("session_id")`, `label=f"{panel['checkpoint']} round 1"`,
`stand_in_for=seats[name].get("stand_in_for")`. `ask` passes `panel_id`, `lead_session=panel.get("session_id")`, and
`label=f"ask {ask_id}"`.

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m unittest tests.test_runs tests.test_seat_run tests.test_seat_cli tests.test_panel_run tests.test_panel_cli tests.test_panel_service tests.test_runner -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
python3 docs/usage-map/generate.py
git add lead/runs.py seats/run.py seats/cli.py panel/run.py tests/fixtures/panel/fake_seat.py tests/test_runs.py tests/test_seat_run.py tests/test_seat_cli.py tests/test_panel_run.py CHANGELOG.md docs/usage-map/data.generated.js
git commit -m "feat: bw seat and bw panel write run start and end rows"
```

CHANGELOG: the `run` rows from `bw seat`; the usage row's optional `run_id`; "changes `seats/run.py`, so every
seat needs `bw seat verify --changed` once".

---

### Task 5: `bw run` and the `bin/bw` wiring

**Files:**
- Create: `lead/run_cli.py`
- Modify: `lead/runs.py` (request writing, the heartbeat row)
- Modify: `bin/bw:1-50` (docstring lines for `bw run` and `bw live`), `:75-76` (`COMMANDS` gains `"run"`, `"live"`), `:513-514` (`add_parser("run", add_help=False)`, same for `"live"`), `:516-517` (the pass-through tuple `("seat", "panel")` gains both), `:527-534` (`_dispatch` routes both before `load_config`)
- Create: `tests/fixtures/exit_with.py` (exits with `argv[1]`, or kills itself with the signal in `argv[2]`), `tests/fixtures/sleeper.py` (writes the file `argv[1]`, then sleeps up to 30 s; on SIGTERM it writes `argv[1] + ".term"` and exits 0)
- Test: `tests/test_run_cli.py`, `tests/test_bw_default.py`

**Interfaces:**
- Consumes: `runs.start_row`, `runs.end_row`, `runs.END_LOOKBACK_S`, `store.append`, `store.read_tail`, `store.log_path`, `lead.paths.load_config`.
- Produces (`lead/runs.py`): `REQUEST_DIR = "runs"`, `requests_dir(checkpoint_dir: str) -> str` (`join(checkpoint_dir, "runs")`; every caller passes the checkpoint **dir**: `bw run` passes `dirname($BW_CHECKPOINT)`, Tasks 6 and 7 pass `handoff.checkpoint_dir(state, key)`), `write_request(dir_path: str, row: dict) -> str` (mkdir 0700, write `.tmp-<run_id>.<phase>`, rename to `<run_id>.<phase>.json`, mode 0600; a newer beat replaces the older one), `beat_row(run_id, seat, dir, t=None) -> dict` (`type: "run"`, `phase: "beat"`, PD-13).
- Produces (`lead/run_cli.py`): `main(argv: list, out=sys.stdout, err=sys.stderr, environ=None, now=time.time) -> int`.
- Produces: `bin/bw` routes `bw run …` to `run_cli.main(rest)` and `bw live …` to `cmd_live_placeholder`, which prints `bw live: not built yet` and exits 2. Task 7 replaces that function with `live.main(rest)`. The docstring test needs the name now.

Behavior:
- Wrap form `bw run --seat NAME [--model M] [--label TEXT] [--dir D] -- COMMAND [ARGS...]`. Without `--` or with no command: usage error 2. `NAME` must match `SEAT_NAME`. `--model` defaults to `local.seats[NAME].model` when the config has it. `--dir` defaults to the cwd; both resolve to the worktree root (`resolve_project(...).worktree`), else the real path.
- It writes the start row (`source: "local"`, `pid=os.getpid()`, `host`), runs the command with stdio inherited (`subprocess.Popen`), and forwards SIGINT and SIGTERM to it with `Popen.send_signal` (`lead/proc.py` has none). While the child runs, it waits with `Popen.wait(timeout=run_max_s / 2)` in a loop (no thread) and writes one `beat` row each time the wait times out (PD-13). Then it writes the end row: `ok = rc == 0`, `reason` null, `"exit <rc>"`, or `"signal <n>"`. It exits with `rc`, or `128 + n` after a signal. A command that cannot start gives exit 127 (not found) or 126 (not executable) and `reason: "exit 127"` / `"exit 126"`, with `bw run: <cmd>: not found` (or `not executable`) on stderr.
- Sink, chosen once per `bw run` call and kept for all its rows. `BW_CHECKPOINT` is set for every lead bw starts (`lead/runner.py:168`), contained or not. So: first load the config and `store.append(store.log_path(config), row)`. Only when that fails (`ConfigError` or `OSError`, as in a contained lead whose sandbox denies the state dir) and `BW_CHECKPOINT` is set, write `write_request(requests_dir(dirname(BW_CHECKPOINT)), row)`. When both fail, print one line, `bw run: not tracked (<why>)`, and still run the command.
- `bw run start …` prints only the run id on stdout (also when tracking failed) and records no `pid` (PD-3). `bw run end RUN_ID (--ok | --fail [--reason R])` looks up the start row in `read_tail(log, now - END_LOOKBACK_S)` **and** in the pending request files (`read_requests(..., unlink=False)`, Task 6; until Task 6 lands, the log only), and copies its `seat` and `dir`. An end it writes as a request file may omit `seat` and `dir`; Task 6 fills them. It refuses (stderr, exit 2) a start with `source: "bw"`. Both forms exit 0 when tracking fails. A bad `RUN_ID` is a usage error 2.

- [ ] **Step 1: Write the failing tests** (`tests/test_run_cli.py`; env sets `BALANCEWHEEL_CONFIG` to a v3 fixture copy and `BALANCEWHEEL_METRICS_LOG` to a temp log, and removes `BW_CHECKPOINT`)

```python
def test_wrap_ok_passes_status_and_writes_two_rows(self):
    self.assertEqual(self.main(["--seat", "my-seat", "--label", "lint", "--", sys.executable, EXIT, "0"]), 0)
    s, e = self.runs()
    self.assertEqual((s["source"], s["seat"], s["label"], s["pid"], e["ok"], e["reason"]), ("local", "my-seat", "lint", os.getpid(), True, None))

def test_wrap_failure_and_signal(self):  # Review Focus 1
    self.assertEqual(self.main(["--seat", "s", "--", sys.executable, EXIT, "3"]), 3)
    self.assertEqual(self.runs()[-1]["reason"], "exit 3")
    self.assertEqual(self.main(["--seat", "s", "--", sys.executable, EXIT, "0", "15"]), 143)
    self.assertEqual(self.runs()[-1]["reason"], "signal 15")
    self.assertEqual(self.main(["--seat", "s", "--", "no-such-command-xyz"]), 127)
    self.assertEqual(self.runs()[-1]["reason"], "exit 127")
    script = Path(self.tmp, "noexec.sh"); script.write_text("#!/bin/sh\n"); script.chmod(0o644)
    self.assertEqual(self.main(["--seat", "s", "--", str(script)]), 126)
    self.assertEqual((self.runs()[-1]["reason"], len([r for r in self.runs() if r["phase"] == "end"])), ("exit 126", 4))

def test_sigterm_to_bw_run_reaches_the_child(self):
    p = subprocess.Popen([sys.executable, BW, "run", "--seat", "s", "--", sys.executable, SLEEPER, self.flag], env=self.env)
    self.assertTrue(wait_for(lambda: os.path.exists(self.flag)))
    p.send_signal(signal.SIGTERM); p.wait(10)
    self.assertTrue(os.path.exists(self.flag + ".term"))
    self.assertEqual([r["phase"] for r in self.runs()], ["start", "end"])

def test_heartbeat_while_the_child_runs(self):  # PD-13
    self.write_config(local={"run_max_s": 2})          # a beat every 1 s
    self.main(["--seat", "s", "--", sys.executable, "-c", "import time; time.sleep(2.5)"])
    phases = [r["phase"] for r in self.runs()]
    self.assertEqual((phases[0], phases[-1]), ("start", "end")); self.assertGreaterEqual(phases.count("beat"), 2)

def test_untracked_still_runs(self):  # Review Focus 2
    for env in ({"BALANCEWHEEL_CONFIG": "/nonexistent.json"}, {"BALANCEWHEEL_METRICS_LOG": "/dev/null/x.jsonl"}):
        rc, err = self.main_env(env, ["--seat", "s", "--", sys.executable, EXIT, "4"])
        self.assertEqual(rc, 4); self.assertEqual(err.count("bw run:"), 1); self.assertIn("not tracked", err)

def test_uncontained_lead_appends_directly(self):
    cp = os.path.join(self.tmp, "handoffs", "k"); os.makedirs(cp)
    self.main_env({"BW_CHECKPOINT": os.path.join(cp, "checkpoint.md")}, ["--seat", "s", "--", sys.executable, EXIT, "0"])
    self.assertEqual(len(self.runs()), 2); self.assertFalse(os.path.exists(os.path.join(cp, "runs")))

def test_contained_lead_falls_back_to_request_files(self):
    cp = os.path.join(self.tmp, "handoffs", "k"); os.makedirs(cp)
    rc, _ = self.main_env({"BW_CHECKPOINT": os.path.join(cp, "checkpoint.md"), "BALANCEWHEEL_METRICS_LOG": "/dev/null/x.jsonl"},
                         ["--seat", "s", "--", sys.executable, EXIT, "0"])
    names = sorted(os.listdir(os.path.join(cp, "runs")))
    self.assertEqual((rc, len(names)), (0, 2)); self.assertTrue(all(n.endswith((".start.json", ".end.json")) for n in names))

def test_start_prints_only_the_id_and_end_closes_it(self):
    rid = self.capture(["start", "--seat", "s"]).strip()
    self.assertTrue(runs.RUN_ID.fullmatch(rid)); self.assertNotIn("pid", self.runs()[0])
    self.assertEqual(self.main(["end", rid, "--fail", "--reason", "lint failed"]), 0)
    self.assertEqual((self.runs()[-1]["seat"], self.runs()[-1]["reason"]), ("s", "lint failed"))

def test_end_refuses_a_bw_seat_run(self):
    store.append(self.log, runs.start_row("astra", "model-a", "/w", "x", "bw"))
    self.assertEqual(self.main(["end", self.runs()[0]["run_id"], "--ok"]), 2)

def test_usage_errors(self):
    for argv in (["--seat", "s"], ["--seat", "S!", "--", "true"], ["end", "bad", "--ok"], ["end", "r0123456789ab"]):
        self.assertEqual(self.main(argv), 2)
```

In `tests/test_bw_default.py` the existing `test_every_documented_subcommand_is_known` covers the docstring;
add `test_run_skips_the_config_exit` (with a broken config, `bw run --seat s -- true` exits 0) and
`test_live_placeholder` (`bw live --json` exits 2 with `not built yet`; Task 7 deletes this test).

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests.test_run_cli tests.test_bw_default -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'lead.run_cli'`).

- [ ] **Step 3: Implement `lead/run_cli.py`, `runs.requests_dir`, `runs.write_request`, `runs.beat_row`, and the `bin/bw` wiring**

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m unittest tests.test_run_cli tests.test_bw_default tests.test_runs -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
python3 docs/usage-map/generate.py
git add lead/run_cli.py lead/runs.py bin/bw tests/test_run_cli.py tests/test_bw_default.py tests/fixtures/exit_with.py tests/fixtures/sleeper.py CHANGELOG.md docs/usage-map/data.generated.js
git commit -m "feat: bw run tracks a local wrapper with start, beat, and end rows"
```

---

### Task 6: The panel service ingests run request files

**Files:**
- Modify: `lead/runs.py` (validation, reading, ingest)
- Modify: `lead/run_cli.py` (`bw run end` also looks up pending start requests)
- Modify: `panel/service.py:720-736` (`drain` calls `runs.ingest` inside the drain lock, before `_answer_all`)
- Test: `tests/test_run_requests.py`, `tests/test_panel_service.py`, `tests/test_run_cli.py`

**Interfaces:**
- Consumes: `inbox.read_at(dfd, name, limit, single_link=True)`, `handoff.checkpoint_dir(state, key)`, `runs.requests_dir`, `store.append`, `store.read_tail`, `runs.END_LOOKBACK_S`.
- Produces: `REQUEST_LIMIT = 4096`, `MAX_RUN_REQUESTS = 50`, `MODEL_CHARS = 120`, `REASON_CHARS = 200`, `HOST_CHARS = 255`, `T_SKEW_S = 86400`, `REQUEST_NAME = re.compile(r"(r[0-9a-f]{12})\.(start|beat|end)\.json")`.
- Produces: `validate_request(obj, worktree: str, now: float) -> dict` — the row to append, or `ValueError`. `source` is forced to `"local"`, `dir` to `worktree`; `seat` must match `SEAT_NAME` on a start (optional on `beat` and `end`); strings are single-line, `safe()`, and within their limits; `t` is finite and within `T_SKEW_S` of `now`; `pid` is an int from 1 to 2**31-1 or absent; unknown keys are dropped; `ok` is a bool on an end.
- Produces: `read_requests(dir_path: str, limit: int = MAX_RUN_REQUESTS, unlink: bool = True) -> tuple` — `([(name, dict or ValueError)], problems)`. It opens the dir with `O_NOFOLLOW` at the last two levels (as `panel/channel.py:166-179` does), reads only `REQUEST_NAME` files, refuses links, FIFOs, and second hard links, and unlinks what it takes only when `unlink`. It never raises.
- Produces: `ingest(config: dict, state: str, project, now: Optional[float] = None) -> list` — problem lines. Starts first, then beats, then ends. An end or beat without `seat` gets `seat` from its start (in the log or in this batch); with no start found, `seat` stays null. It refuses an end whose start has `source: "bw"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_run_requests.py
def test_valid_start_and_end_are_appended_with_forced_fields(self):
    self.put("r0123456789ab.start.json", {**runs.start_row("my-seat", "model-a", "/elsewhere", "x", "bw"), "run_id": "r0123456789ab"})
    self.put("r0123456789ab.end.json", {"run_id": "r0123456789ab", "ok": True, "t": self.now})   # no seat, no dir
    self.assertEqual(runs.ingest(self.config, self.state, self.project, now=self.now), [])
    s, e = store.load(self.log)["runs"]
    self.assertEqual((s["source"], s["dir"], e["seat"], e["dir"]), ("local", self.project.worktree, "my-seat", self.project.worktree))
    self.assertEqual(os.listdir(self.runs_dir), [])

def test_hostile_files_are_refused(self):
    os.symlink("/etc/hosts", os.path.join(self.runs_dir, "r0123456789ab.start.json"))
    self.put_bytes("r0123456789ac.start.json", b"x" * 5000)
    self.put("r0123456789ad.start.json", {"seat": "BAD NAME", "t": self.now})
    self.put("r0123456789ae.start.json", {"seat": "s", "t": self.now + 10 * 86400})
    self.put("notes.txt", {})
    probs = runs.ingest(self.config, self.state, self.project, now=self.now)
    self.assertEqual(store.load(self.log)["runs"], []); self.assertTrue(probs)
    self.assertTrue(os.path.exists(os.path.join(self.runs_dir, "notes.txt")))  # not ours: left alone

def test_end_for_a_bw_run_is_refused(self):
    store.append(self.log, {**runs.start_row("astra", "m", self.project.worktree, "x", "bw"), "run_id": "r0123456789ab"})
    self.put("r0123456789ab.end.json", {"run_id": "r0123456789ab", "seat": "astra", "ok": True, "t": self.now})
    self.assertTrue(runs.ingest(self.config, self.state, self.project, now=self.now))
    self.assertEqual([r["phase"] for r in store.load(self.log)["runs"]], ["start"])

def test_read_only_mode_leaves_files(self):
    self.put("r0123456789ab.start.json", {"seat": "s", "t": self.now})
    items, _ = runs.read_requests(self.runs_dir, unlink=False)
    self.assertEqual(len(items), 1); self.assertEqual(len(os.listdir(self.runs_dir)), 1)

def test_bounded_per_call(self):
    for i in range(runs.MAX_RUN_REQUESTS + 5):
        self.put(f"r{i:012x}.start.json", {"seat": "s", "t": self.now})
    runs.ingest(self.config, self.state, self.project, now=self.now)
    self.assertEqual(len(os.listdir(self.runs_dir)), 5)

# tests/test_run_cli.py
def test_end_finds_a_pending_start(self): ...                # contained: start and end both as files; the end file holds seat "s"

# tests/test_panel_service.py
def test_drain_ingests_run_requests(self): ...               # one start request -> one run row after drain(FakeJobs())
def test_drain_survives_a_broken_runs_dir(self): ...         # runs/ is a symlink: drain does not raise; one notice key "runs"
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests.test_run_requests tests.test_panel_service tests.test_run_cli -v`
Expected: FAIL (`AttributeError: module 'lead.runs' has no attribute 'ingest'`).

- [ ] **Step 3: Implement `validate_request`, `read_requests`, `ingest`; call `runs.ingest` from `drain`; let `bw run end` read pending starts**

In `drain`, a problem list becomes one `notice("panel", "; ".join(problems), key="runs")`; an exception becomes
`notice("panel", f"bw could not read run requests ({type(e).__name__})", key="runs")`.

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m unittest tests.test_run_requests tests.test_panel_service tests.test_run_cli -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
python3 docs/usage-map/generate.py
git add lead/runs.py lead/run_cli.py panel/service.py tests/test_run_requests.py tests/test_panel_service.py tests/test_run_cli.py CHANGELOG.md docs/usage-map/data.generated.js
git commit -m "feat: the panel service appends run requests from contained leads"
```

---

### Task 7: `bw live --json`

**Files:**
- Create: `lead/live.py`
- Modify: `bin/bw` (replace `cmd_live_placeholder` with a route to `live.main`), `tests/test_bw_default.py` (delete `test_live_placeholder`)
- Test: `tests/test_live.py`

**Interfaces:**
- Consumes: `store.read_tail`, `store.row_time`, `metrics_config.extra_logs`, `runs.read_requests`, `runs.requests_dir`, `runs.validate_request`, `handoff.checkpoint_dir`, `panel.state.panels` (read-only; never `service.watch` or `ensure_dir`), `seats.live.live_usage`, `seats.live.seat_home_path`, `lead.projects.resolve_project`.
- Produces: `DEFAULT_SINCE_S = 86400`, `DEFAULT_LIMIT = 50`, `LIMIT_MAX = 500`, `pid_alive(pid: int) -> bool`, `run_state(row: dict, now: float, host: str, run_max_s: int, alive=pid_alive, last: Optional[float] = None) -> str`, `build(config, state, cwd, since=None, limit=DEFAULT_LIMIT, now=None, run=proc.run, alive=pid_alive, host=None) -> dict`, `empty(now: float, error: Optional[str] = None) -> dict`, `main(argv: list, out=sys.stdout, err=sys.stderr) -> int`.
- Produces: the v1 document of spec §4, exactly those keys; `local` also holds the resolved `run_max_s` (the pane's fallback expiry reads it, Task 9).

Liveness rule (spec §1):

```python
def run_state(row, now, host, run_max_s, alive=pid_alive, last=None):
    # last: the newest time of this run's start and beat rows (PD-13); the start's own time when None
    pid, t = row.get("pid"), last if last is not None else store.row_time(row)
    if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 and row.get("host") == host:
        return "live" if alive(pid) else "unknown"
    return "live" if t is not None and now - t <= run_max_s else "stale"

def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:      # it exists under another user
        return True
    except OSError:
        return False
    return True
```

Assembly:
1. `cover = min(since, now - max(run_max_s, panel.deep_cap_min * 60))`. Discovery needs no full scan: a `bw run` wrap writes a `beat` row every `run_max_s / 2` (PD-13), and a `bw seat` run is shorter than its cap, so every live run has a row inside the cover. Read the main log (a row's own `source`, default `"bw"`) and every `extra_logs(config, exclude=main)` log (source `"local"`) with `read_tail(path, cover)`. `complete` is the AND of all reads. Each extra log adds `{"path", "ok", "error"}` to `local.logs` (a missing file: `ok: false`, `error: "missing"`). `local.seats` copies `config["local"]["seats"]` (`name`, `match`, `metric`, `model`, `icon`, `color`); `local.families` copies `config["local"]["families"]`; `local.run_max_s` is the resolved value.
2. `worktree` = `resolve_project(--dir or cwd).worktree`, else `os.path.realpath(dir)`. Only rows whose `dir` equals it count.
3. Join first, derive second. Pending request files (`read_requests(requests_dir(handoff.checkpoint_dir(state, key)), unlink=False)`, each item passed through `validate_request(obj, worktree, now)`; an invalid one is skipped) are merged with the logged rows **before** steps 4 to 7, by `run_id`: a pending end closes a logged start, and a pending start and end pair counts as finished. Pending rows have `source: "local"`.
4. Open: each start (logged or pending) with no end of the same `run_id`, with `state = run_state(start, ..., last=<newest start or beat time>)`. Drop it when its `panel` is in panel state and that seat's status is `ok` or `absent` with no running ask for it. `ask` = the id of a running ask of that seat in that panel (PD-4). `icon`/`color` come from `local.seats` by name. For a `source: "bw"` row in state `live`: `live_usage(config seat runtime, seat_home_path(state, seat), None, dir, start, seat_cfg)`; for a local seat with a `runtime`: `live_usage(runtime, None, None, dir, start)`; fill `ctx`, `window`, `cost_usd`.
5. A panel seat whose status is `running`, or a panel ask whose state is `running` (an ask keeps the seat's round-one status, `panel/run.py:570`), with no open run row for that panel and seat, becomes one open entry with `run_id: null`, `source: "bw"`, `state: "live"`, `ask` set for an ask, and `start` = the panel job's `started`, else the ask's `ts`, else the panel's `created`. A panel seat never shows twice.
6. Recent: end rows (logged or pending) with `row_time >= since`, joined to their start row (`model`, `label`, `start`, `stand_in_for`, `source`) and, by `run_id`, to a usage row for tokens and cost the end row lacks; newest `end` first; at most `limit`.
7. Usage: usage rows with no `run_id` and `row_time >= since`, as `{"seat", "t", "dir", "ok", "source"}`; newest first; at most `limit`.
8. Panels: each panel of this worktree in `queued`, `running`, or `ready`: `{"panel", "state", "seats": {name: status or "pending"}, "asks": [{"id", "seat", "state"}]}`.

`main`: `--json` is required (else usage error 2: `bw live: only --json output exists`); `--since` a finite
number; `--limit` an int from 1 to `LIMIT_MAX`. A `ConfigError` prints `empty(now, "config error: …")`, exit 0 (PD-12).

- [ ] **Step 1: Write the failing tests** (`tests/test_live.py`; real git repo in a temp dir as in `tests/test_seat_run.py`; rows written with `store.append`)

```python
def test_empty_state_gives_a_v1_document(self):
    d = live.build(self.config, self.state, self.wt, now=NOW)
    self.assertEqual(set(d), {"v", "now", "complete", "open", "recent", "usage", "local", "panels"})
    self.assertEqual((d["v"], d["open"], d["recent"], d["complete"]), (1, [], [], True))

def test_liveness_rules(self):  # Review Focus 4
    host = socket.gethostname(); mk = lambda **k: {"t": NOW - 10, **k}
    alive = lambda pid: pid == 42
    self.assertEqual(live.run_state(mk(pid=42, host=host), NOW, host, 2700, alive), "live")
    self.assertEqual(live.run_state(mk(pid=43, host=host), NOW, host, 2700, alive), "unknown")
    self.assertEqual(live.run_state(mk(pid=43, host="other"), NOW, host, 2700, alive), "live")
    self.assertEqual(live.run_state({"t": NOW - 2701}, NOW, host, 2700, alive), "stale")
    self.assertEqual(live.run_state({"ts": "2026-11-27T00:00:00Z"}, store.row_time({"ts": "2026-11-27T00:00:00Z"}) + 5, host, 2700, alive), "live")

def test_open_recent_and_join(self):
    s = self.start("astra", source="bw"); self.end(s, ok=True)
    store.append(self.log, {"type": "usage", "seat": "astra", "run_id": s["run_id"], "tokens_in": 10, "tokens_out": 2, "cost_usd": 0.01, "dir": self.wt})
    o = self.start("my-seat", source="local", pid=os.getpid(), host=socket.gethostname())
    d = live.build(self.config, self.state, self.wt, now=NOW)
    self.assertEqual([x["run_id"] for x in d["open"]], [o["run_id"]])
    self.assertEqual((d["recent"][0]["tokens_in"], d["recent"][0]["cost_usd"]), (10, 0.01))
    self.assertEqual(d["open"][0]["icon"], "🦉")

def test_other_worktree_and_old_rows_are_out(self): ...
def test_since_and_limit(self): ...                          # recent/usage cut by --since and --limit
def test_usage_rows_without_run_id_listed(self): ...
def test_panel_seat_is_never_shown_twice(self):
    pid = self.panel_with(seats={"astra": {"status": "running"}}, asks=[{"id": "a1", "seat": "astra", "state": "running"}])
    self.start("astra", source="bw", panel=pid, pid=os.getpid(), host=socket.gethostname())
    d = live.build(self.config, self.state, self.wt, now=NOW)
    self.assertEqual([(o["seat"], o["panel"], o["ask"]) for o in d["open"]], [("astra", pid, "a1")])
def test_panel_done_closes_an_open_row(self): ...
def test_pending_request_shows_without_a_service(self): ...  # request file only -> one open local entry; file still there
def test_pending_pair_and_pending_end_are_joined_first(self):
    # start and end both as request files, no service: the run is in recent, not open
    self.put_request("r0123456789ab.start.json", {"seat": "s", "t": NOW - 5})
    self.put_request("r0123456789ab.end.json", {"ok": True, "t": NOW - 1})
    # a logged start whose end is only pending is closed too
    store.append(self.log, {**runs.start_row("t", None, self.wt, "x", "local"), "run_id": "r0123456789ac", "t": NOW - 5})
    self.put_request("r0123456789ac.end.json", {"ok": False, "reason": "exit 1", "t": NOW - 1})
    d = live.build(self.config, self.state, self.wt, now=NOW)
    self.assertEqual(d["open"], [])
    self.assertEqual(sorted(r["run_id"] for r in d["recent"]), ["r0123456789ab", "r0123456789ac"])
def test_a_beat_keeps_an_old_run_found_and_live(self):  # PD-13
    s = self.start("my-seat", source="local", t=NOW - 3 * 2700, host="other")
    store.append(self.log, runs.beat_row(s["run_id"], "my-seat", self.wt, t=NOW - 100))
    self.assertEqual([(o["run_id"], o["state"]) for o in live.build(self.config, self.state, self.wt, now=NOW)["open"]],
                     [(s["run_id"], "live")])
def test_running_ask_without_a_run_row_shows(self):
    self.panel_with(seats={"astra": {"status": "ok", "session_id": "s1"}}, asks=[{"id": "a1", "seat": "astra", "state": "running", "ts": NOW - 3}])
    self.assertEqual([(o["seat"], o["ask"], o["run_id"]) for o in live.build(self.config, self.state, self.wt, now=NOW)["open"]],
                     [("astra", "a1", None)])
def test_local_holds_run_max_s(self):
    self.assertEqual(live.build(self.with_local(run_max_s=60), self.state, self.wt, now=NOW)["local"]["run_max_s"], 60)
def test_extra_log_missing_is_reported(self):
    d = live.build(self.with_local_logs(["/nonexistent/x.jsonl"]), self.state, self.wt, now=NOW)
    self.assertEqual(d["local"]["logs"], [{"path": "/nonexistent/x.jsonl", "ok": False, "error": "missing"}])
def test_dir_resolution(self):
    sub = os.path.join(self.wt, "sub"); link = os.path.join(self.tmp, "wt-link"); os.symlink(self.wt, link)
    for d in (sub, link): self.assertEqual(self.worktree_of(d), self.wt)
    plain = os.path.join(self.tmp, "plain"); os.makedirs(plain)
    self.assertEqual(self.worktree_of(plain), os.path.realpath(plain))   # no traceback outside git
def test_main_config_error_and_usage_errors(self):
    rc, doc = self.main(["--json"], env={"BALANCEWHEEL_CONFIG": "/nonexistent.json"})
    self.assertEqual((rc, doc["v"], doc["complete"]), (0, 1, False)); self.assertIn("config error", doc["error"])
    for argv in ([], ["--json", "--limit", "0"], ["--json", "--since", "nan"]): self.assertEqual(self.main(argv)[0], 2)
def test_live_writes_nothing(self):
    self.start("astra", source="bw"); self.put_request("r0123456789ab.start.json", {"seat": "s", "t": NOW})
    before = snapshot(self.state), Path(self.log).read_bytes()   # snapshot: {relpath: (mode, size, mtime_ns)}
    live.main(["--json", "--dir", self.wt], out=io.StringIO())
    self.assertEqual((snapshot(self.state), Path(self.log).read_bytes()), before)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests.test_live -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'lead.live'`).

- [ ] **Step 3: Implement `lead/live.py` and route `bw live` to it**

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m unittest tests.test_live tests.test_bw_default -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
python3 docs/usage-map/generate.py
git add lead/live.py bin/bw tests/test_live.py CHANGELOG.md docs/usage-map/data.generated.js
git commit -m "feat: bw live --json reports open and recent runs for a worktree"
```

---

### Task 8: Registry and cockpit — local seats with `origin` and `last_seen`

**Files:**
- Modify: `lead/registry.py:1-20` (docstring), `:179-190` (bw seats gain `"origin": "bw"`; local seats appended)
- Modify: `cockpit/views.py:70-92` (`layout` copies `origin`, default `"bw"`)
- Modify: `cockpit/page.html:374-384` (a node with `origin === "local"` gets a small "local" text and " (local)" in its title)
- Test: `tests/test_registry.py`, `tests/test_cockpit_views.py`, `tests/test_cockpit_page.py`

**Interfaces:**
- Consumes: `config["local"]`, `store.read_tail`, `store.row_time`, `metrics_config.extra_logs`.
- Produces: `LAST_SEEN_DAYS = 30`; `_last_seen(config: dict, now: float) -> dict` (name → newest `row_time` of a `run` row with `seat == name` or a `usage` row with `seat == metric`, over the main and extra logs, since `now - LAST_SEEN_DAYS * 86400`).
- Produces: a local seat node `{"id": "seat:<name>", "name", "runtime", "model", "plan": None, "fallback": None, "verified": "unknown", "enforcement": "local", "origin": "local", "last_seen": float or None, "health": "unknown"}`. No edges.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_registry.py
def test_local_seats_join_seats_with_origin_and_last_seen(self):
    store.append(self.log, {"type": "usage", "seat": "my-metric", "t": NOW - 60})
    reg = registry.build(self.with_local([{"name": "my-seat", "match": "x", "metric": "my-metric"}]), self.state, NOW, run=self.run)
    n = next(s for s in reg["seats"] if s["name"] == "my-seat")
    self.assertEqual((n["origin"], n["health"], n["last_seen"]), ("local", "unknown", NOW - 60))
    self.assertTrue(all(s["origin"] == "bw" for s in reg["seats"] if s["name"] != "my-seat"))
# test_health_values_are_in_the_set already runs over every node; run it with a local seat configured too.

# tests/test_cockpit_views.py
def test_layout_passes_origin_through(self):
    out = views.layout({"seats": [{"id": "seat:a", "name": "a", "origin": "local"}, {"id": "seat:b", "name": "b"}]})
    self.assertEqual({n["id"]: n["origin"] for n in out["nodes"]}, {"seat:a": "local", "seat:b": "bw"})

# tests/test_cockpit_page.py
def test_graph_marks_local_nodes(self):
    self.assertIn('n.origin === "local"', PAGE)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests.test_registry tests.test_cockpit_views tests.test_cockpit_page -v`
Expected: FAIL (`KeyError: 'origin'`).

- [ ] **Step 3: Implement the local nodes, `_last_seen`, the `origin` pass-through, and the page mark**

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m unittest tests.test_registry tests.test_cockpit_views tests.test_cockpit_page tests.test_cockpit_server -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
python3 docs/usage-map/generate.py
git add lead/registry.py cockpit/views.py cockpit/page.html tests/test_registry.py tests/test_cockpit_views.py tests/test_cockpit_page.py CHANGELOG.md docs/usage-map/data.generated.js
git commit -m "feat: local seats in the registry and the cockpit graph"
```

---

### Task 9: The plugin `claude/live-pane/` (clean-room)

**Files:**
- Create: `lead/version.py` (`VERSION = "0.2.0-dev.1"`, PD-10)
- Create: `.claude-plugin/marketplace.json` (repo root): marketplace `balancewheel`, one plugin `live-pane`, source `./claude/live-pane`, `version` = `VERSION`
- Create: `claude/live-pane/.claude-plugin/plugin.json` (`name: "live-pane"`, `version` = `VERSION`, a `userConfig` option `bwPath` with default `"bw"`; confirm the option shape with `claude plugin validate`)
- Create: `claude/live-pane/hooks/hooks.json`, `claude/live-pane/hooks/register.tsx` (entry only: registers the pane, the Bash hook, and the timers)
- Create: `claude/live-pane/hooks/poll.tsx` (`bw live` polling; `$` only as a plain `function` argument)
- Create: `claude/live-pane/hooks/fallback.ts` (pure: `matches`, `pair`, `dropWrapped`, `expire`)
- Create: `claude/live-pane/hooks/layout.ts` (pure: room split, bead position and speed, glyph and color choice)
- Create: `claude/live-pane/hooks/format.ts` (pure: durations, tokens, `≈` cost, history lines)
- Create: `claude/live-pane/hooks/view.tsx` (lanes, figures, context chart, timeline, history)
- Create: `claude/live-pane/types/index.d.ts` (the `bw live` v1 contract: `LiveDoc`, `OpenRun`, `RecentRun`, `UsageRow`, `LocalSection` (with `run_max_s: number`), `LocalSeat`, `PanelRow`), `claude/live-pane/README.md` (what it shows, `bw setup claude-pane`, the tested Claude Code version, the PD-10 bump rule)
- Create: `claude/live-pane/hooks/*.test.tsx` / `*.test.ts`
- Modify: `.gitignore` (add `claude/live-pane/.claude-plugin/types/`, PD-11)
- Test: `tests/test_live_pane_plugin.py`

**Interfaces:**
- Consumes: `bw live --json --dir <cwd> --since <session start epoch>` (Task 7), document `v: 1`.
- Produces: `lead.version.VERSION` (Task 10 compares it to the installed version).

**Plugin facts the builder needs** (spec, "Notes for the builder"; verify every API name in the engine's
generated types before use):
- The engine writes `.claude-plugin/types/` into a loaded plugin folder. Load the folder once (`command claude plugin test claude/live-pane` or `command claude --plugin-dir claude/live-pane`), then grep those types for the exact names: `$.agent.list`, `$.session.usage`, `$.session.model`, `$.process.spawn`, `$.state`, `atom`, the `tool.call` hook and its `next(e)`, and the `ui.render` Pane. Do not write any API name the types do not hold.
- Run `claude plugin validate <dir>` and `claude plugin test <dir>` with the real binary (`command claude` or its absolute path), never a shell alias: an alias that adds flags before `plugin` stops the command.
- JSX compiles to calls to `h`: never name a variable `h`.
- `$` may cross into another file only as the argument of a plain `function` declaration, and each use is spelled `$.noun.method(...)`. `claude plugin test` catches what `tsc` does not. Keep `fallback.ts`, `layout.ts`, and `format.ts` free of `$`.
- `$.state` values read through `atom(ref, initial, { shape })` come back as `initial` unless stored under the same shape tag. A test that seeds state stores the tagged form.
- The `plugin-authoring` skill, when it is built into the builder's Claude Code, is public Claude Code documentation and may be used. Nothing else outside this repo may be opened.

**Behavior** (spec §5):
- Each second: `$.agent.list()`, `$.session.usage()`, `$.session.model()`. Every 5 s while the pane is open: one `bw live` call through `$.process.spawn` with argv `[bwPath, "live", "--json", "--dir", cwd, "--since", String(sessionStart)]`, at most one in flight, a 10 s timeout, cancel on pane close, and a check that `v === 1`. When `bw` is missing, fails, or gives another `v`, the pane still draws Claude's own agents and shows the age of its seat data.
- Fallback (`tool.call` on Bash): `matches(command, seats)` checks each non-empty `match` as a plain substring of `command.slice(0, 4096)`. A match opens a lane with a millisecond start time. A foreground call closes it when the call ends. The hook calls `next(e)` once, returns its result unchanged, and catches its own errors.
- Lane pairing for background lanes:

```ts
// rows: bw live `usage` (no run_id). key(r, n) = `${r.seat}|${r.dir}|${r.t}|${n}`; n counts equal triples in this poll.
export function pair(lanes: Lane[], rows: UsageRow[], taken: Set<string>): Map<string, string> {
  const out = new Map<string, string>();
  const keyed = withKeys(rows);                           // [{row, key}] in bw live order
  for (const lane of [...lanes].sort((a, b) => a.startMs - b.startMs)) {   // the older lane takes a row first
    const hit = keyed
      .filter(k => !taken.has(k.key) && k.row.seat === lane.metric && k.row.dir === lane.dir
                   && k.row.t * 1000 >= lane.startMs - 1000)          // the log has 1 s precision
      .sort((a, b) => a.row.t - b.row.t)[0];                          // the oldest row at or after the start
    if (hit) { taken.add(hit.key); out.set(lane.id, hit.key); }
  }
  return out;
}
// dropWrapped: drop a lane when an open run with the same seat starts within 2000 ms of lane.startMs.
// expire: an unpaired lane older than local.run_max_s (from the last bw live document) closes as "end unknown".
// reconcile: each poll also checks `recent`; a lane whose seat has a recent run that started within 2000 ms of
// lane.startMs (a wrapped run that finished between two polls) is dropped.
```

- Look: one lane per running agent or seat, main first; the bead's speed grows with context fill (a steady middle pace with no known window); glyph by kind (main, subagent, bw seat, local seat; the seat's `icon` when set, two columns for an emoji); color by family word (built-in Claude family words plus `local.families`) for Claude models and by seat for seats. Below: session figures (time, `≈` cost, runs, live, rate limits), the context chart on a fixed 0–100% scale, the agents-at-work timeline, and the two-line history titled "this worktree, since this session started". Lanes take what they need; the charts take 11 rows when there is room; history takes the rest; a narrow pane drops the charts first. Glyphs and colors are original and readable on dark and light terminals; no known game's look.
- State: `$.state` atoms with shape tags, so a reload keeps history; nothing is kept across sessions.

- [ ] **Step 1: Write the failing tests**

Plugin tests (`claude plugin test`), one per spec case plus two edges:
- `view.test.tsx`: draws with no data; draws with samples and finished runs; `bw` missing still draws Claude's agents and an age line.
- `poll.test.tsx`: a slow `bw live` (a spawn that never ends) is never called twice at once; a `v: 2` document is ignored.
- `fallback.test.ts`: a Bash call that matches a local seat opens and then closes a lane; a call that does not match changes nothing; a match after the first 4 KB opens no lane; an empty `match` never matches; `pair` gives the older lane the shared row; a lane is dropped when a run start for its seat is within 2 s; a lane is dropped when a `recent` run for its seat started within 2 s (it finished between two polls); with `local.run_max_s: 60` in the document, an unpaired lane closes as `end unknown` after 60 s, not 2700 s; the hook returns `next(e)`'s result unchanged when its own code throws.

`tests/test_live_pane_plugin.py`:

```python
PLUGIN = ROOT / "claude" / "live-pane"

def test_versions_agree(self):
    self.assertEqual(json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())["version"], version.VERSION)
    m = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    self.assertEqual(m["name"], "balancewheel")
    self.assertEqual([(p["name"], p["source"], p["version"]) for p in m["plugins"]], [("live-pane", "./claude/live-pane", version.VERSION)])

def test_no_model_ids_and_no_h_variable(self):
    text = "".join(p.read_text() for p in PLUGIN.rglob("*.ts*") if ".claude-plugin" not in p.parts)
    self.assertIsNone(re.search(r"claude-(opus|sonnet|haiku|fable)-\d|gpt-\d", text))
    self.assertIsNone(re.search(r"\b(const|let|var)\s+h\b", text))

def test_claude_plugin_validate_and_test(self):
    if os.environ.get("BW_PLUGIN_TESTS") != "1":    # the default suite stays hermetic
        self.skipTest("set BW_PLUGIN_TESTS=1 to run claude plugin validate and test")
    exe = shutil.which("claude")
    if not exe:
        self.skipTest("claude is not on PATH: the plugin's own tests did not run")
    for argv in ([exe, "plugin", "validate", str(PLUGIN)], [exe, "plugin", "validate", str(ROOT)], [exe, "plugin", "test", str(PLUGIN)]):
        r = subprocess.run(argv, capture_output=True, text=True, timeout=600)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests.test_live_pane_plugin -v`
Expected: FAIL (`FileNotFoundError` for `plugin.json`).

- [ ] **Step 3: Write the plugin, `lead/version.py`, the marketplace file, and the `.gitignore` line**

- [ ] **Step 4: Run to verify they pass**

Run: `command claude plugin test claude/live-pane && BW_PLUGIN_TESTS=1 python3 -m unittest tests.test_live_pane_plugin -v`
Expected: every plugin test passes; the Python test PASSES (without `BW_PLUGIN_TESTS=1`, or without `claude` on PATH, the last test skips with its reason).

- [ ] **Step 5: Commit**

```bash
python3 docs/usage-map/generate.py
git add lead/version.py .claude-plugin/marketplace.json claude/live-pane .gitignore tests/test_live_pane_plugin.py CHANGELOG.md docs/usage-map/data.generated.js
git commit -m "feat: the live pane plugin for claude code"
```

---

### Task 10: Install — `bw setup claude-pane` and the ensure step at launch

**Files:**
- Create: `lead/claude_pane.py`
- Create: `tests/fixtures/fake_claude.py` (appends its argv as one JSON line to `$FAKE_CLAUDE_LOG`; answers `--version`, `plugin list --json`, and `plugin marketplace list --json` from `$FAKE_CLAUDE_STATE`, a JSON file; exits 0)
- Modify: `bin/bw:23` (docstring line for `bw setup claude-pane [--remove]`), `:460-463` (`cmd_setup` routes `claude-pane`), `:512` (`choices=["cc-switch", "claude-pane"]`, `--remove`), `:528-529` (`setup claude-pane` loads the config itself)
- Modify: `lead/runner.py:208-220` (`_launch`: before `self.adapter.argv(...)`, when `self.lead["runtime"] == "claude-code"`, call `claude_pane.ensure(ctx.config, say=ctx.say)`; `run()` at `:754` already refused a bw inside the project, so `refused` is None here)
- Modify: `lead/start.py:310-336` (`_dry_run` prints "would ensure the live pane plugin" when `claude_pane` is not false and a lead in the profile's `lead_order` runs `claude-code`)
- Modify: `tests/fixtures/run_runner.py` (the only driver that reaches `Session._launch` in tests: it sets `os.environ.setdefault("BW_CLAUDE_CMD", json.dumps([sys.executable, <fake_claude.py>]))` and `FAKE_CLAUDE_LOG` to `$FAKE_OUT/claude-calls.jsonl`, so no runner test calls a real `claude`)
- Modify: `docs/stack.md:20` (Claude Code row: the live pane, tested on Claude Code 2.1.332, the minimum `bw setup claude-pane` checks), `docs/leads.md:74` (Containment: the plugin runs unsandboxed in every Claude session, so its source is bw's own checkout, outside every lead's write grant; setup refuses otherwise)
- Modify: `README.md:215-222` (the fenced `bw start` excerpt) and `docs/start/index.html:517` (the `bw start --dry-run` output gains the line; copy it from a real run)
- Test: `tests/test_claude_pane.py`, `tests/test_runner.py`, `tests/test_start.py`

**Interfaces:**
- Consumes: `lead.version.VERSION`, `lead.paths.ROOT`, `proc.run`.
- Produces: `MARKETPLACE = "balancewheel"`, `PLUGIN_ID = "live-pane@balancewheel"`, `MIN_CLAUDE = (2, 1, 332)`, `STEP_TIMEOUT_S = 20`, `CHECK_TIMEOUT_S = 5`.
- Produces: `marketplace_source(run, timeout) -> Optional[str]` (the `balancewheel` entry's source path, real-path compared to ROOT).
- Produces: `claude_cmd() -> list` (`BW_CLAUDE_CMD`, a JSON list, else `["claude"]`), `parse_version(text: str) -> Optional[tuple]`, `installed(run=proc.run, timeout=CHECK_TIMEOUT_S) -> Optional[dict]` (the `PLUGIN_ID` entry of `claude plugin list --json`: `id`, `version`, `enabled`), `refusal(config: Optional[dict], root: str = ROOT, run=proc.run) -> Optional[str]`, `setup(config: Optional[dict], remove: bool = False, run=proc.run, out=sys.stdout) -> int`, `ensure(config: dict, run=proc.run, say=print, refused: Optional[str] = None) -> None` (`refused`: the runner's `refuse_inside` result).

Behavior:
- `refusal`: ROOT inside any `config["projects"]` path, or inside any worktree that `git -C <path> worktree list --porcelain` names for it (bounded by `CHECK_TIMEOUT_S`), gives `"refusing: bw's checkout (<ROOT>) lies inside <path>; a lead there could edit the pane that runs in every Claude session"`.
- `setup` (each step bounded by `STEP_TIMEOUT_S`; it prints one line per step it ran): `claude --version` below `MIN_CLAUDE` or unreadable → one line that mods cannot load, exit 1; a `balancewheel` marketplace whose source is not ROOT → refuse with `bw: a marketplace named balancewheel points at <source>, not this checkout; remove it first`, exit 1, and change nothing; marketplace absent → `claude plugin marketplace add <ROOT>`; plugin absent → `claude plugin install live-pane@balancewheel`; installed at another version → `claude plugin marketplace update balancewheel`, then `claude plugin update live-pane@balancewheel`; already current → "the live pane is installed and current". Idempotent. `--remove` runs `claude plugin uninstall live-pane@balancewheel` and `claude plugin marketplace remove balancewheel`, and only for the marketplace whose source is ROOT. A `ConfigError` gives `config=None` (no project check) and one line.
- `ensure`: one total limit of `CHECK_TIMEOUT_S` (5 s) for every check it makes (version, list, marketplace source). It calls `refuse_inside(project, ...)` for the current project (`lead/runner.py:754` already holds that result; pass it in) and `refusal(config)` first, and does nothing (one line) when either refuses. It applies the same marketplace-source rule as `setup`; does nothing when `config.get("claude_pane") is False`, when the entry exists with `enabled: false`, or when the check fails or times out. Absent → install (as `setup`); version not `VERSION` → update. It prints one `bw: …` line only when it acts. It never raises.
- Confirm the JSON shapes of `plugin list --json` and `plugin marketplace list --json` against a scratch `CLAUDE_CONFIG_DIR`, never the real one, then write the fake's state by hand.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_claude_pane.py (run = a recorder that returns Results from a table)
def test_fresh_setup_adds_marketplace_then_installs(self):
    rec = self.rec(version="2.1.332 (Claude Code)", plugins=[], markets=[])
    self.assertEqual(cp.setup(self.config, run=rec), 0)
    changing = [c for c in rec.calls if "--version" not in c and "list" not in c]   # drop the read-only checks
    self.assertEqual(changing, [["claude", "plugin", "marketplace", "add", cp.ROOT], ["claude", "plugin", "install", "live-pane@balancewheel"]])

def test_setup_is_idempotent_and_updates_on_version_change(self): ...
def test_old_claude_is_refused(self):
    self.assertEqual(cp.setup(self.config, run=self.rec(version="2.0.1 (Claude Code)")), 1)
def test_refuses_root_inside_a_project(self):
    self.assertIn("refusing", cp.refusal({"projects": {cp.ROOT: "p"}}, run=self.rec()))
def test_a_foreign_balancewheel_marketplace_is_refused(self):
    rec = self.rec(markets=[{"name": "balancewheel", "source": "directory", "path": "/elsewhere"}], plugins=[])
    self.assertEqual(cp.setup(self.config, run=rec), 1)
    self.assertFalse([c for c in rec.calls if "install" in c or "add" in c or "update" in c])

def test_remove_touches_only_its_own(self):
    rec = self.rec(markets=[{"name": "balancewheel", "source": "directory", "path": "/elsewhere"}],
                   plugins=[{"id": cp.PLUGIN_ID, "version": version.VERSION, "enabled": True}])
    cp.setup(self.config, remove=True, run=rec)
    self.assertNotIn(["claude", "plugin", "marketplace", "remove", "balancewheel"], rec.calls)
    self.assertFalse([c for c in rec.calls if "uninstall" in c])     # no uninstall when the marketplace is not ours
    # the market keys above are a guess: use the shape a scratch CLAUDE_CONFIG_DIR run shows
def test_ensure(self):
    for plugins, want in (([], "install"), ([{"id": cp.PLUGIN_ID, "version": "0.0.1", "enabled": True}], "update"),
                          ([{"id": cp.PLUGIN_ID, "version": version.VERSION, "enabled": True}], None),
                          ([{"id": cp.PLUGIN_ID, "version": "0.0.1", "enabled": False}], None)):
        rec = self.rec(plugins=plugins); cp.ensure(self.config, run=rec, say=self.said.append)
        self.assertEqual(any(want and want in " ".join(c) for c in rec.calls), bool(want))
    rec = self.rec(); cp.ensure({**self.config, "claude_pane": False}, run=rec); self.assertEqual(rec.calls, [])
    cp.ensure(self.config, run=self.rec(timeout=True))       # a hung claude: no raise, no line
    rec = self.rec(slow_s=2); t0 = time.monotonic(); cp.ensure(self.config, run=rec)   # each check 2 s
    self.assertLessEqual(time.monotonic() - t0, cp.CHECK_TIMEOUT_S + 0.5)              # one 5 s total

# tests/test_runner.py
def test_claude_code_lead_ensures_the_live_pane(self):       # config(): lead la gets runtime claude-code
    ...; calls = self.claude_calls()                           # lines of FAKE_OUT/claude-calls.jsonl
    self.assertIn(["plugin", "list", "--json"], [c[-3:] for c in calls])
def test_other_runtimes_never_call_claude(self):
    ...; self.assertEqual(self.claude_calls(), [])

# tests/test_start.py
def test_dry_run_says_it_would_ensure_the_live_pane(self):
    out = self.dry_run(lead_runtime="claude-code")
    self.assertIn("would ensure the live pane plugin", out); self.assertFalse(self.claude_called())
    self.assertNotIn("would ensure the live pane plugin", self.dry_run(lead_runtime="claude-code", claude_pane=False))

def test_stack_md_records_the_minimum(self):                   # in tests/test_claude_pane.py
    self.assertIn(".".join(map(str, cp.MIN_CLAUDE)), (ROOT / "docs" / "stack.md").read_text())
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m unittest tests.test_claude_pane tests.test_runner tests.test_start -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'lead.claude_pane'`).

- [ ] **Step 3: Implement `lead/claude_pane.py`, the runner and dry-run lines, the `bw setup` choice, and the docs**

Run `python3 bin/bw start --dry-run` in a scratch worktree with a claude-code lead first in the order, and
copy its real output into `README.md` and `docs/start/index.html`. Replace every real path with the
placeholders the guide already uses (`~/code/my-project`, `~/.local/state/balancewheel/...`); no real path lands.

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m unittest tests.test_claude_pane tests.test_runner tests.test_start tests.test_bw_default -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
python3 docs/usage-map/generate.py
git add lead/claude_pane.py lead/runner.py lead/start.py bin/bw tests/test_claude_pane.py tests/test_runner.py tests/test_start.py tests/fixtures/fake_claude.py docs/stack.md docs/leads.md README.md docs/start/index.html CHANGELOG.md docs/usage-map/data.generated.js
git commit -m "feat: bw setup claude-pane and the live pane check at claude code launch"
```

---

### Task 11: Docs — the reference and the journeys

**Files:**
- Modify: `docs/leads.md` (a "Live pane, `bw run`, and the local layer" section: `bw run` forms and when to use each, `bw live --json` with its v1 fields, the `local` and `claude_pane` keys, `bw setup claude-pane [--remove]`, the `bw seat verify --changed` step after this upgrade, the PD-10 version rule; update the preamble date)
- Modify: `docs/usage-map/infra.js` (a journey: a user wraps a local seat with `bw run` and sees it in the pane; mark the live pane shipped), then `python3 docs/usage-map/generate.py`
- Modify: `CHANGELOG.md [Unreleased]` (check that every earlier task's line is there; one summary line for the pane)
- Modify: `docs/stack.md` (preamble date and "what changed")
- Modify: `balancewheel.example.json` (`"claude_pane": true` and a `local` section with one synthetic seat, as in spec §3; `tests/test_config*` load it, so run them)
- (Removed 2026-11-27: the old "ox"/GLM seat is retired and replaced by `flash` on branch `docs/retire-glm`, merged to `main`; this branch takes it in when it merges `main` before finishing. Do not edit `docs/reference-implementation.md` or `docs/architecture.md` for it here.)

- [ ] **Step 1: Check every doc claim against the code**

Run: `python3 bin/bw --help | grep -E "bw (run|live|setup)"` and `python3 bin/bw live --json | python3 -m json.tool | head -20`
Expected: the help lines and the v1 keys match what `docs/leads.md` says.

- [ ] **Step 2: Run the full suite** (the example config changed: `tests.test_config*` must still pass)

Run: `python3 -m unittest discover -s tests -v 2>&1 | tail -5`
Expected: `OK` (or `OK (skipped=N)`), with more tests than the 2337 at baseline.

- [ ] **Step 3: Commit**

```bash
git add docs/leads.md docs/usage-map/infra.js docs/usage-map/data.generated.js docs/stack.md CHANGELOG.md balancewheel.example.json
git commit -m "docs: the live pane, bw run, bw live, and the local layer"
```

---

## Verification (whole branch)

- `python3 -m unittest discover -s tests -v` — all pass; the plugin test skips unless `BW_PLUGIN_TESTS=1`.
- `BW_PLUGIN_TESTS=1 python3 -m unittest tests.test_live_pane_plugin -v` — passes where `claude` is on PATH.
- `command claude plugin validate claude/live-pane`, `command claude plugin validate .`, and `command claude plugin test claude/live-pane` — exit 0.
- `git diff 09a3408 --stat -- lead/sandbox.py lead/runtimes.py lead/codepaths.py seats/adapters.py seats/grants.py setup/paths.txt` — empty.
- `python3 docs/usage-map/generate.py && git status --short docs/usage-map` — no change (the committed file is current).
- Manual, spends quota: one `bw seat <id> -b "…"` run, then `bw live --json` while it runs (one `open` entry, `state: "live"`) and after (one `recent` entry with tokens).

## Risks and open questions

- **Seat key reset.** Task 4 changes `seats/run.py`, so every seat is unverified until `bw seat verify --changed` runs (spends quota). The CHANGELOG and `docs/leads.md` say so.
- **SIGINT forwarding.** A child in the same terminal group already gets Ctrl-C; `bw run` forwards it again. Most CLIs treat a second SIGINT as an exit. If a wrapper treats it as a force quit, forward only SIGTERM and say so in `docs/leads.md`.
- **Session-file formats** (Task 3) are vendor formats that change; kimi's is unverified. A change gives nulls, never an error.
- **pid reuse.** A dead pid reused by another process on the same host keeps a lane `live`. The spec accepts this; the `run_max_s` rule does not apply to such a row.
- **Heartbeat volume.** PD-13 adds two rows per `run_max_s` per running wrap (about 3 rows an hour at the default). Acceptable for an append-only log.
- **Plugin API names** are not pinned in this plan. The builder takes them from the engine's generated types for the tested Claude Code version (2.1.332).
- **Version bumps.** With PD-10, an unbumped plugin change does not reach installed panes.

## Follow-up (not in this plan)

- Spec §7 (OpenEscapement) is a follow-up in another repo, on its own branch, docs only: the "local layer" paragraph beside esc's "+N lines local" report. This plan does not touch it.
