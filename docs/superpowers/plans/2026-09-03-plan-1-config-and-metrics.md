# Plan 1 — Config loader and the metrics loop (store, logger, watches, dashboard)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Balancewheel's first code: a validated JSON config, an append-only metrics store with one tolerant reader, a logger CLI with roster watches, and a self-contained HTML dashboard that shows change over time — all runnable against an existing metrics log.

**Architecture:** Four stdlib-Python modules in `metrics/` (`config.py`, `store.py`, `log.py`, `dashboard.py`) plus a `watches.py` helper; scripts import siblings by path so they run as `python3 metrics/log.py …` with no packaging. Events are JSONL lines; later events amend earlier ones instead of rewriting; `store.load()` is the only reader and folds everything. The dashboard is one HTML file with inline SVG, no JS beyond `<details>`.

**Tech Stack:** Python 3.9+ standard library only (`json`, `hashlib`, `fcntl`, `html`, `xml.etree`, `unittest`). No third-party packages. Tests run with `python3 -m unittest discover -s tests -v`.

**Spec:** `docs/superpowers/specs/2026-09-03-config-driven-seats-and-metrics-design.md` — sections *Config*, *Metrics*, *Dashboard*, *Testing*, and the *Constraints*. Plans 2 (seats/setup) and 3 (commands/docs) build on the interfaces this plan produces.

## Global Constraints

- Python **3.9+** standard library only; no `tomllib`, no `jq`, no pip packages. Config is JSON.
- **Clean-room**: nothing copied from any private tooling. Test fixtures use synthetic paths (`/work/example-repo`) and placeholder model ids (`vendor/model-a`). No personal paths, no project names, no mention of external review tooling in any file.
- Model ids are **data**, never enumerated or validated by code.
- Event log is **append-only**; every append is one `write()` under an exclusive `fcntl.flock`.
- Legacy records (no `id`, no `partial`/`unique`, `seats` as a list, `panel-amend` keyed by `panel_ts`, watches without `id`) must load without error; missing counts become `None`, never `0`.
- Every string from the log is HTML-escaped before it reaches the dashboard.
- Commit messages: lowercase summary with a type prefix (`feat:`, `test:`, `docs:`), as the repo already does.
- All work happens on a branch off `main` in a worktree (superpowers:using-git-worktrees); `main` is never committed to directly.

---

## File structure

| File | Responsibility |
|---|---|
| `balancewheel.example.json` | Placeholder config the user copies; every key the loader accepts, with a comment-free header key `"_comment"` allowed once at top level |
| `metrics/config.py` | `load_config(path=None)` → resolved dict; `ConfigError` with key path; effort mapping table; role and runtime sets |
| `metrics/store.py` | `read_events`, `load` (normalise + fold), `append` (locked single write), `watch_id`, `legacy_watch_id` |
| `metrics/watches.py` | `evaluate(panels, config) -> [watch]`; the three roster watches as pure functions |
| `metrics/log.py` | CLI: `config`, `usage`, `panel`, `check`, `watches`, `resolve`, `amend` |
| `metrics/dashboard.py` | `render(data, config, seats_state) -> str`; `main()` writes `state_dir/dashboard.html` |
| `tests/fixtures/*.jsonl`, `tests/fixtures/config.json` | Synthetic logs: modern, legacy, truncated |
| `tests/test_config.py`, `test_store.py`, `test_watches.py`, `test_log.py`, `test_dashboard.py` | unittest suites |
| `CLAUDE.md` (modify) | "There is now code": how to run tests; firewall unchanged |

Interfaces shared across tasks (exact names):

```python
# metrics/config.py
class ConfigError(Exception): ...
RUNTIMES = {"claude-code", "codex", "pi", "kimi", "agy"}
ROLES = {"design", "plan", "review", "debug", "decision"}
EFFORT_LEVELS = {"codex": {"low","medium","high"},
                 "pi": {"off","minimal","low","medium","high","xhigh"},
                 "kimi": {"low","high","max"}}
def load_config(path: str | None = None) -> dict   # resolved: absolute paths, defaults applied
def config_path(path: str | None = None) -> str    # $BALANCEWHEEL_CONFIG or ~/.config/balancewheel/config.json

# metrics/store.py
def log_path(config: dict | None = None) -> str    # $BALANCEWHEEL_METRICS_LOG > config["metrics_log"]
def read_events(path: str) -> tuple[list[dict], list[str]]   # (events, warnings)
def load(path: str) -> dict   # {"usage","panels","watches","events","warnings"}
def append(path: str, event: dict) -> None
def watch_id(kind: str, target: str, panel_id: str) -> str   # sha1(kind|target|panel_id)[:8]
def legacy_watch_id(ts: str, kind: str, target: str) -> str  # sha1(ts|kind|target)[:8]
def panel_id_for(event: dict) -> str   # event["id"] or sha1(ts)[:8]

# metrics/watches.py
def evaluate(panels: list[dict], config: dict) -> list[dict]  # candidate watch events (no ts)

# metrics/log.py — CLI only; importable helpers:
def validate_panel(rec: dict, config: dict, force: bool) -> dict   # returns normalised record, raises ValueError
def compute_counts(findings_detail: list[dict]) -> dict           # {seat: {total,confirmed,refuted,partial,unique}}

# metrics/dashboard.py
def render(data: dict, config: dict, seats_state: dict | None) -> str
```

---

### Task 1: Config loader and example config

**Files:**
- Create: `metrics/config.py`
- Create: `balancewheel.example.json`
- Create: `tests/fixtures/config.json`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `load_config`, `config_path`, `ConfigError`, `RUNTIMES`, `ROLES`, `EFFORT_LEVELS` (signatures above). Later tasks call `load_config()` and read `config["seats"]`, `config["watches"]`, `config["state_dir"]`, `config["metrics_log"]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_config.py`:

```python
import json, os, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "metrics"))
import config as cfg  # noqa: E402

FIXTURE = HERE / "fixtures" / "config.json"


def write(tmp, obj):
    p = Path(tmp) / "config.json"
    p.write_text(json.dumps(obj))
    return str(p)


class LoadConfig(unittest.TestCase):
    def test_fixture_loads_with_defaults_and_absolute_paths(self):
        c = cfg.load_config(str(FIXTURE))
        self.assertEqual(c["schema_version"], 1)
        self.assertTrue(os.path.isabs(c["state_dir"]))
        self.assertEqual(c["metrics_log"], os.path.join(c["state_dir"], "metrics.jsonl"))
        self.assertEqual(c["watches"]["bloat_total"], 30)
        self.assertEqual(c["watches"]["redundant_window_panels"], 10)
        self.assertTrue(c["seats"]["alpha"]["enabled"])
        self.assertEqual(c["seats"]["alpha"]["roles"], ["review"])
        self.assertEqual(c["seats"]["alpha"]["args"], [])

    def test_env_var_overrides_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = write(tmp, json.load(open(FIXTURE)))
            os.environ["BALANCEWHEEL_CONFIG"] = p
            try:
                self.assertEqual(cfg.config_path(), p)
            finally:
                del os.environ["BALANCEWHEEL_CONFIG"]

    def test_unknown_top_level_key_names_key_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            obj = json.load(open(FIXTURE)); obj["metric_log"] = "x"
            with self.assertRaises(cfg.ConfigError) as e:
                cfg.load_config(write(tmp, obj))
            self.assertIn("metric_log", str(e.exception))

    def test_unknown_seat_key_names_key_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            obj = json.load(open(FIXTURE)); obj["seats"]["alpha"]["efort"] = "high"
            with self.assertRaises(cfg.ConfigError) as e:
                cfg.load_config(write(tmp, obj))
            self.assertIn("seats.alpha.efort", str(e.exception))

    def test_unknown_runtime_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            obj = json.load(open(FIXTURE)); obj["seats"]["alpha"]["runtime"] = "mystery"
            with self.assertRaises(cfg.ConfigError) as e:
                cfg.load_config(write(tmp, obj))
            self.assertIn("seats.alpha.runtime", str(e.exception))

    def test_effort_rejected_for_agy_and_claude_code(self):
        for rt in ("agy", "claude-code"):
            with tempfile.TemporaryDirectory() as tmp:
                obj = json.load(open(FIXTURE))
                obj["seats"]["alpha"].update({"runtime": rt, "effort": "high"})
                with self.assertRaises(cfg.ConfigError) as e:
                    cfg.load_config(write(tmp, obj))
                self.assertIn("seats.alpha.effort", str(e.exception))

    def test_effort_level_validated_per_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            obj = json.load(open(FIXTURE))
            obj["seats"]["alpha"].update({"runtime": "pi", "effort": "max"})
            with self.assertRaises(cfg.ConfigError) as e:
                cfg.load_config(write(tmp, obj))
            self.assertIn("xhigh", str(e.exception))

    def test_bad_seat_name_and_bad_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            obj = json.load(open(FIXTURE)); obj["seats"]["Bad Name"] = obj["seats"]["alpha"]
            with self.assertRaises(cfg.ConfigError):
                cfg.load_config(write(tmp, obj))
        with tempfile.TemporaryDirectory() as tmp:
            obj = json.load(open(FIXTURE)); obj["seats"]["alpha"]["roles"] = ["review", "qa"]
            with self.assertRaises(cfg.ConfigError) as e:
                cfg.load_config(write(tmp, obj))
            self.assertIn("seats.alpha.roles", str(e.exception))

    def test_types_are_checked_not_coerced(self):
        for patch, key in (({"schema_version": True}, "schema_version"), ({"state_dir": None}, "state_dir"),
                           ({"watches": {"bloat_total": True}}, "watches.bloat_total"),
                           ({"watches": {"redundant_min_panels": 0}}, "redundant_min_panels")):
            with tempfile.TemporaryDirectory() as tmp:
                obj = json.load(open(FIXTURE)); obj.update(patch)
                with self.assertRaises(cfg.ConfigError) as e:
                    cfg.load_config(write(tmp, obj))
                self.assertIn(key, str(e.exception))

    def test_json_syntax_error_reports_line_and_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "config.json"; p.write_text('{"seats": {,}}')
            with self.assertRaises(cfg.ConfigError) as e:
                cfg.load_config(str(p))
            self.assertRegex(str(e.exception), r"line \d+ column \d+")

    def test_example_config_is_valid(self):
        c = cfg.load_config(str(HERE.parent / "balancewheel.example.json"))
        self.assertGreaterEqual(len(c["seats"]), 3)


if __name__ == "__main__":
    unittest.main()
```

`tests/fixtures/config.json`:

```json
{
  "schema_version": 1,
  "seats": {
    "alpha": {"runtime": "codex", "model": "vendor/model-a", "effort": "high", "roles": ["review"]},
    "beta":  {"runtime": "pi", "model": "router/vendor/model-b", "effort": "medium",
              "roles": ["plan", "review", "decision"],
              "provider": {"reasoning": true, "contextWindow": 200000, "maxTokens": 32000}},
    "gamma": {"runtime": "claude-code", "model": "vendor/model-c", "roles": ["design", "plan", "review", "debug", "decision"]},
    "delta": {"runtime": "agy", "model": "vendor/model-d-high", "roles": ["review"]},
    "omega": {"runtime": "kimi", "model": "vendor/model-e", "roles": ["review"], "enabled": false}
  },
  "state_dir": "/work/state/balancewheel"
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_config -v`
Expected: FAIL / ERROR with `ModuleNotFoundError: No module named 'config'`.

- [ ] **Step 3: Write `metrics/config.py`**

```python
"""config.py — load and validate ~/.config/balancewheel/config.json.

Resolved config has absolute paths, defaults applied, and every seat filled in
(`enabled`, `roles`, `args`, `effort`, `provider`, `policy`). Errors name the key path.
"""
import json
import os
import re
from pathlib import Path

RUNTIMES = {"claude-code", "codex", "pi", "kimi", "agy"}
ROLES = {"design", "plan", "review", "debug", "decision"}
EFFORT_LEVELS = {
    "codex": {"low", "medium", "high"},
    "pi": {"off", "minimal", "low", "medium", "high", "xhigh"},
    "kimi": {"low", "high", "max"},
}
SEAT_NAME = re.compile(r"^[a-z0-9-]+$")
TOP_KEYS = {"_comment", "schema_version", "seats", "state_dir", "metrics_log", "commands_dir", "bin_dir", "watches"}
SEAT_KEYS = {"runtime", "model", "effort", "roles", "args", "provider", "policy", "enabled"}
WATCH_DEFAULTS = {
    "bloat_total": 30, "bloat_per_seat": 8, "bloat_min_seats": 3,
    "skim_min_seats": 3, "skim_min_findings": 15,
    "redundant_min_panels": 5, "redundant_window_panels": 10,
}
DEFAULT_PATH = "~/.config/balancewheel/config.json"


class ConfigError(Exception):
    pass


def config_path(path=None):
    return path or os.environ.get("BALANCEWHEEL_CONFIG") or os.path.expanduser(DEFAULT_PATH)


def _abs(p):
    return os.path.abspath(os.path.expanduser(str(p)))


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _expect(cond, msg):
    if not cond:
        raise ConfigError(msg)


def _validate_seat(name, seat):
    kp = f"seats.{name}"
    _expect(SEAT_NAME.match(name), f"{kp}: seat name must match [a-z0-9-]+")
    _expect(isinstance(seat, dict), f"{kp}: must be an object")
    unknown = sorted(set(seat) - SEAT_KEYS)
    if unknown:  # not via _expect: an f-string would index an empty list eagerly
        raise ConfigError(f"{kp}.{unknown[0]}: unknown key")
    rt = seat.get("runtime")
    _expect(rt in RUNTIMES, f"{kp}.runtime: must be one of {sorted(RUNTIMES)}, got {rt!r}")
    _expect(isinstance(seat.get("model"), str) and seat["model"], f"{kp}.model: required string")
    roles = seat.get("roles", [])
    _expect(isinstance(roles, list) and all(isinstance(r, str) for r in roles), f"{kp}.roles: must be a list of strings")
    bad = [r for r in roles if r not in ROLES]
    if bad:
        raise ConfigError(f"{kp}.roles: unknown role {bad[0]!r}; valid: {sorted(ROLES)}")
    effort = seat.get("effort")
    if effort is not None:
        _expect(rt in EFFORT_LEVELS,
                f"{kp}.effort: not accepted for runtime {rt!r} (agy encodes effort in the model id; claude-code has no effort knob)")
        _expect(effort in EFFORT_LEVELS[rt], f"{kp}.effort: {effort!r} not valid for {rt}; valid: {sorted(EFFORT_LEVELS[rt])}")
    args = seat.get("args", [])
    _expect(isinstance(args, list) and all(isinstance(a, str) for a in args), f"{kp}.args: must be a list of strings")
    provider = seat.get("provider", {})
    _expect(isinstance(provider, dict), f"{kp}.provider: must be an object")
    policy = seat.get("policy", "")
    _expect(isinstance(policy, str), f"{kp}.policy: must be a string")
    enabled = seat.get("enabled", True)
    _expect(isinstance(enabled, bool), f"{kp}.enabled: must be true or false")
    return {"runtime": rt, "model": seat["model"], "effort": effort, "roles": roles,
            "args": args, "provider": provider, "policy": policy, "enabled": enabled}


def load_config(path=None):
    p = config_path(path)
    try:
        raw = Path(p).read_text()
    except OSError as e:
        raise ConfigError(f"{p}: {e.strerror}") from e
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ConfigError(f"{p}: invalid JSON at line {e.lineno} column {e.colno}: {e.msg}") from e
    _expect(isinstance(obj, dict), "top level must be an object")
    unknown = sorted(set(obj) - TOP_KEYS)
    if unknown:
        raise ConfigError(f"{unknown[0]}: unknown key")
    version = obj.get("schema_version", 1)
    _expect(_is_int(version) and version == 1, f"schema_version: unsupported {version!r} (this version reads 1)")
    seats_in = obj.get("seats")
    _expect(isinstance(seats_in, dict) and seats_in, "seats: required non-empty object")
    seats = {name: _validate_seat(name, s) for name, s in seats_in.items()}
    for k in ("state_dir", "metrics_log", "commands_dir", "bin_dir"):
        if k in obj:
            _expect(isinstance(obj[k], str) and obj[k].strip(), f"{k}: must be a non-empty string path")
    state_dir = _abs(obj.get("state_dir", "~/.local/state/balancewheel"))
    metrics_log = _abs(obj["metrics_log"]) if obj.get("metrics_log") else os.path.join(state_dir, "metrics.jsonl")
    watches = dict(WATCH_DEFAULTS)
    w_in = obj.get("watches", {})
    _expect(isinstance(w_in, dict), "watches: must be an object")
    for k, v in w_in.items():
        _expect(k in WATCH_DEFAULTS, f"watches.{k}: unknown key; valid: {sorted(WATCH_DEFAULTS)}")
        _expect(_is_int(v) and v >= 0, f"watches.{k}: must be a non-negative integer")
        watches[k] = v
    _expect(watches["redundant_min_panels"] >= 1, "watches.redundant_min_panels: must be at least 1")
    return {
        "schema_version": 1,
        "path": p,
        "seats": seats,
        "state_dir": state_dir,
        "metrics_log": metrics_log,
        "commands_dir": _abs(obj.get("commands_dir", "~/.claude/commands")),
        "bin_dir": _abs(obj.get("bin_dir", "~/.local/bin")),
        "watches": watches,
    }


if __name__ == "__main__":
    import sys
    try:
        print(json.dumps(load_config(sys.argv[1] if len(sys.argv) > 1 else None), indent=2))
    except ConfigError as e:
        sys.exit(f"config error: {e}")
```

`balancewheel.example.json` (placeholder rows — the user replaces them):

```json
{
  "_comment": "Replace these rows with your seats. Seat names are yours; model ids are data and are expected to change. Copy to ~/.config/balancewheel/config.json.",
  "schema_version": 1,
  "seats": {
    "sol":   {"runtime": "codex",       "model": "gpt-5.6-sol",   "effort": "high",   "roles": ["review"],
              "policy": "why this seat has these roles; the evidence cited; what would change them"},
    "ox":    {"runtime": "pi",          "model": "openrouter/z-ai/glm-5.3-flash", "effort": "medium",
              "roles": ["plan", "review", "decision"],
              "provider": {"reasoning": true, "contextWindow": 1310720, "maxTokens": 131072}},
    "fable": {"runtime": "claude-code", "model": "claude-fable-5-1",
              "roles": ["design", "plan", "review", "debug", "decision"]},
    "gem":   {"runtime": "agy",         "model": "gemini-3.1-pro-high", "roles": ["review"]},
    "kimi":  {"runtime": "kimi",        "model": "kimi-code/k3", "roles": ["plan", "review"], "enabled": false}
  },
  "state_dir": "~/.local/state/balancewheel",
  "metrics_log": "~/.local/state/balancewheel/metrics.jsonl",
  "commands_dir": "~/.claude/commands",
  "bin_dir": "~/.local/bin",
  "watches": {
    "bloat_total": 30, "bloat_per_seat": 8, "bloat_min_seats": 3,
    "skim_min_seats": 3, "skim_min_findings": 15,
    "redundant_min_panels": 5, "redundant_window_panels": 10
  }
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_config -v`
Expected: all 11 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add metrics/config.py balancewheel.example.json tests/fixtures/config.json tests/test_config.py
git commit -m "feat: config loader with key-path errors and the example config"
```

---

### Task 2: Store — read, normalise, fold, append

**Files:**
- Create: `metrics/store.py`
- Create: `tests/fixtures/modern.jsonl`, `tests/fixtures/legacy.jsonl`, `tests/fixtures/truncated.jsonl`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `config.load_config` (only for `log_path`).
- Produces: `read_events`, `load`, `append`, `watch_id`, `legacy_watch_id`, `panel_id_for`, `log_path`. `load()` returns `{"usage": [...], "panels": [...], "watches": [...], "events": [...], "warnings": [...]}` where each panel has `id`, `ts`, `kind`, `target`, `seats` (dict name→model|None), `findings` (dict seat→{total,confirmed,refuted,partial,unique} with ints or `None`), `findings_detail` (list or `None`), `findings_raw`/`findings_after_triage` (int or `None`), `disputes` (list), `notes`, `amendments` (list). Each watch has `id`, `ts`, `kind`, `target`, `panel_id` (or `None`), `status`, `detail`, `updates` (list).

- [ ] **Step 1: Write the fixtures**

`tests/fixtures/modern.jsonl` (one JSON object per line; shown pretty here for the plan only — write them as single lines):

```json
{"type":"usage","ts":"2026-01-02T10:00:00Z","seat":"alpha","model":"vendor/model-a","mode":"new","dir":"/work/example-repo","duration_s":120,"ok":true}
{"type":"usage","ts":"2026-01-02T10:00:01Z","seat":"beta","model":"router/vendor/model-b","mode":"new","dir":"/work/example-repo","duration_s":300,"ok":true}
{"type":"usage","ts":"2026-01-03T10:00:00Z","seat":"beta","model":"router/vendor/model-b","mode":"resume","dir":"/work/example-repo","duration_s":90,"ok":false,"reason":"timeout"}
{"type":"panel","ts":"2026-01-02T11:00:00Z","id":"p0000001","target":"example change A","kind":"review","seats":{"alpha":"vendor/model-a","beta":"router/vendor/model-b"},"rounds":1,"immediate_agreement":true,"findings":{"alpha":{"total":2,"confirmed":2,"refuted":0,"partial":0,"unique":1},"beta":{"total":2,"confirmed":1,"refuted":1,"partial":0,"unique":0}},"findings_detail":[{"seat":"alpha","group":"g1","title":"null deref","claim":"x may be null","ref":"src/a.py:10","severity":"major","verdict":"confirmed","evidence":"read the code","action":"guarded"},{"seat":"beta","group":"g1","title":"null deref","claim":"x may be null","ref":"src/a.py:10","severity":"major","verdict":"confirmed","evidence":"read the code","action":"guarded"},{"seat":"alpha","group":"g2","title":"off by one","claim":"loop bound","ref":"src/b.py:3","severity":"minor","verdict":"confirmed","evidence":"traced","action":"fixed"},{"seat":"beta","group":"g3","title":"unused import","claim":"import os unused","ref":"src/c.py:1","severity":"nit","verdict":"refuted","evidence":"it is used at line 40","action":"none"}],"disputes":[],"notes":""}
{"type":"panel","ts":"2026-01-05T11:00:00Z","id":"p0000002","target":"example change B","kind":"plan","seats":{"alpha":"vendor/model-a","beta":"router/vendor/model-b2"},"rounds":2,"immediate_agreement":false,"findings":{"alpha":{"total":1,"confirmed":0,"refuted":0,"partial":1,"unique":0},"beta":{"total":1,"confirmed":1,"refuted":0,"partial":0,"unique":1}},"findings_detail":[{"seat":"alpha","group":"g4","title":"schema gap","claim":"field missing","ref":"Plan step 3","severity":"major","verdict":"partial","evidence":"field exists but optional","action":"made required"},{"seat":"beta","group":"g5","title":"race","claim":"two writers","ref":"Plan step 5","severity":"critical","verdict":"confirmed","evidence":"no lock","action":"added lock"}],"disputes":[{"summary":"is the field required","challenger":"alpha","proposals":{"alpha":"required","beta":"optional"},"winner":"alpha","reason":"callers assume it"}],"notes":"model of beta changed"}
{"type":"watch","ts":"2026-01-05T11:00:01Z","id":"w0000001","kind":"verification-skim","target":"example chan","panel_id":"p0000002","status":"open","detail":"2 seats, 2 findings, 0 refuted"}
{"type":"watch-update","ts":"2026-01-06T09:00:00Z","watch_id":"w0000001","status":"resolved","note":"re-verified a sample"}
{"type":"panel-amend","ts":"2026-01-06T09:30:00Z","panel_id":"p0000001","notes":"amended note","note":"added a note"}
```

`tests/fixtures/legacy.jsonl` — the shapes an older logger wrote (no ids, seats as a list, no partial/unique, amend by `panel_ts`, watch without id):

```json
{"type":"usage","ts":"2025-12-01T10:00:00Z","seat":"alpha","mode":"new","dir":"/work/old-repo","duration_s":60,"ok":true}
{"type":"panel","ts":"2025-12-01T11:00:00Z","target":"old change","kind":"code","seats":["alpha","beta"],"rounds":1,"immediate_agreement":true,"findings":{"alpha":{"total":3,"confirmed":3,"refuted":0},"beta":{"total":2,"confirmed":2,"refuted":0}},"disputes":[]}
{"type":"watch","ts":"2025-12-01T11:00:01Z","kind":"verification-skim","target":"old change","detail":"legacy watch"}
{"type":"watch","ts":"2025-12-01T11:00:02Z","id":"perf0001","kind":"panel-bloat","target":"old change","status":"performed","detail":"legacy performed watch"}
{"type":"watch","ts":"2025-12-01T11:00:03Z","kind":"panel-bloat","auto":true,"target":"older change","summary":"auto-form legacy watch"}
{"type":"watch-update","ts":"2025-12-02T09:00:00Z","watch_id":"LEGACYID","status":"dismissed","note":"was fine"}
{"type":"panel-amend","ts":"2025-12-03T09:00:00Z","panel_ts":"2025-12-01T11:00:00Z","findings_detail":[{"seat":"alpha","group":"g1","title":"a","claim":"a","severity":"major","verdict":"confirmed"}],"note":"backfilled"}
{"type":"panel-amend","ts":"2025-12-03T09:01:00Z","panel_ts":"1999-01-01T00:00:00Z","notes":"orphan","note":"targets nothing"}
{"type":"watch-update","ts":"2025-12-03T09:02:00Z","watch_id":"nope0000","status":"resolved","note":"targets nothing"}
```

Before writing `legacy.jsonl`, compute `LEGACYID` with `python3 -c 'import hashlib;print(hashlib.sha1("2025-12-01T11:00:01Z|verification-skim|old change".encode()).hexdigest()[:8])'` and substitute the 8-character result into the `watch-update` line.

`tests/fixtures/truncated.jsonl` — the modern file's first four lines, then a final line cut mid-object:

```
…(first 4 lines of modern.jsonl)…
{"type":"panel","ts":"2026-01-07T11:00:00Z","id":"p0000003","target":"cut off
```

- [ ] **Step 2: Write the failing tests**

`tests/test_store.py`:

```python
import hashlib, json, os, sys, tempfile, threading, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "metrics"))
import store  # noqa: E402

FX = HERE / "fixtures"


class ReadAndLoad(unittest.TestCase):
    def test_modern_log_loads_and_folds(self):
        d = store.load(str(FX / "modern.jsonl"))
        self.assertEqual(len(d["usage"]), 3)
        self.assertEqual([p["id"] for p in d["panels"]], ["p0000001", "p0000002"])
        p1 = d["panels"][0]
        self.assertEqual(p1["seats"], {"alpha": "vendor/model-a", "beta": "router/vendor/model-b"})
        self.assertEqual(p1["notes"], "amended note")
        self.assertEqual(p1["amendments"][0]["note"], "added a note")
        self.assertEqual(p1["findings"]["alpha"]["unique"], 1)
        w = d["watches"][0]
        self.assertEqual(w["id"], "w0000001")
        self.assertEqual(w["status"], "resolved")
        self.assertEqual(w["updates"][0]["note"], "re-verified a sample")
        self.assertEqual(d["warnings"], [])

    def test_legacy_log_normalises_without_error(self):
        d = store.load(str(FX / "legacy.jsonl"))
        p = d["panels"][0]
        self.assertEqual(p["id"], hashlib.sha1(b"2025-12-01T11:00:00Z").hexdigest()[:8])
        self.assertEqual(p["seats"], {"alpha": None, "beta": None})
        self.assertIsNone(p["findings"]["alpha"]["partial"])
        self.assertIsNone(p["findings"]["alpha"]["unique"])
        self.assertEqual(p["findings"]["alpha"]["confirmed"], 3)
        self.assertEqual(len(p["findings_detail"]), 1)  # from the panel_ts amend
        w = d["watches"][0]
        self.assertEqual(w["id"], store.legacy_watch_id("2025-12-01T11:00:01Z", "verification-skim", "old change"))
        self.assertEqual(w["status"], "dismissed")
        self.assertIsNone(w["panel_id"])
        self.assertEqual(d["watches"][1]["status"], "resolved")  # legacy "performed" maps to resolved
        self.assertEqual(d["watches"][2]["status"], "resolved")  # legacy auto:true with no status, too
        self.assertEqual(d["watches"][2]["detail"], "auto-form legacy watch")  # legacy "summary" → detail
        self.assertEqual(len(d["warnings"]), 2)  # orphan amend + orphan watch-update are warned, not dropped silently
        self.assertTrue(any("unknown panel" in w for w in d["warnings"]))
        self.assertTrue(any("unknown watch" in w for w in d["warnings"]))

    def test_truncated_final_line_is_skipped_with_warning(self):
        d = store.load(str(FX / "truncated.jsonl"))
        self.assertEqual(len(d["panels"]), 1)
        self.assertEqual(len(d["warnings"]), 1)
        self.assertIn("line 5", d["warnings"][0])

    def test_missing_file_is_empty_not_error(self):
        d = store.load("/nonexistent/path/metrics.jsonl")
        self.assertEqual(d["events"], [])
        self.assertEqual(d["warnings"], [])

    def test_non_object_lines_and_cut_multibyte_tail_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "m.jsonl"
            good = json.dumps({"type": "usage", "seat": "alpha"})
            p.write_bytes((good + "\nnull\n[]\n" + '{"type":"usage","seat":"bé"').encode("utf-8")[:-1])
            d = store.load(str(p))
            self.assertEqual(len(d["usage"]), 1)
            self.assertEqual(len(d["warnings"]), 3)

    def test_bool_and_float_counts_become_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "m.jsonl"
            p.write_text(json.dumps({"type": "panel", "ts": "2026-01-01T00:00:00Z", "seats": ["a"],
                                     "findings": {"a": {"total": True, "confirmed": 1.9, "refuted": 2}}}) + "\n")
            f = store.load(str(p))["panels"][0]["findings"]["a"]
            self.assertIsNone(f["total"]); self.assertIsNone(f["confirmed"]); self.assertEqual(f["refuted"], 2)


class Ids(unittest.TestCase):
    def test_watch_id_is_stable_and_timestamp_free(self):
        a = store.watch_id("panel-bloat", "t", "p1")
        self.assertEqual(a, store.watch_id("panel-bloat", "t", "p1"))
        self.assertEqual(len(a), 8)
        self.assertNotEqual(a, store.watch_id("panel-bloat", "t", "p2"))


class Append(unittest.TestCase):
    def test_append_writes_one_line_and_creates_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "sub", "m.jsonl")
            store.append(p, {"type": "usage", "seat": "alpha"})
            store.append(p, {"type": "usage", "seat": "beta"})
            lines = Path(p).read_text().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[1])["seat"], "beta")
            self.assertTrue(json.loads(lines[0])["ts"].endswith("Z"))

    def test_concurrent_appends_do_not_interleave(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "m.jsonl")
            payload = {"type": "usage", "dir": "x" * 20000}
            threads = [threading.Thread(target=store.append, args=(p, dict(payload, seat=str(i)))) for i in range(20)]
            for t in threads: t.start()
            for t in threads: t.join()
            events, warnings = store.read_events(p)
            self.assertEqual(len(events), 20)
            self.assertEqual(warnings, [])


class LogPath(unittest.TestCase):
    def test_env_overrides_config(self):
        os.environ["BALANCEWHEEL_METRICS_LOG"] = "/tmp/x.jsonl"
        try:
            self.assertEqual(store.log_path({"metrics_log": "/elsewhere"}), "/tmp/x.jsonl")
        finally:
            del os.environ["BALANCEWHEEL_METRICS_LOG"]
        self.assertEqual(store.log_path({"metrics_log": "/elsewhere"}), "/elsewhere")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_store -v`
Expected: ERROR `No module named 'store'`.

- [ ] **Step 4: Write `metrics/store.py`**

```python
"""store.py — the one reader (and the one appender) for the metrics JSONL.

Append-only. Later events amend earlier ones:
  watch-update  {"type":"watch-update","watch_id":…,"status":"open|resolved|dismissed","note":…}
  panel-amend   {"type":"panel-amend","panel_id":… (legacy: "panel_ts"),"findings_detail":[…],…,"note":…}
load() folds these and normalises legacy shapes; nothing else should parse the raw lines.
"""
import fcntl
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

PANEL_AMENDABLE = ("findings_detail", "findings", "findings_raw", "findings_after_triage", "notes")
COUNT_KEYS = ("total", "confirmed", "refuted", "partial", "unique")
WATCH_STATUSES = ("open", "resolved", "dismissed")


def now_ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_path(config=None):
    env = os.environ.get("BALANCEWHEEL_METRICS_LOG")
    if env:
        return env
    if config and config.get("metrics_log"):
        return config["metrics_log"]
    return os.path.expanduser("~/.local/state/balancewheel/metrics.jsonl")


def _sha8(s):
    return hashlib.sha1(s.encode()).hexdigest()[:8]


def watch_id(kind, target, panel_id):
    return _sha8(f"{kind}|{target}|{panel_id}")


def legacy_watch_id(ts, kind, target):
    return _sha8(f"{ts}|{kind}|{target}")


def panel_id_for(event):
    return event.get("id") or _sha8(event.get("ts", ""))


def read_events(path):
    p = Path(path)
    if not p.exists():
        return [], []
    events, warnings = [], []
    # append() writes UTF-8. Decode with replacement so a write cut inside a multibyte character
    # becomes an unparseable last line (skipped with a warning) instead of a UnicodeDecodeError.
    lines = p.read_bytes().decode("utf-8", errors="replace").split("\n")
    last_index = max((i for i, l in enumerate(lines) if l.strip()), default=-1)
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            kind = "truncated final line" if i == last_index else "unparseable line"
            warnings.append(f"{kind} {i + 1} skipped")
            continue
        if not isinstance(obj, dict) or not isinstance(obj.get("type"), str):
            warnings.append(f"line {i + 1} is not an event object; skipped")
            continue
        events.append(obj)
    return events, warnings


def locked(path):
    """Context manager: exclusive lock on <log>.lock for read-modify-append sequences (e.g. watch dedupe)."""
    import contextlib

    @contextlib.contextmanager
    def _cm():
        p = Path(str(path) + ".lock")
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return _cm()


def _is_count(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _normalise_counts(findings):
    """Counts are ints or None ("not recorded"). Bools and floats are corrupt data, not counts."""
    out = {}
    if not isinstance(findings, dict):
        return out
    for seat, c in findings.items():
        c = c if isinstance(c, dict) else {}
        out[seat] = {k: (c[k] if _is_count(c.get(k)) else None) for k in COUNT_KEYS}
    return out


def _normalise_panel(e):
    seats = e.get("seats") or {}
    if isinstance(seats, list):
        seats = {s: None for s in seats}
    return {
        "type": "panel",
        "id": panel_id_for(e),
        "ts": e.get("ts"),
        "target": e.get("target", ""),
        "kind": e.get("kind"),
        "seats": dict(seats),
        "rounds": e.get("rounds"),
        "immediate_agreement": e.get("immediate_agreement"),
        "findings": _normalise_counts(e.get("findings")),
        "findings_raw": e.get("findings_raw"),
        "findings_after_triage": e.get("findings_after_triage"),
        "findings_detail": e.get("findings_detail"),
        "disputes": list(e.get("disputes") or []),
        "notes": e.get("notes", ""),
        "amendments": [],
    }


def _normalise_watch(e):
    wid = e.get("id") or legacy_watch_id(e.get("ts", ""), e.get("kind", ""), e.get("target", ""))
    raw_status = e.get("status")
    if raw_status == "performed" or (raw_status is None and e.get("auto") is True):
        raw_status = "resolved"  # older loggers: "performed", or auto:true with no status, = already done
    status = raw_status if raw_status in WATCH_STATUSES else "open"
    return {
        "type": "watch", "id": wid, "ts": e.get("ts"), "kind": e.get("kind"),
        "target": e.get("target", ""), "panel_id": e.get("panel_id"),
        "status": status, "detail": e.get("detail") or e.get("summary") or "", "updates": [],
    }


def load(path):
    events, warnings = read_events(path)
    usage = [e for e in events if e.get("type") == "usage"]
    panels = [_normalise_panel(e) for e in events if e.get("type") == "panel"]
    by_id, by_ts = {}, {}
    for p in panels:
        if p["id"] in by_id:
            warnings.append(f"duplicate panel id {p['id']}; amendments will attach to the last one")
        by_id[p["id"]] = p
        by_ts[p["ts"]] = p
    for e in events:
        if e.get("type") != "panel-amend":
            continue
        p = by_id.get(e.get("panel_id")) or by_ts.get(e.get("panel_ts"))
        if p is None:
            warnings.append(f"panel-amend at {e.get('ts')} targets an unknown panel")
            continue
        for k in PANEL_AMENDABLE:
            if k in e:
                p[k] = _normalise_counts(e[k]) if k == "findings" else e[k]
        p["amendments"].append({"ts": e.get("ts"), "note": e.get("note", "")})
    watches = [_normalise_watch(e) for e in events if e.get("type") == "watch"]
    w_by_id = {w["id"]: w for w in watches}
    for e in events:
        if e.get("type") != "watch-update":
            continue
        w = w_by_id.get(e.get("watch_id"))
        if w is None:
            warnings.append(f"watch-update at {e.get('ts')} targets unknown watch {e.get('watch_id')}")
            continue
        if e.get("status") in WATCH_STATUSES:
            w["status"] = e["status"]
        w["updates"].append({"ts": e.get("ts"), "status": e.get("status"), "note": e.get("note", "")})
    return {"usage": usage, "panels": panels, "watches": watches, "events": events, "warnings": warnings}


def append(path, event):
    """One line, one write(), under an exclusive lock. Adds ts if missing.
    flush() without fsync(): survives a process crash, not a power loss — acceptable for metrics."""
    event = dict(event)
    event.setdefault("ts", now_ts())
    line = json.dumps(event, ensure_ascii=False) + "\n"
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(line)
            f.flush()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_store -v`
Expected: all 10 tests PASS. If `test_legacy_log_normalises_without_error` fails on the watch status, the `LEGACYID` substitution in the fixture was skipped — recompute it.

- [ ] **Step 6: Commit**

```bash
git add metrics/store.py tests/fixtures/modern.jsonl tests/fixtures/legacy.jsonl tests/fixtures/truncated.jsonl tests/test_store.py
git commit -m "feat: metrics store with legacy normalisation, amendment folding, locked appends"
```

---

### Task 3: Roster watches

**Files:**
- Create: `metrics/watches.py`
- Test: `tests/test_watches.py`

**Interfaces:**
- Consumes: normalised panels from `store.load()["panels"]`; `config["watches"]`; `store.watch_id`.
- Produces: `evaluate(panels, config) -> list[dict]` returning candidate watch events shaped `{"type":"watch","id","kind","target","panel_id","status","detail"}` (no `ts`; the caller appends). `status` is `"resolved"` for a performed bloat triage, else `"open"`. Also `should_refire(candidate, data, config) -> bool` (`data` is a `store.load()` result), which the logger consults before appending a `redundant-seat` candidate.

- [ ] **Step 1: Write the failing tests**

`tests/test_watches.py`:

```python
import sys, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "metrics"))
import watches  # noqa: E402

W = {"bloat_total": 30, "bloat_per_seat": 8, "bloat_min_seats": 3,
     "skim_min_seats": 3, "skim_min_findings": 15,
     "redundant_min_panels": 5, "redundant_window_panels": 10}
CFG = {"watches": W, "seats": {"a": {}, "b": {}, "c": {}}}


def panel(pid, seats, counts, raw=None, triaged=None):
    return {"id": pid, "ts": f"2026-01-{int(pid[1:]):02d}T00:00:00Z", "target": f"t{pid}",
            "seats": {s: None for s in seats},
            "findings": {s: dict(total=t, confirmed=c, refuted=r, partial=p, unique=u)
                         for s, (t, c, r, p, u) in counts.items()},
            "findings_raw": raw, "findings_after_triage": triaged}


class Bloat(unittest.TestCase):
    def test_fires_on_total_and_marks_performed_when_triaged(self):
        p = panel("p1", "abc", {"a": (12, 12, 0, 0, 1), "b": (10, 10, 0, 0, 1), "c": (10, 10, 0, 0, 1)}, raw=40, triaged=20)
        out = [w for w in watches.evaluate([p], CFG) if w["kind"] == "panel-bloat"]
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["status"], "resolved")
        self.assertIn("performed", out[0]["detail"])

    def test_fires_on_per_seat_average_with_min_seats_and_suggests_when_not_triaged(self):
        p = panel("p1", "abc", {"a": (9, 9, 0, 0, 1), "b": (8, 8, 0, 0, 1), "c": (8, 8, 0, 0, 1)})
        out = [w for w in watches.evaluate([p], CFG) if w["kind"] == "panel-bloat"]
        self.assertEqual(out[0]["status"], "open")
        p2 = panel("p2", "ab", {"a": (9, 9, 0, 0, 1), "b": (9, 9, 0, 0, 1)})
        self.assertEqual([w for w in watches.evaluate([p2], CFG) if w["kind"] == "panel-bloat"], [])

    def test_below_threshold_is_silent(self):
        p = panel("p1", "abc", {"a": (5, 5, 0, 0, 1), "b": (5, 5, 0, 0, 1), "c": (5, 5, 0, 0, 1)})
        self.assertEqual([w for w in watches.evaluate([p], CFG) if w["kind"] == "panel-bloat"], [])


class Skim(unittest.TestCase):
    def test_fires_when_nothing_refuted_or_partial(self):
        p = panel("p1", "abc", {"a": (5, 5, 0, 0, 1), "b": (5, 5, 0, 0, 1), "c": (5, 5, 0, 0, 1)})
        out = [w for w in watches.evaluate([p], CFG) if w["kind"] == "verification-skim"]
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["status"], "open")

    def test_partial_counts_as_verification(self):
        p = panel("p1", "abc", {"a": (5, 4, 0, 1, 1), "b": (5, 5, 0, 0, 1), "c": (5, 5, 0, 0, 1)})
        self.assertEqual([w for w in watches.evaluate([p], CFG) if w["kind"] == "verification-skim"], [])

    def test_legacy_none_partial_treated_as_zero(self):
        p = panel("p1", "abc", {"a": (5, 5, 0, None, None), "b": (5, 5, 0, None, None), "c": (5, 5, 0, None, None)})
        out = [w for w in watches.evaluate([p], CFG) if w["kind"] == "verification-skim"]
        self.assertEqual(len(out), 1)


class Redundant(unittest.TestCase):
    def test_fires_after_min_panels_with_zero_unique(self):
        ps = [panel(f"p{i}", "abc", {"a": (3, 3, 0, 0, 0), "b": (3, 3, 0, 0, 1), "c": (3, 3, 0, 0, 1)}) for i in range(1, 6)]
        out = [w for w in watches.evaluate(ps, CFG) if w["kind"] == "redundant-seat"]
        self.assertEqual([w["target"] for w in out], ["a"])
        self.assertEqual(out[0]["panel_id"], "p5")

    def test_null_unique_panels_are_excluded_not_counted_as_zero(self):
        ps = [panel(f"p{i}", "abc", {"a": (3, 3, 0, 0, None), "b": (3, 3, 0, 0, 1), "c": (3, 3, 0, 0, 1)}) for i in range(1, 6)]
        self.assertEqual([w for w in watches.evaluate(ps, CFG) if w["kind"] == "redundant-seat"], [])

    def test_window_limits_lookback(self):
        old = [panel(f"p{i}", "abc", {"a": (3, 3, 0, 0, 0), "b": (3, 3, 0, 0, 1), "c": (3, 3, 0, 0, 1)}) for i in range(1, 6)]
        new = [panel(f"p{i}", "bc", {"b": (3, 3, 0, 0, 1), "c": (3, 3, 0, 0, 1)}) for i in range(6, 17)]
        out = [w for w in watches.evaluate(old + new, CFG) if w["kind"] == "redundant-seat"]
        self.assertEqual(out, [])  # a's zero-unique panels fell outside the 10-panel window


class Ids(unittest.TestCase):
    def test_ids_are_deterministic_across_runs(self):
        p = panel("p1", "abc", {"a": (5, 5, 0, 0, 1), "b": (5, 5, 0, 0, 1), "c": (5, 5, 0, 0, 1)})
        a = watches.evaluate([p], CFG); b = watches.evaluate([p], CFG)
        self.assertEqual([w["id"] for w in a], [w["id"] for w in b])


class Edges(unittest.TestCase):
    def test_zero_min_panels_does_not_crash_and_never_fires(self):
        cfg = {"watches": dict(W, redundant_min_panels=0), "seats": {"a": {}}}
        self.assertEqual(watches.evaluate([], cfg), [])
        p = panel("p1", "a", {"a": (3, 3, 0, 0, 0)})
        self.assertEqual([w for w in watches.evaluate([p], cfg) if w["kind"] == "redundant-seat"], [])

    def test_seat_count_uses_declared_seats_when_counts_are_missing(self):
        p = panel("p1", "abc", {"a": (20, 20, 0, 0, 1)})  # b and c sat but have no counts (legacy)
        out = [w for w in watches.evaluate([p], CFG) if w["kind"] == "panel-bloat"]
        self.assertEqual(out, [])  # 20 raw / 3 seats < 8 per seat; would have fired at 20/1


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_watches -v`
Expected: ERROR `No module named 'watches'`.

- [ ] **Step 3: Write `metrics/watches.py`**

```python
"""watches.py — roster self-checks over logged panels. Pure functions; no I/O.

panel-bloat        raw >= bloat_total, or avg/seat >= bloat_per_seat with >= bloat_min_seats.
                   'resolved' (performed) when findings_raw/findings_after_triage are recorded,
                   else 'open' (suggest triage).
verification-skim  >= skim_min_seats seats, >= skim_min_findings findings, 0 refuted, 0 partial
                   (None partial on legacy panels counts as 0). Suggests re-verifying a sample.
redundant-seat     a seat with unique == 0 on >= redundant_min_panels of its panels within the
                   last redundant_window_panels panels; panels with unique None are excluded.
Watch ids are sha1(kind|target|panel_id)[:8] — no timestamp — so re-running is idempotent for
panel-scoped watches. redundant-seat's id moves with the last panel, so the caller (log.py
run_check) applies the re-fire rule: never while one is open for that seat, and after a
dismissal only once redundant_min_panels further panels have passed (see should_refire).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from store import legacy_watch_id, watch_id  # noqa: E402


def _is_count(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _n(v):
    return v if _is_count(v) else 0


def _sum(panel, key):
    return sum(_n(c.get(key)) for c in panel["findings"].values())


def _seat_count(panel):
    return max(len(panel["seats"]), len(panel["findings"]))


def _bloat(panel, w):
    seats = _seat_count(panel)
    raw = panel.get("findings_raw") if _is_count(panel.get("findings_raw")) else _sum(panel, "total")
    avg = raw / seats if seats else 0
    if not (raw >= w["bloat_total"] or (seats >= w["bloat_min_seats"] and avg >= w["bloat_per_seat"])):
        return None
    performed = _is_count(panel.get("findings_raw")) and _is_count(panel.get("findings_after_triage"))
    if performed:
        detail = (f"{seats} seats produced {raw} raw findings; triage performed → "
                  f"{panel['findings_after_triage']} verified")
    else:
        detail = f"{seats} seats produced {raw} raw findings with no triage recorded — dedupe, drop nits, re-log with findings_raw/findings_after_triage"
    return {"type": "watch", "id": watch_id("panel-bloat", panel["target"], panel["id"]),
            "kind": "panel-bloat", "target": panel["target"], "panel_id": panel["id"],
            "status": "resolved" if performed else "open", "detail": detail}


def _skim(panel, w):
    seats = _seat_count(panel)
    total = _sum(panel, "total")
    if seats < w["skim_min_seats"] or total < w["skim_min_findings"]:
        return None
    if _sum(panel, "refuted") or _sum(panel, "partial"):
        return None
    return {"type": "watch", "id": watch_id("verification-skim", panel["target"], panel["id"]),
            "kind": "verification-skim", "target": panel["target"], "panel_id": panel["id"], "status": "open",
            "detail": f"{seats} seats, {total} findings, 0 refuted and 0 partial — re-verify a sample and report which seat's findings failed"}


def _redundant(panels, config):
    w = config["watches"]
    if w["redundant_min_panels"] < 1:  # 0 would make every seat "redundant" on an empty window
        return []
    window = panels[-w["redundant_window_panels"]:] if w["redundant_window_panels"] else panels
    out = []
    seats = set(config.get("seats", {})) | {s for p in window for s in p["findings"]}
    for seat in sorted(seats):
        mine = [p for p in window if seat in p["findings"] and isinstance(p["findings"][seat].get("unique"), int)]
        if len(mine) < w["redundant_min_panels"]:
            continue
        recent = mine[-w["redundant_min_panels"]:]
        if any(p["findings"][seat]["unique"] > 0 for p in recent):
            continue
        last = recent[-1]["id"]
        excluded = sum(1 for p in window if seat in p["findings"] and not isinstance(p["findings"][seat].get("unique"), int))
        note = f" ({excluded} panel(s) without a recorded unique count excluded)" if excluded else ""
        out.append({"type": "watch", "id": watch_id("redundant-seat", seat, last),
                    "kind": "redundant-seat", "target": seat, "panel_id": last, "status": "open",
                    "detail": f"seat '{seat}' has 0 unique catches over its last {len(recent)} panels{note} — consider dropping it from the default roster (your call, never automatic)"})
    return out


def should_refire(candidate, data, config):
    """For redundant-seat: False while an open watch exists for the seat; after a resolved or
    dismissed one, True only once redundant_min_panels further panels have been *logged after the
    decision* (counted in log order, so same-second timestamps cannot confuse it)."""
    if candidate["kind"] != "redundant-seat":
        return True
    same = [w for w in data["watches"] if w["kind"] == "redundant-seat" and w["target"] == candidate["target"]]
    if not same:
        return True
    if any(w["status"] == "open" for w in same):
        return False
    ids = {w["id"] for w in same}
    events = data["events"]
    last = -1
    for i, e in enumerate(events):
        if e.get("type") == "watch":
            eid = e.get("id") or legacy_watch_id(e.get("ts", ""), e.get("kind", ""), e.get("target", ""))
            if eid in ids:
                last = i
        elif e.get("type") == "watch-update" and e.get("watch_id") in ids:
            last = i
    since = sum(1 for e in events[last + 1:] if e.get("type") == "panel")
    return since >= config["watches"]["redundant_min_panels"]


def evaluate(panels, config):
    """Candidate watch events for the whole log; the caller dedupes against existing ids and
    applies should_refire() to redundant-seat candidates."""
    w = config["watches"]
    panels = sorted(panels, key=lambda p: p.get("ts") or "")
    out = []
    for p in panels:
        for fn in (_bloat, _skim):
            r = fn(p, w)
            if r:
                out.append(r)
    out.extend(_redundant(panels, config))
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_watches -v`
Expected: all 12 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add metrics/watches.py tests/test_watches.py
git commit -m "feat: roster watches as pure functions with timestamp-free ids"
```

---

### Task 4: Logger CLI

**Files:**
- Create: `metrics/log.py`
- Test: `tests/test_log.py`

**Interfaces:**
- Consumes: `config.load_config`, `store.{load, append, log_path, watch_id, now_ts}`, `watches.evaluate`.
- Produces: the CLI used by Plans 2 and 3:
  - `log.py config [--json]` → resolved config as JSON on stdout (exit 2 on `ConfigError`, message on stderr).
  - `log.py usage --seat S --mode new|resume --dir D --duration N --ok true|false [--model M] [--reason R]`.
  - `log.py panel [--force] < record.json` → validates, computes `unique`, assigns `id`, appends, runs `check` for that panel, prints `panel <id> logged` and any `WATCH[kind|status] …` lines.
  - `log.py check` → appends new watches (deduped by id), prints them, prints the count of open suggestions.
  - `log.py watches` → table of watches (id, date, kind, status, target, last note).
  - `log.py resolve <id> [--status resolved|dismissed|open] [--note …]`.
  - `log.py amend --panel-id <id> < patch.json` → appends `panel-amend`, re-runs `check`.
  - Importable: `validate_panel(rec, config, force) -> dict`, `compute_counts(findings_detail) -> dict`.

- [ ] **Step 1: Write the failing tests**

`tests/test_log.py`:

```python
import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "metrics"))
import log as logmod  # noqa: E402
import store  # noqa: E402

LOG_PY = str(HERE.parent / "metrics" / "log.py")
CONFIG = str(HERE / "fixtures" / "config.json")


def run(args, stdin=None, env=None):
    e = dict(os.environ, BALANCEWHEEL_CONFIG=CONFIG, **(env or {}))
    return subprocess.run([sys.executable, LOG_PY, *args], input=stdin, capture_output=True, text=True, env=e)


def detail(seat, group, verdict="confirmed", severity="major"):
    return {"seat": seat, "group": group, "title": f"{group} title", "claim": "c", "ref": "f:1",
            "severity": severity, "verdict": verdict, "evidence": "e", "action": "a"}


def record(**over):
    rec = {"target": "change X", "kind": "review", "seats": ["alpha", "beta"], "rounds": 1,
           "immediate_agreement": True,
           "findings": {"alpha": {"total": 2, "confirmed": 2, "refuted": 0, "partial": 0, "unique": 1},
                        "beta": {"total": 1, "confirmed": 0, "refuted": 1, "partial": 0, "unique": 0}},
           "findings_detail": [detail("alpha", "g1"), detail("alpha", "g2"), detail("beta", "g2", "refuted")],
           "disputes": []}
    rec.update(over)
    return rec


class ComputeCounts(unittest.TestCase):
    def test_unique_is_confirmed_and_group_of_one(self):
        c = logmod.compute_counts([detail("alpha", "g1"), detail("alpha", "g2"), detail("beta", "g2", "refuted"),
                                   detail("beta", "g3", "dropped"), detail("beta", "g4", "partial")])
        self.assertEqual(c["alpha"], {"total": 2, "confirmed": 2, "refuted": 0, "partial": 0, "unique": 1})
        self.assertEqual(c["beta"], {"total": 2, "confirmed": 0, "refuted": 1, "partial": 1, "unique": 0})


class ValidatePanel(unittest.TestCase):
    def setUp(self):
        import config as cfg
        self.cfg = cfg.load_config(CONFIG)

    def test_accepts_and_fills_seats_models_and_unique(self):
        rec = logmod.validate_panel(record(), self.cfg, force=False)
        self.assertEqual(rec["seats"], {"alpha": "vendor/model-a", "beta": "router/vendor/model-b"})
        self.assertEqual(rec["findings"]["alpha"]["unique"], 1)
        self.assertEqual(len(rec["id"]), 8)

    def test_rejects_count_mismatch_unless_forced(self):
        bad = record(); bad["findings"]["alpha"]["confirmed"] = 1
        with self.assertRaises(ValueError) as e:
            logmod.validate_panel(bad, self.cfg, force=False)
        self.assertIn("alpha", str(e.exception))
        rec = logmod.validate_panel(bad, self.cfg, force=True)
        self.assertIn("count_mismatch", rec["notes"])

    def test_rejects_unknown_seat_unless_forced(self):
        bad = record(seats=["alpha", "beta", "zeta"])  # zeta is not in config; alpha/beta keep their findings
        with self.assertRaises(ValueError):
            logmod.validate_panel(bad, self.cfg, force=False)
        rec = logmod.validate_panel(bad, self.cfg, force=True)
        self.assertIsNone(rec["seats"]["zeta"])

    def test_rejects_bad_kind_verdict_severity_and_missing_detail(self):
        for bad in (record(kind="code"), record(findings_detail=None),
                    record(findings_detail=[dict(detail("alpha", "g1"), verdict="maybe")]),
                    record(findings_detail=[dict(detail("alpha", "g1"), severity="huge")])):
            with self.assertRaises(ValueError):
                logmod.validate_panel(bad, self.cfg, force=False)

    def test_dispute_shape_enforced(self):
        bad = record(disputes=[{"summary": "s", "challenger": "alpha"}])
        with self.assertRaises(ValueError) as e:
            logmod.validate_panel(bad, self.cfg, force=False)
        self.assertIn("proposals", str(e.exception))


class Cli(unittest.TestCase):
    def test_end_to_end_usage_panel_check_resolve_amend(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"BALANCEWHEEL_METRICS_LOG": os.path.join(tmp, "m.jsonl")}
            r = run(["usage", "--seat", "alpha", "--mode", "new", "--dir", "/work/x", "--duration", "5", "--ok", "true", "--model", "vendor/model-a"], env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            # a skim-worthy panel: 3 seats, 15 findings, none refuted
            fd = [detail(s, f"g{s}{i}") for s in ("alpha", "beta", "gamma") for i in range(5)]
            counts = {s: {"total": 5, "confirmed": 5, "refuted": 0, "partial": 0, "unique": 5} for s in ("alpha", "beta", "gamma")}
            rec = record(seats=["alpha", "beta", "gamma"], findings=counts, findings_detail=fd)
            r = run(["panel"], stdin=json.dumps(rec), env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("logged", r.stdout)
            self.assertIn("WATCH[verification-skim|open]", r.stdout)
            d = store.load(env["BALANCEWHEEL_METRICS_LOG"])
            self.assertEqual(len(d["watches"]), 1)
            wid = d["watches"][0]["id"]
            r = run(["check"], env=env)
            self.assertEqual(len(store.load(env["BALANCEWHEEL_METRICS_LOG"])["watches"]), 1)  # deduped
            r = run(["resolve", wid, "--note", "sample held"], env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(store.load(env["BALANCEWHEEL_METRICS_LOG"])["watches"][0]["status"], "resolved")
            r = run(["watches"], env=env)
            self.assertIn(wid, r.stdout); self.assertIn("resolved", r.stdout)
            pid = d["panels"][0]["id"]
            r = run(["amend", "--panel-id", pid], stdin=json.dumps({"notes": "amended", "note": "why"}), env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(store.load(env["BALANCEWHEEL_METRICS_LOG"])["panels"][0]["notes"], "amended")
            r = run(["amend", "--panel-id", pid], stdin=json.dumps({"findings_detail": [{"seat": "alpha", "group": "g"}]}), env=env)
            self.assertEqual(r.returncode, 2)  # malformed detail is rejected, not a traceback

    def test_amend_recording_triage_flips_open_bloat_watch_to_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"BALANCEWHEEL_METRICS_LOG": os.path.join(tmp, "m.jsonl")}
            fd = [detail(s, f"g{s}{i}", "refuted" if i == 0 else "confirmed") for s in ("alpha", "beta", "gamma") for i in range(11)]
            counts = {s: {"total": 11, "confirmed": 10, "refuted": 1, "partial": 0, "unique": 10} for s in ("alpha", "beta", "gamma")}
            rec = record(seats=["alpha", "beta", "gamma"], findings=counts, findings_detail=fd)
            r = run(["panel"], stdin=json.dumps(rec), env=env)
            self.assertIn("WATCH[panel-bloat|open]", r.stdout)
            d = store.load(env["BALANCEWHEEL_METRICS_LOG"])
            pid = d["panels"][0]["id"]
            r = run(["amend", "--panel-id", pid], stdin=json.dumps({"findings_raw": 60, "findings_after_triage": 33, "note": "triage recorded"}), env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("WATCH[panel-bloat|resolved] (was open)", r.stdout)
            w = [w for w in store.load(env["BALANCEWHEEL_METRICS_LOG"])["watches"] if w["kind"] == "panel-bloat"]
            self.assertEqual(len(w), 1); self.assertEqual(w[0]["status"], "resolved")

    def test_config_subcommand_prints_resolved_json_and_fails_on_bad_config(self):
        r = run(["config"])
        self.assertEqual(r.returncode, 0)
        self.assertIn("alpha", json.loads(r.stdout)["seats"])
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "c.json"; p.write_text("{}")
            r = subprocess.run([sys.executable, LOG_PY, "config"], capture_output=True, text=True,
                               env=dict(os.environ, BALANCEWHEEL_CONFIG=str(p)))
            self.assertEqual(r.returncode, 2)
            self.assertIn("seats", r.stderr)

    def test_panel_rejects_invalid_record_with_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"BALANCEWHEEL_METRICS_LOG": os.path.join(tmp, "m.jsonl")}
            for bad in (record(kind="nope"), record(target=""), record(findings={"alpha": {"total": "2"}}),
                        record(disputes=[{"summary": 1, "challenger": "alpha", "proposals": "x", "winner": "alpha", "reason": "r"}]),
                        {"target": "t"}, [1, 2]):
                r = run(["panel"], stdin=json.dumps(bad), env=env)
                self.assertEqual(r.returncode, 2, f"{bad!r}: {r.stderr}")
                self.assertNotIn("Traceback", r.stderr)
            self.assertFalse(os.path.exists(env["BALANCEWHEEL_METRICS_LOG"]))

    def test_unique_mismatch_is_rejected_unless_forced(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"BALANCEWHEEL_METRICS_LOG": os.path.join(tmp, "m.jsonl")}
            bad = record(); bad["findings"]["alpha"]["unique"] = 2  # computed is 1
            r = run(["panel"], stdin=json.dumps(bad), env=env)
            self.assertEqual(r.returncode, 2); self.assertIn("alpha.unique", r.stderr)
            r = run(["panel", "--force"], stdin=json.dumps(bad), env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            p = store.load(env["BALANCEWHEEL_METRICS_LOG"])["panels"][0]
            self.assertEqual(p["findings"]["alpha"]["unique"], 1); self.assertIn("count_mismatch", p["notes"])

    def test_usage_requires_fields_and_valid_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"BALANCEWHEEL_METRICS_LOG": os.path.join(tmp, "m.jsonl")}
            r = run(["usage", "--seat", "alpha", "--mode", "new"], env=env)
            self.assertEqual(r.returncode, 2)
            r = run(["usage", "--seat", "alpha", "--mode", "new", "--dir", "/w", "--duration", "1", "--ok", "typo"], env=env)
            self.assertEqual(r.returncode, 2)
            self.assertFalse(os.path.exists(env["BALANCEWHEEL_METRICS_LOG"]))

    def test_redundant_seat_watch_fires_once_and_respects_dismissal(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"BALANCEWHEEL_METRICS_LOG": os.path.join(tmp, "m.jsonl")}

            def log_panel(i):
                # beta never has a unique catch (shares every group with alpha); alpha always does
                fd = [detail("alpha", f"g{i}a"), detail("alpha", f"g{i}s"), detail("beta", f"g{i}s")]
                counts = {"alpha": {"total": 2, "confirmed": 2, "refuted": 0, "partial": 0, "unique": 1},
                          "beta": {"total": 1, "confirmed": 1, "refuted": 0, "partial": 0, "unique": 0}}
                r = run(["panel"], stdin=json.dumps(record(target=f"change {i}", findings=counts, findings_detail=fd)), env=env)
                self.assertEqual(r.returncode, 0, r.stderr)

            def redundant():
                return [w for w in store.load(env["BALANCEWHEEL_METRICS_LOG"])["watches"] if w["kind"] == "redundant-seat"]

            for i in range(7):
                log_panel(i)
            run(["check"], env=env)
            ws = redundant()
            self.assertEqual(len(ws), 1)                      # fired once at panel 5, not again at 6 and 7
            self.assertEqual(ws[0]["target"], "beta")
            run(["resolve", ws[0]["id"], "--status", "dismissed", "--note", "keep beta"], env=env)
            for i in range(7, 10):
                log_panel(i)
            self.assertEqual(len(redundant()), 1)             # 3 more panels: still not re-fired
            for i in range(10, 12):
                log_panel(i)
            ws = redundant()
            self.assertEqual(len(ws), 2)                      # 5 panels after the dismissal: one fresh suggestion
            self.assertEqual([w["status"] for w in ws], ["dismissed", "open"])

    def test_check_does_not_recreate_watches_written_under_older_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            import shutil
            log = os.path.join(tmp, "m.jsonl")
            shutil.copy(str(HERE / "fixtures" / "modern.jsonl"), log)  # its skim watch has a non-formula id AND a truncated target
            r = run(["check"], env={"BALANCEWHEEL_METRICS_LOG": log})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("0 new watch(es)", r.stdout)
            self.assertEqual(len(store.load(log)["watches"]), 1)

    def test_forced_unknown_seat_is_annotated(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"BALANCEWHEEL_METRICS_LOG": os.path.join(tmp, "m.jsonl")}
            r = run(["panel", "--force"], stdin=json.dumps(record(seats=["alpha", "beta", "zeta"])), env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("forced: seats not in config: ['zeta']", store.load(env["BALANCEWHEEL_METRICS_LOG"])["panels"][0]["notes"])

    def test_findings_for_a_seat_not_in_seats_is_a_mismatch(self):
        import config as cfg
        bad = record(); bad["findings"]["omega"] = {"total": 1}
        with self.assertRaises(ValueError) as e:
            logmod.validate_panel(bad, cfg.load_config(CONFIG), force=False)
        self.assertIn("findings.omega", str(e.exception))

    def test_amend_with_inconsistent_counts_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"BALANCEWHEEL_METRICS_LOG": os.path.join(tmp, "m.jsonl")}
            run(["panel"], stdin=json.dumps(record()), env=env)
            pid = store.load(env["BALANCEWHEEL_METRICS_LOG"])["panels"][0]["id"]
            patch = {"findings_detail": [detail("alpha", "g9")], "findings": {"alpha": {"total": 5}}}
            r = run(["amend", "--panel-id", pid], stdin=json.dumps(patch), env=env)
            self.assertEqual(r.returncode, 2); self.assertIn("alpha.total", r.stderr)
            r = run(["amend", "--panel-id", pid], stdin=json.dumps([]), env=env)
            self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_log -v`
Expected: ERROR `No module named 'log'`.

- [ ] **Step 3: Write `metrics/log.py`**

```python
#!/usr/bin/env python3
"""log.py — the metrics logger CLI. Every write to the log goes through here or store.append.

  log.py config [--json]              resolved config (JSON)
  log.py usage --seat S --mode M --dir D --duration N --ok B [--model M] [--reason R]
  log.py panel [--force] < record.json
  log.py check
  log.py watches
  log.py resolve <id> [--status resolved|dismissed|open] [--note …]
  log.py amend --panel-id <id> < patch.json
"""
import argparse
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as cfgmod  # noqa: E402
import store  # noqa: E402
import watches as watchmod  # noqa: E402

SEVERITIES = ("critical", "major", "minor", "nit")
VERDICTS = ("confirmed", "refuted", "partial", "dropped")
DETAIL_REQUIRED = ("seat", "group", "title", "severity", "verdict")
DISPUTE_REQUIRED = ("summary", "challenger", "proposals", "winner", "reason")
REJECT = (ValueError, TypeError, AttributeError, KeyError)  # anything malformed input can raise inside validation


def compute_counts(findings_detail):
    groups = {}
    for f in findings_detail:
        groups.setdefault(f["group"], set()).add(f["seat"])
    out = {}
    for f in findings_detail:
        c = out.setdefault(f["seat"], {"total": 0, "confirmed": 0, "refuted": 0, "partial": 0, "unique": 0})
        v = f["verdict"]
        if v == "dropped":
            continue
        c["total"] += 1
        c[v] += 1
        if v == "confirmed" and len(groups[f["group"]]) == 1:
            c["unique"] += 1
    return out


def _fail(msg):
    raise ValueError(msg)


def die(msg):
    """Rejections exit 2 (bad input); unexpected crashes keep Python's exit 1."""
    print(msg, file=sys.stderr)
    sys.exit(2)


def validate_detail(fd, seats):
    """Shared by panel and amend: shape-check every findings_detail entry."""
    if not isinstance(fd, list):
        _fail("findings_detail must be a list")
    for i, f in enumerate(fd):
        if not isinstance(f, dict):
            _fail(f"findings_detail[{i}] must be an object")
        for k in DETAIL_REQUIRED:
            if k not in f:
                _fail(f"findings_detail[{i}] missing {k}")
        if f["severity"] not in SEVERITIES:
            _fail(f"findings_detail[{i}].severity must be one of {SEVERITIES}")
        if f["verdict"] not in VERDICTS:
            _fail(f"findings_detail[{i}].verdict must be one of {VERDICTS}")
        if f["seat"] not in seats:
            _fail(f"findings_detail[{i}].seat {f['seat']!r} is not in seats")


def _is_count(v):
    return isinstance(v, int) and not isinstance(v, bool)


def check_counts(given, computed, seats, force):
    """Compare hand-filled per-seat counts (incl. unique) with counts computed from findings_detail.
    Returns (findings, note): findings are the reconciled counts; note is '' or the forced mismatch text."""
    if not isinstance(given, dict):
        _fail("findings must be an object keyed by seat")
    zero = {"total": 0, "confirmed": 0, "refuted": 0, "partial": 0, "unique": 0}
    mismatches = [f"findings.{s}: seat is not in seats" for s in given if s not in seats]
    findings = {}
    for s in seats:
        g = given.get(s, {})
        if not isinstance(g, dict):
            _fail(f"findings.{s} must be an object")
        c = computed.get(s, dict(zero))
        out = {}
        for k in zero:
            if k in g and not _is_count(g[k]):
                _fail(f"findings.{s}.{k} must be an integer")
            if k in g and g[k] != c[k]:
                mismatches.append(f"{s}.{k}: record says {g[k]}, findings_detail says {c[k]}")
            out[k] = c[k]  # computed is authoritative; a disagreement is either rejected or noted
        findings[s] = out
    if mismatches and not force:
        _fail("findings counts disagree with findings_detail: " + "; ".join(mismatches))
    return findings, ("count_mismatch: " + "; ".join(mismatches)) if mismatches else ""


def validate_panel(rec, config, force):
    if not isinstance(rec, dict):
        _fail("panel record must be a JSON object")
    for k in ("target", "kind", "seats", "immediate_agreement", "findings", "findings_detail"):
        if k not in rec or rec[k] is None:
            _fail(f"panel record missing required field: {k}")
    if not isinstance(rec["target"], str) or not rec["target"].strip():
        _fail("target must be a non-empty string")
    if rec["kind"] not in cfgmod.ROLES:
        _fail(f"kind must be one of {sorted(cfgmod.ROLES)}, got {rec['kind']!r}")
    if not isinstance(rec["immediate_agreement"], bool):
        _fail("immediate_agreement must be true or false")
    if rec.get("rounds") is not None and not _is_count(rec["rounds"]):
        _fail("rounds must be an integer")
    seats_in = rec["seats"]
    if not isinstance(seats_in, (list, dict)) or not seats_in:
        _fail("seats must be a non-empty list or object")
    names = list(seats_in)
    if not all(isinstance(s, str) for s in names):
        _fail("seat names must be strings")
    unknown = [s for s in names if s not in config["seats"]]
    if unknown and not force:
        _fail(f"seats not in config: {unknown} (use --force to log an on-demand seat)")
    force_note = f"forced: seats not in config: {unknown}" if unknown else ""
    seats = {}
    for s in names:
        given = seats_in[s] if isinstance(seats_in, dict) else None
        if given is not None and not isinstance(given, str):
            _fail(f"seats.{s}: model must be a string or null")
        seats[s] = given or (config["seats"][s]["model"] if s in config["seats"] else None)
    fd = rec["findings_detail"]
    validate_detail(fd, seats)
    disputes = rec.get("disputes") or []
    if not isinstance(disputes, list):
        _fail("disputes must be a list")
    for i, d in enumerate(disputes):
        if not isinstance(d, dict):
            _fail(f"disputes[{i}] must be an object")
        for k in DISPUTE_REQUIRED:
            if k not in d:
                _fail(f"disputes[{i}] missing {k}")
        if not isinstance(d["proposals"], dict):
            _fail(f"disputes[{i}].proposals must be an object keyed by seat")
    findings, mismatch_note = check_counts(rec["findings"], compute_counts(fd), seats, force)
    notes = rec.get("notes", "") or ""
    if not isinstance(notes, str):
        _fail("notes must be a string")
    for extra in (force_note, mismatch_note):
        if extra:
            notes = (notes + " | " if notes else "") + extra
    out = dict(rec)
    out.update({"type": "panel", "id": uuid.uuid4().hex[:8], "ts": store.now_ts(), "seats": seats,
                "findings": findings, "findings_detail": fd, "disputes": disputes,
                "rounds": rec.get("rounds", 1), "notes": notes})
    return out


def _print_watches(new):
    for w in new:
        print(f"WATCH[{w['kind']}|{w['status']}] {w['detail']} (id {w['id']})")


def run_check(path, config, only_panel=None):
    """Append watches the log does not have yet. A watch that already exists but is open and is now
    computed as resolved (e.g. an amend recorded the triage) gets a watch-update instead of a duplicate."""
    new, flipped = [], []
    with store.locked(path):  # read-then-append must be atomic across concurrent `check`/`panel` runs
        data = store.load(path)
        existing = {w["id"]: w["status"] for w in data["watches"]}
        # Watches written by older loggers carry ids from a different formula (or none) and may hold a
        # truncated target. A panel-scoped watch is the same watch if the kind matches and one target
        # is a prefix of the other, so those never get re-created.
        legacy = [(w["kind"], w["target"]) for w in data["watches"] if w["kind"] != "redundant-seat"]

        def seen_before(w):
            return any(k == w["kind"] and (t.startswith(w["target"]) or w["target"].startswith(t))
                       for k, t in legacy if t)

        candidates = watchmod.evaluate(data["panels"], config)
        if only_panel:
            candidates = [w for w in candidates if w.get("panel_id") == only_panel or w["kind"] == "redundant-seat"]
        for w in candidates:
            if w["id"] not in existing:
                if w["kind"] != "redundant-seat" and seen_before(w):
                    continue
                if not watchmod.should_refire(w, data, config):
                    continue
                store.append(path, w)
                new.append(w)
                existing[w["id"]] = w["status"]
            elif existing[w["id"]] == "open" and w["status"] == "resolved":
                store.append(path, {"type": "watch-update", "watch_id": w["id"], "status": "resolved",
                                    "note": "re-evaluated: " + w["detail"]})
                flipped.append(w)
                existing[w["id"]] = "resolved"
    _print_watches(new)
    for w in flipped:
        print(f"WATCH[{w['kind']}|resolved] (was open) {w['detail']} (id {w['id']})")
    open_count = sum(1 for s in existing.values() if s == "open")
    return new, open_count


def cmd_config(args, config):
    print(json.dumps(config, indent=None if args.json else 2))


def cmd_usage(args, config):
    store.append(store.log_path(config), {
        "type": "usage", "seat": args.seat, "model": args.model, "mode": args.mode, "dir": args.dir,
        "duration_s": args.duration, "ok": args.ok.lower() == "true", "reason": args.reason,
    })


def cmd_panel(args, config):
    try:
        rec = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        die(f"panel record is not valid JSON: {e}")
    try:
        rec = validate_panel(rec, config, args.force)
    except REJECT as e:
        die(f"panel record rejected: {e}")
    path = store.log_path(config)
    store.append(path, rec)
    print(f"panel {rec['id']} logged")
    _, open_count = run_check(path, config, only_panel=rec["id"])
    if open_count:
        print(f"{open_count} open watch suggestion(s) — `log.py watches` to list, `log.py resolve <id>` to close")


def cmd_check(args, config):
    new, open_count = run_check(store.log_path(config), config)
    print(f"{len(new)} new watch(es); {open_count} open suggestion(s)")


def cmd_watches(args, config):
    for w in store.load(store.log_path(config))["watches"]:
        note = w["updates"][-1]["note"] if w["updates"] else ""
        print(f"{w['id']}  {(w['ts'] or '')[:10]}  {w['kind']:<18} {w['status']:<9} {w['target'][:48]}{('  — ' + note) if note else ''}")


def cmd_resolve(args, config):
    path = store.log_path(config)
    known = {w["id"] for w in store.load(path)["watches"]}
    if args.id not in known:
        die(f"unknown watch id {args.id}")
    store.append(path, {"type": "watch-update", "watch_id": args.id, "status": args.status, "note": args.note})
    print(f"watch {args.id} → {args.status}")


def cmd_amend(args, config):
    path = store.log_path(config)
    panels = {p["id"]: p for p in store.load(path)["panels"]}
    if args.panel_id not in panels:
        die(f"unknown panel id {args.panel_id}")
    try:
        patch = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        die(f"patch is not valid JSON: {e}")
    if not isinstance(patch, dict):
        die("patch must be a JSON object")
    allowed = set(store.PANEL_AMENDABLE) | {"note"}
    bad = set(patch) - allowed
    if bad:
        die(f"amend may only set {sorted(allowed)}; got {sorted(bad)}")
    seats = list(panels[args.panel_id]["seats"])
    try:
        if "findings_detail" in patch:
            validate_detail(patch["findings_detail"], seats)
            computed = compute_counts(patch["findings_detail"])
            given = patch.get("findings", {})
            patch["findings"], note = check_counts(given, computed, seats, args.force)
            if note:
                patch["notes"] = ((patch.get("notes") or panels[args.panel_id]["notes"] or "") + " | " + note).strip(" |")
        elif "findings" in patch and panels[args.panel_id]["findings_detail"]:
            computed = compute_counts(panels[args.panel_id]["findings_detail"])
            patch["findings"], note = check_counts(patch["findings"], computed, seats, args.force)
    except REJECT as e:
        die(f"amend rejected: {e}")
    store.append(path, dict(patch, type="panel-amend", panel_id=args.panel_id))
    print(f"panel {args.panel_id} amended")
    run_check(path, config, only_panel=args.panel_id)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="log.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("config"); c.add_argument("--json", action="store_true")
    u = sub.add_parser("usage")
    u.add_argument("--seat", required=True); u.add_argument("--mode", required=True, choices=["new", "resume"])
    u.add_argument("--dir", required=True); u.add_argument("--duration", type=int, required=True)
    u.add_argument("--ok", required=True, choices=["true", "false"])
    u.add_argument("--model", default=None); u.add_argument("--reason", default=None)
    p = sub.add_parser("panel"); p.add_argument("--force", action="store_true")
    sub.add_parser("check"); sub.add_parser("watches")
    r = sub.add_parser("resolve"); r.add_argument("id")
    r.add_argument("--status", default="resolved", choices=list(store.WATCH_STATUSES)); r.add_argument("--note", default="")
    a = sub.add_parser("amend"); a.add_argument("--panel-id", required=True); a.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    try:
        config = cfgmod.load_config()
    except cfgmod.ConfigError as e:
        die(f"config error: {e}")
    {"config": cmd_config, "usage": cmd_usage, "panel": cmd_panel, "check": cmd_check,
     "watches": cmd_watches, "resolve": cmd_resolve, "amend": cmd_amend}[args.cmd](args, config)


if __name__ == "__main__":
    main()
```

Every rejection path goes through `die()` (exit **2**); a bare `sys.exit("msg")` would exit 1 and fail the CLI tests.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_log -v`
Expected: all 17 tests PASS.

- [ ] **Step 5: Run the whole suite**

Run: `python3 -m unittest discover -s tests -v`
Expected: every test in `test_config`, `test_store`, `test_watches`, `test_log` passes.

- [ ] **Step 6: Commit**

```bash
git add metrics/log.py tests/test_log.py
git commit -m "feat: logger cli — usage, panel with computed unique, check, watches, resolve, amend"
```

---

### Task 5: Dashboard

**Files:**
- Create: `metrics/dashboard.py`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `store.load()` data, `config`, and `seats_state` (the dict in `state_dir/seats.json` that Plan 2's setup writes: `{"<seat>": {"verified": bool, "runtime_version": str, "verified_at": str, "failed_probe": str|None}}`; may be `None` when setup has not run).
- Produces: `render(data, config, seats_state) -> str` and `main()` (writes `state_dir/dashboard.html`, prints the path). Sections in this order, each in a `<section id="…">`: `now`, `roster`, `timeline`, `disputes`, `findings`, `watches`.

- [ ] **Step 1: Write the failing tests**

`tests/test_dashboard.py`:

```python
import json, os, sys, tempfile, unittest
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "metrics"))
import config as cfg  # noqa: E402
import dashboard  # noqa: E402
import store  # noqa: E402

FX = HERE / "fixtures"
CONFIG = cfg.load_config(str(FX / "config.json"))
SECTIONS = ("now", "roster", "timeline", "disputes", "findings", "watches")


def svgs(html):
    out = []
    i = 0
    while True:
        s = html.find("<svg", i)
        if s < 0:
            return out
        close = html.find("</svg>", s)
        if close < 0:
            raise AssertionError("unterminated <svg> element")
        e = close + len("</svg>")
        out.append(html[s:e]); i = e


class Render(unittest.TestCase):
    def test_all_sections_present_and_svg_is_well_formed(self):
        html = dashboard.render(store.load(str(FX / "modern.jsonl")), CONFIG, None)
        for s in SECTIONS:
            self.assertIn(f'<section id="{s}"', html)
        self.assertGreaterEqual(len(svgs(html)), 2)
        for svg in svgs(html):
            ET.fromstring(svg)  # raises on malformed markup

    def test_empty_log_and_one_panel_log_render(self):
        html = dashboard.render(store.load("/nonexistent.jsonl"), CONFIG, None)
        for s in SECTIONS:
            self.assertIn(f'<section id="{s}"', html)
        self.assertIn("no panels logged yet", html)
        d = store.load(str(FX / "modern.jsonl")); d["panels"] = d["panels"][:1]
        html = dashboard.render(d, CONFIG, None)
        for svg in svgs(html):
            ET.fromstring(svg)

    def test_model_supplied_text_is_escaped_and_control_chars_dropped(self):
        d = store.load(str(FX / "modern.jsonl"))
        d["panels"][0]["findings_detail"][0]["claim"] = "<script>alert(1)</script>"
        d["panels"][0]["target"] = "x <b>bold</b>\ud800"
        html = dashboard.render(d, CONFIG, None)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<b>bold</b>", html)
        self.assertNotIn("", html); self.assertNotIn("\ud800", html)
        for svg in svgs(html):
            ET.fromstring(svg)
        html.encode("utf-8")  # no lone surrogates survive

    def test_model_change_guide_stacked_counts_and_amendment_badge(self):
        d = store.load(str(FX / "modern.jsonl"))
        html = dashboard.render(d, CONFIG, None)
        self.assertIn("model change: beta", html)    # beta's model changed between p1 and p2
        self.assertIn('class="stack confirmed"', html)
        self.assertIn('class="stack refuted"', html)
        self.assertIn("amended 1×", html)            # p1 carries one panel-amend

    def test_timeline_uses_real_dates_with_irregular_spacing(self):
        xs = dashboard.timeline_positions(["2026-01-02T00:00:00Z", "2026-01-05T00:00:00Z", "2026-01-12T00:00:00Z"], width=300)
        self.assertEqual(xs[0], 0.0); self.assertEqual(xs[-1], 300.0)
        self.assertAlmostEqual(xs[1], 90.0, places=3)  # 3 of 10 days
        self.assertEqual(dashboard.timeline_positions(["2026-01-02T00:00:00Z"], width=300), [0.0])
        # unparseable timestamps are pinned to the left edge, not 'now' (deterministic renders)
        self.assertEqual(dashboard.timeline_positions([None, "2026-01-02T00:00:00Z", "2026-01-12T00:00:00Z"], width=300)[0], 0.0)
        self.assertEqual(dashboard.timeline_positions(["garbage"], width=300), [0.0])

    def test_circle_radius_is_sqrt_of_count(self):
        self.assertAlmostEqual(dashboard.radius_for(16) / dashboard.radius_for(4), 2.0)

    def test_right_now_reports_open_watches_freshness_and_verification(self):
        d = store.load(str(FX / "modern.jsonl"))
        state = {"alpha": {"verified": True, "runtime_version": "1.0"}, "beta": {"verified": False, "failed_probe": "redirect"}}
        html = dashboard.render(d, CONFIG, state)
        self.assertIn("1 verified", html); self.assertIn("1 unverified", html)
        self.assertIn("0 open", html)
        self.assertIn("log freshness", html)

    def test_roster_meter_states_and_usage_share(self):
        d = store.load(str(FX / "modern.jsonl"))
        html = dashboard.render(d, CONFIG, None)
        self.assertIn("no unique recorded", html)   # a seat with no unique recorded in window
        self.assertIn("usage share", html)
        self.assertIn("(67%)", html)                 # beta: 2 of 3 usage events

    def test_timeline_height_grows_with_roster(self):
        d = store.load(str(FX / "modern.jsonl"))
        big = dict(CONFIG, seats={f"s{i}": {"runtime": "pi", "model": "m", "roles": []} for i in range(12)})
        html = dashboard.render(d, big, None)
        self.assertIn('height="220"', html)          # 136 + 6 * (12 configured + alpha and beta from the log)

    def test_main_out_without_directory_component(self):
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            os.environ["BALANCEWHEEL_METRICS_LOG"] = str(FX / "modern.jsonl")
            os.environ["BALANCEWHEEL_CONFIG"] = str(FX / "config.json")
            try:
                self.assertTrue(Path(dashboard.main(["--out", "d.html"])).exists())
            finally:
                del os.environ["BALANCEWHEEL_METRICS_LOG"]; del os.environ["BALANCEWHEEL_CONFIG"]; os.chdir(cwd)


class Main(unittest.TestCase):
    def test_main_writes_file_into_state_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["BALANCEWHEEL_METRICS_LOG"] = str(FX / "modern.jsonl")
            os.environ["BALANCEWHEEL_CONFIG"] = str(FX / "config.json")
            try:
                out = dashboard.main(["--state-dir", tmp])
            finally:
                del os.environ["BALANCEWHEEL_METRICS_LOG"]; del os.environ["BALANCEWHEEL_CONFIG"]
            self.assertTrue(Path(out).exists())
            self.assertIn("<title>", Path(out).read_text())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_dashboard -v`
Expected: ERROR `No module named 'dashboard'`.

- [ ] **Step 3: Write `metrics/dashboard.py`**

```python
#!/usr/bin/env python3
"""dashboard.py — one self-contained HTML file (inline SVG, no JS) from the metrics log.

Reads at seven panels as well as seventy: points and step marks, counts not rates at small n,
<title> tooltips on every mark, real date axis, model-change guides from recorded models.
Every string from the log passes through html.escape.
"""
import argparse
import html
import json
import math
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as cfgmod  # noqa: E402
import store  # noqa: E402

PALETTE = ["#2563eb", "#d97706", "#059669", "#dc2626", "#7c3aed", "#0891b2", "#db2777", "#65a30d"]
VERDICT = {"confirmed": "#059669", "partial": "#d97706", "refuted": "#dc2626", "dropped": "#9ca3af"}
CSS = """
:root{--bg:#ffffff;--fg:#111827;--muted:#6b7280;--line:#e5e7eb;--card:#f9fafb;--amber:#d97706;--red:#dc2626;--green:#059669}
@media (prefers-color-scheme: dark){:root{--bg:#0f172a;--fg:#e5e7eb;--muted:#9ca3af;--line:#334155;--card:#1e293b}}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;padding:24px;max-width:1100px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:28px 0 8px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px}
.tile .v{font-size:22px;font-weight:600}.tile .l{color:var(--muted);font-size:12px}
.tile.amber .v{color:var(--amber)}.tile.red .v{color:var(--red)}
table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:500;font-size:12px}
.pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;color:#fff}
.meter{display:inline-block;height:10px;border-radius:5px;background:var(--line);width:120px;position:relative;vertical-align:middle}
.meter>span{position:absolute;left:0;top:0;bottom:0;border-radius:5px}
.meter.green>span{background:var(--green)}.meter.amber>span{background:var(--amber)}.meter.red>span{background:var(--red)}.meter.grey>span{background:var(--muted)}
.muted{color:var(--muted)}details{margin:6px 0}summary{cursor:pointer}svg text{fill:var(--fg);font-size:11px}
.legend span{display:inline-block;margin-right:12px}.swatch{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:middle}
"""


CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]")


def esc(s):
    """HTML-escape and drop XML-illegal control characters / lone surrogates (valid JSON, invalid SVG)."""
    return html.escape(CONTROL.sub("", "" if s is None else str(s)), quote=True)


def parse_ts(ts):
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def timeline_positions(timestamps, width):
    """x positions on a real date axis; irregular spacing preserved. Single point → [0.0].
    Unparseable timestamps sit at the left edge (deterministic), never at 'now'."""
    parsed = [parse_ts(t) for t in timestamps]
    good = [d for d in parsed if d is not None]
    if not parsed:
        return []
    if not good:
        return [0.0 for _ in parsed]
    lo, hi = min(good), max(good)
    span = (hi - lo).total_seconds()
    return [0.0 if d is None or span == 0 else round((d - lo).total_seconds() / span * width, 6) for d in parsed]


def radius_for(count):
    """Area encodes the count: radius ∝ √count, with a floor so a zero-finding panel is still visible."""
    return max(2.0, 2.5 * math.sqrt(max(count, 0)))


def seat_colors(config, data):
    names = list(config["seats"]) + sorted({s for p in data["panels"] for s in p["seats"] if s not in config["seats"]})
    return {n: PALETTE[i % len(PALETTE)] for i, n in enumerate(names)}


def age_days(ts):
    d = parse_ts(ts)
    return None if d is None else (datetime.now(timezone.utc) - d).total_seconds() / 86400


# ---------- sections ----------

def section_now(data, config, seats_state):
    open_w = [w for w in data["watches"] if w["status"] == "open"]
    oldest = min(open_w, key=lambda w: w["ts"] or "") if open_w else None
    last = data["panels"][-1] if data["panels"] else None
    newest = max((e.get("ts") or "" for e in data["events"]), default="")
    fresh = age_days(newest) if newest else None
    tiles = []
    tiles.append(("amber" if open_w else "", f"{len(open_w)} open",
                  f"watch suggestions" + (f" · oldest: {esc(oldest['kind'])}, {age_days(oldest['ts']) or 0:.0f}d" if oldest else "")))
    tiles.append(("", esc(last["target"][:40]) if last else "none", "last panel" + (f" · {esc(', '.join(last['seats']))} · {esc((last['ts'] or '')[:10])}" if last else " — no panels logged yet")))
    if seats_state:
        v = sum(1 for s in seats_state.values() if s.get("verified"))
        u = len(seats_state) - v
        tiles.append(("red" if u else "", f"{v} verified", f"seats · {u} unverified" + (" — run setup" if u else "")))
    else:
        tiles.append(("amber", "not set up", "seats · setup has not written seats.json"))
    cls = "amber" if (fresh or 0) > 7 else ""
    tiles.append((cls, f"{fresh:.0f}d" if fresh is not None else "—", "log freshness (age of newest event)"))
    body = "".join(f'<div class="tile {c}"><div class="v">{v}</div><div class="l">{l}</div></div>' for c, v, l in tiles)
    warn = "".join(f'<p class="muted">⚠ {esc(w)}</p>' for w in data["warnings"])
    return f'<section id="now"><h2>Right now</h2><div class="tiles">{body}</div>{warn}</section>'


def stacked_bars(panels, seat):
    """Per-panel stacked confirmed/partial/refuted counts for one seat (x = panel index in date order)."""
    w, h, bw = 12, 40, 8
    width = max(len(panels) * w, w)
    ymax = max((sum(v for k, v in p["findings"][seat].items() if k in ("confirmed", "partial", "refuted") and isinstance(v, int))
                for p in panels if seat in p["findings"]), default=0) or 1
    parts = [f'<svg width="{width}" height="{h}" viewBox="0 0 {width} {h}" role="img" aria-label="per-panel counts for {esc(seat)}">']
    for i, p in enumerate(panels):
        f = p["findings"].get(seat)
        if not f:
            continue
        y = h
        for k in ("confirmed", "partial", "refuted"):
            v = f.get(k) if isinstance(f.get(k), int) else 0
            if not v:
                continue
            bh = v / ymax * (h - 2)
            y -= bh
            parts.append(f'<rect class="stack {k}" x="{i * w + 2}" y="{y:.2f}" width="{bw}" height="{bh:.2f}" fill="{VERDICT[k]}"><title>{esc(p["target"])} · {k}: {v}</title></rect>')
    parts.append("</svg>")
    return "".join(parts)


def section_roster(data, config, colors):
    panels = sorted(data["panels"], key=lambda p: p["ts"] or "")
    w = config["watches"]
    window = panels[-w["redundant_window_panels"]:] if w["redundant_window_panels"] else panels
    rows = []
    for seat in colors:
        mine = [p for p in panels if seat in p["findings"]]
        conf = sum(p["findings"][seat]["confirmed"] or 0 for p in mine)
        ref = sum(p["findings"][seat]["refuted"] or 0 for p in mine)
        part = sum(p["findings"][seat]["partial"] or 0 for p in mine)
        n = conf + ref + part
        rate = f"{conf / n * 100:.0f}% <span class=\"muted\">(n={n})</span>" if n else '<span class="muted">n=0</span>'
        recent = [p for p in window if seat in p["findings"] and isinstance(p["findings"][seat].get("unique"), int)][-w["redundant_min_panels"]:]
        uniq = sum(p["findings"][seat]["unique"] for p in recent)
        if not recent:
            meter = '<span class="meter grey"><span style="width:0"></span></span> <span class="muted">no unique recorded</span>'
        else:
            cls = "red" if uniq == 0 else ("amber" if uniq == 1 else "green")
            pct = min(100, uniq / max(len(recent), 1) * 100)
            meter = f'<span class="meter {cls}"><span style="width:{pct:.0f}%"></span></span> {uniq} unique / {len(recent)} panels'
        challenges = sum(1 for p in panels for d in p["disputes"] if d.get("challenger") == seat)
        wins = sum(1 for p in panels for d in p["disputes"] if d.get("winner") == seat)
        involved = sum(1 for p in panels for d in p["disputes"] if seat in (d.get("proposals") or {}) or d.get("challenger") == seat)
        share = f"{wins}/{involved}" if involved else "—"
        usage = sum(1 for u in data["usage"] if u.get("seat") == seat)
        total_usage = len(data["usage"]) or 1
        usage_pct = usage / total_usage * 100
        usage_bar = (f'<span class="meter grey"><span style="width:{usage_pct:.0f}%"></span></span> '
                     f'{usage} <span class="muted">({usage_pct:.0f}%)</span>')
        rows.append(f'<tr><td><span class="swatch" style="background:{colors[seat]}"></span>{esc(seat)}</td><td>{meter}</td>'
                    f'<td>{rate}</td><td>{challenges}</td><td>{share}</td><td>{usage_bar}</td><td>{stacked_bars(panels, seat)}</td></tr>')
    legend = '<p class="legend"><span><i class="swatch" style="background:#059669"></i>confirmed</span><span><i class="swatch" style="background:#d97706"></i>partial</span><span><i class="swatch" style="background:#dc2626"></i>refuted</span></p>'
    return ('<section id="roster"><h2>Roster health</h2><table><tr><th>seat</th><th>unique catches (redundancy meter)</th><th>confirmed rate</th>'
            '<th>challenges</th><th>dispute wins</th><th>usage share</th><th>per-panel counts</th></tr>' + "".join(rows) + f'</table>{legend}</section>')


def section_timeline(data, config, colors):
    panels = sorted(data["panels"], key=lambda p: p["ts"] or "")
    if not panels:
        return '<section id="timeline"><h2>Panel timeline</h2><p class="muted">no panels logged yet</p></section>'
    W, pad = 900, 40
    H = 136 + 6 * len(colors)  # presence dot rows grow with the roster
    xs = timeline_positions([p["ts"] for p in panels], W - 2 * pad)
    parts = [f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="panel timeline">',
             f'<line x1="{pad}" y1="70" x2="{W - pad}" y2="70" stroke="#9ca3af"/>']
    prev_models = {}
    for p, x in zip(panels, xs):
        cx = pad + x
        raw = p["findings_raw"] if isinstance(p["findings_raw"], int) else sum(c["total"] or 0 for c in p["findings"].values())
        r = radius_for(raw)
        fill = "#2563eb" if p["immediate_agreement"] else "none"
        ring = ' stroke="#dc2626" stroke-width="2"' if p["disputes"] else ' stroke="#2563eb" stroke-width="1.5"'
        parts.append(f'<circle cx="{cx:.2f}" cy="70" r="{r:.2f}" fill="{fill}" fill-opacity="0.35"{ring}><title>{esc(p["target"])} · {esc((p["ts"] or "")[:10])} · {raw} findings · {len(p["disputes"])} disputes</title></circle>')
        for i, d in enumerate(p["disputes"]):
            c = colors.get(d.get("winner"), "#9ca3af")
            parts.append(f'<rect x="{cx - 2 + i * 5:.2f}" y="{78 + r:.2f}" width="3" height="8" fill="{c}"><title>dispute: {esc(d.get("summary"))} · winner {esc(d.get("winner"))}</title></rect>')
        for j, seat in enumerate(colors):
            if seat in p["seats"]:
                parts.append(f'<circle cx="{cx:.2f}" cy="{118 + j * 6}" r="2" fill="{colors[seat]}"><title>{esc(seat)} sat on {esc(p["target"])}</title></circle>')
        for seat, model in p["seats"].items():
            if seat in prev_models and model and prev_models[seat] and prev_models[seat] != model:
                parts.append(f'<line x1="{cx:.2f}" y1="20" x2="{cx:.2f}" y2="110" stroke="{colors.get(seat, "#9ca3af")}" stroke-dasharray="3 3"><title>model change: {esc(seat)} {esc(prev_models[seat])} → {esc(model)}</title></line>')
                parts.append(f'<text x="{cx + 3:.2f}" y="28">model change: {esc(seat)}</text>')
            if model:
                prev_models[seat] = model
    by_panel = {}
    for w in data["watches"]:
        if w.get("panel_id"):
            by_panel.setdefault(w["panel_id"], []).append(w)
    for p, x in zip(panels, xs):
        for k, w in enumerate(by_panel.get(p["id"], [])):
            parts.append(f'<line x1="{pad + x:.2f}" y1="40" x2="{pad + x:.2f}" y2="60" stroke="#d97706"><title>watch: {esc(w["kind"])} ({esc(w["status"])})</title></line>')
    parts.append(f'<text x="{pad}" y="{H - 4}">{esc((panels[0]["ts"] or "")[:10])}</text><text x="{W - pad - 70}" y="{H - 4}">{esc((panels[-1]["ts"] or "")[:10])}</text></svg>')
    return ('<section id="timeline"><h2>Panel timeline</h2>' + "".join(parts) +
            '<p class="muted">circle area = raw findings · filled = immediate agreement · red ring = disputes · ticks below = disputes in winner colour · dots = seats present · dashed line = model change · amber tick above = watch</p></section>')


def section_disputes(data, colors):
    rows = []
    for p in sorted(data["panels"], key=lambda p: p["ts"] or "", reverse=True):
        for d in p["disputes"]:
            props = "<br>".join(f"<b>{esc(s)}</b>: {esc(t)}" for s, t in (d.get("proposals") or {}).items())
            rows.append(f'<tr><td>{esc((p["ts"] or "")[:10])}</td><td>{esc(p["target"][:40])}</td><td>{esc(d.get("summary"))}</td><td>{esc(d.get("challenger"))}</td><td>{props}</td><td><b>{esc(d.get("winner"))}</b></td><td>{esc(d.get("reason"))}</td></tr>')
    body = "".join(rows) or '<tr><td colspan="7" class="muted">no disputes yet</td></tr>'
    return f'<section id="disputes"><h2>Dispute record</h2><table><tr><th>date</th><th>panel</th><th>issue</th><th>challenger</th><th>proposals</th><th>winner</th><th>reason</th></tr>{body}</table></section>'


def section_findings(data, colors):
    blocks = []
    for p in sorted(data["panels"], key=lambda p: p["ts"] or "", reverse=True):
        fd = p["findings_detail"]
        head = f'{esc((p["ts"] or "")[:10])} · {esc(p["target"])} · {esc(p["kind"])} · {esc(", ".join(p["seats"]))}'
        if not fd:
            blocks.append(f'<details><summary>{head} <span class="muted">(no findings_detail recorded)</span></summary></details>')
            continue
        rows = "".join(
            f'<tr><td><span class="swatch" style="background:{colors.get(f.get("seat"), "#9ca3af")}"></span>{esc(f.get("seat"))}</td>'
            f'<td>{esc(f.get("severity"))}</td><td>{esc(f.get("title"))}<br><span class="muted">{esc(f.get("claim"))}</span></td>'
            f'<td>{esc(f.get("ref"))}</td><td><span class="pill" style="background:{VERDICT.get(f.get("verdict"), "#9ca3af")}">{esc(f.get("verdict"))}</span></td>'
            f'<td>{esc(f.get("evidence"))}</td><td>{esc(f.get("action"))}</td></tr>' for f in fd)
        amend = f' <span class="muted">· amended {len(p["amendments"])}×</span>' if p["amendments"] else ""
        blocks.append(f'<details><summary>{head} · {len(fd)} findings{amend}</summary><table><tr><th>seat</th><th>sev</th><th>finding</th><th>ref</th><th>verdict</th><th>evidence</th><th>action</th></tr>{rows}</table></details>')
    body = "".join(blocks) or '<p class="muted">no panels logged yet</p>'
    return f'<section id="findings"><h2>Panel findings</h2>{body}</section>'


def section_watches(data):
    rows = []
    for w in sorted(data["watches"], key=lambda w: (w["status"] != "open", w["ts"] or ""), reverse=False):
        color = {"open": "#d97706", "resolved": "#059669", "dismissed": "#6b7280"}[w["status"]]
        note = w["updates"][-1]["note"] if w["updates"] else ""
        rows.append(f'<tr><td><code>{esc(w["id"])}</code></td><td>{esc((w["ts"] or "")[:10])}</td><td>{esc(w["kind"])}</td><td><span class="pill" style="background:{color}">{esc(w["status"])}</span></td><td>{esc(w["target"][:48])}</td><td>{esc(w["detail"])}{("<br><i>" + esc(note) + "</i>") if note else ""}</td></tr>')
    body = "".join(rows) or '<tr><td colspan="6" class="muted">no watches yet</td></tr>'
    return f'<section id="watches"><h2>Roster watches</h2><table><tr><th>id</th><th>date</th><th>kind</th><th>status</th><th>target</th><th>detail</th></tr>{body}</table></section>'


def render(data, config, seats_state):
    colors = seat_colors(config, data)
    parts = [f"<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width\"><title>Balancewheel dashboard</title><style>{CSS}</style></head><body>",
             f'<h1>Balancewheel — seat metrics</h1><p class="muted">generated {esc(store.now_ts())} · {len(data["panels"])} panels · {len(data["usage"])} seat calls · log {esc(store.log_path(config))}</p>',
             section_now(data, config, seats_state), section_roster(data, config, colors), section_timeline(data, config, colors),
             section_disputes(data, colors), section_findings(data, colors), section_watches(data), "</body></html>"]
    return "".join(parts)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dashboard.py")
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    try:
        config = cfgmod.load_config()
    except cfgmod.ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        sys.exit(2)
    state_dir = args.state_dir or config["state_dir"]
    seats_state = None
    sp = os.path.join(state_dir, "seats.json")
    if os.path.exists(sp):
        with open(sp) as f:
            seats_state = json.load(f)
    data = store.load(store.log_path(config))
    out = args.out or os.path.join(state_dir, "dashboard.html")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8", errors="replace") as f:
        f.write(render(data, config, seats_state))
    print(out)
    return out


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_dashboard -v`
Expected: all 11 tests PASS. If `test_all_sections_present_and_svg_is_well_formed` fails on `ET.fromstring`, an attribute value contains an unescaped `&` or `<` — every dynamic value inside SVG must go through `esc()`.

- [ ] **Step 5: Render the dashboard against the fixture and look at it**

Run: `BALANCEWHEEL_CONFIG=tests/fixtures/config.json BALANCEWHEEL_METRICS_LOG=tests/fixtures/modern.jsonl python3 metrics/dashboard.py --out /tmp/bw-dashboard.html && open /tmp/bw-dashboard.html`
Expected: six sections; the timeline shows two circles with a dashed "model change: beta" guide at the second; roster rows for alpha, beta, gamma, delta (omega is disabled but listed as configured); the watch table shows one resolved watch. Fix anything that reads wrong before committing.

- [ ] **Step 6: Commit**

```bash
git add metrics/dashboard.py tests/test_dashboard.py
git commit -m "feat: trend dashboard — right-now tiles, roster meters, stacked counts, date-axis timeline"
```

---

### Task 6: Repo hygiene — CLAUDE.md, test runner, ignore file

**Files:**
- Modify: `CLAUDE.md` (the "What this repo is" and "nothing to build" paragraphs)
- Create: `.gitignore`
- Create: `tests/__init__.py` (empty; makes `python3 -m unittest tests.test_x` work from the root)

- [ ] **Step 1: Update `CLAUDE.md`**

Replace the paragraph beginning "There is nothing to build, lint, or test." with:

```markdown
There is now code (extraction started 2026-09-03; the roadmap records the amended gate).
Python 3.9+ standard library only; no packages to install. Run the tests from the repo root:

    python3 -m unittest discover -s tests -v

`metrics/` holds the config loader, the JSONL store (the only reader/appender), the roster
watches, the logger CLI, and the dashboard generator. `tests/fixtures/` are synthetic — keep
them that way (privacy firewall). Commits use a lowercase summary with a `feat:`/`fix:`/
`test:`/`docs:` prefix.
```

And in "What this repo is", change "no code lives here yet" to "code is being extracted here per the amended roadmap gate".

- [ ] **Step 2: Add `.gitignore` and `tests/__init__.py`**

`.gitignore`:

```
__pycache__/
*.pyc
/dashboard.html
```

`tests/__init__.py`: empty file.

- [ ] **Step 3: Run the full suite from a clean shell**

Run: `cd <repo> && env -i HOME="$HOME" PATH=/usr/bin:/bin /usr/bin/python3 -m unittest discover -s tests -v`
Expected: all tests pass under the system Python with no environment (proves no hidden dependency). If the system Python is older than 3.9, use `python3` from PATH and note the version in the commit message.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md .gitignore tests/__init__.py
git commit -m "docs: repo now has code — test runner, ignore file, firewall note"
```

---

## Self-review

**Spec coverage (this plan's scope):**
- Config: keys, defaults, seat validation, effort table, `schema_version`, key-path errors, JSON line/col → Task 1. ✔
- Store: five event types, legacy normalisation (`id`, seats list, null counts, `panel_ts` amends, legacy watch ids), truncated-line tolerance, locked single-write append, env override → Task 2. ✔
- Watches: three watches, timestamp-free ids, performed vs suggest, null-unique exclusion, partial counts as verification, window → Task 3. ✔
- Logger: `config/usage/panel/check/watches/resolve/amend`, computed `unique`, count-mismatch rejection with `--force`, unknown-seat rejection, dispute shape, re-check after amend, dedupe → Task 4. ✔
- Dashboard: six sections, stat tiles (open watches, last panel, verified seats, freshness), roster meter (grey/amber/red), stacked counts, real date axis, area-encoded circles, model-change guides from recorded models, presence dots, watch ticks, escaping, `<details>`, both themes → Task 5. ✔
- `usage.model` stamping: `log.py usage --model` → Task 4 (Plan 2's `seat` passes it). ✔
- Deferred to Plan 2: `seats.json` is *read* here (Task 5) but written by setup. Deferred to Plan 3: panel commands, README, reference-implementation/architecture/roadmap updates, `docs/seat-policy.md`.

**Placeholder scan:** none; every step has its code or exact command.

**Type consistency:** `store.load()` keys (`usage`, `panels`, `watches`, `events`, `warnings`) are used identically in `watches.py`, `log.py`, `dashboard.py`. `watch_id(kind, target, panel_id)` signature matches its three call sites. `validate_panel` returns the record the tests inspect (`id`, `seats`, `findings[..]["unique"]`, `notes`). `compute_counts` verdict keys match `VERDICTS`. `dashboard.timeline_positions(timestamps, width)` and `radius_for(count)` are module-level as the tests import them.

---

## Post-execution deviations (2026-09-03)

The code on the branch is the source of truth; these are the places it deliberately differs from the task text above, each found by review during execution.

- **Task 4** — `cmd_amend`'s findings-only branch now appends the `count_mismatch` note like the `findings_detail` branch (the listing dropped it). `test_check_does_not_recreate_watches_written_under_older_ids` was rewritten to build its own log with a skim-worthy panel and a legacy watch line with no id and a truncated target, so it can fail.
- **Final review fix wave** (five `fix:` commits):
  - `store.py`: `load()` dedupes duplicate panel ids (last occurrence survives, warning kept) and warns on duplicate legacy timestamps; `panel_id_for` coerces non-string ids; `target`, `seats`, `findings_raw`, `findings_after_triage` are normalised; `append()` repairs a missing trailing newline after a cut line before writing and encodes with `errors="replace"`.
  - `watches.py`: `_is_count` for `unique`; the redundant-seat id is keyed on the newest panel in the window, not the seat's last qualifying panel.
  - `log.py`: the legacy prefix dedupe applies only to watches without `panel_id`; redundant-seat candidates are gated by `should_refire` first, then exact id; `findings_raw`/`findings_after_triage` must be non-negative ints (panel and amend); dispute fields are type-checked; a findings-only amend on a panel without detail merges over existing counts; `amend` and `resolve` run their read-validate-append under `store.locked()` with `run_check` called after the block; `REJECT` narrowed to `(ValueError, TypeError)`.
  - `dashboard.py`: `esc()` also strips U+FFFE/U+FFFF; `main()` survives an unreadable `seats.json` and surfaces it as a warning.
  - Tests: fixture files opened with context managers; the multibyte-cut fixture really ends mid-codepoint; the control-character assertion uses the `"\x01"` escape. Suite: 69 tests.
- **Parked**: a dedicated test for the redundant-seat re-fire when the seat sits out later panels (fix is in place, not directly exercised); disabled seats are still evaluated and rendered; dropped findings still count as group members for `unique`; `rounds: 0` accepted; a detail-only amend zeroes other seats' counts without a note.
