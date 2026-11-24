# `bw pack export` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `bw pack export --to <pack dir>` rewrites one marked block of esc's `model-seats` `pack.yaml` catalog, and its `version:` line, from the metrics log and the config.

**Architecture:** A new package `packs/` holds one module, `packs/export.py`. Pure functions turn the config and `store.load()` output into catalog entries, and turn `pack.yaml` text plus entries into new text, a version bump, and a changelog bullet. One function does the I/O: the git check, the atomic write, the bullet file, and one `pack_export` event. `bin/bw` gains `cmd_pack`. The seat key `title` joins the config schema first. The seat key `tier` comes from Plan 5 (`2026-11-22-plan-5-seats.md`, Task 5), which this plan needs to land first.

**Tech Stack:** Python 3.9+ standard library only (`json`, `re`, `difflib`, `os`). `unittest`. No YAML library.

**Spec:** `docs/superpowers/specs/2026-11-22-seats-panel-guard-packs-design.md`, section "Project 5 — `packs/`: `bw pack export`", plus "Amendments to earlier documents" (the `CLAUDE.md` and `docs/roadmap.md` bullets) and the config line `seats.*.tier`, `seats.*.title`.

## Global Constraints

- Python 3.9+ standard library only. No YAML parser: the export writes the marked block as text.
- Run tests from the repo root: `python3 -m unittest discover -s tests -v`.
- Fixtures are synthetic. Copy no esc file contents into `tests/fixtures/` beyond the minimal shape (`schema`, `name`, `version`, `description`, `rules:`, `catalog:` with `name`/`category`/`status`/`notes`).
- Privacy firewall: the export carries "per-seat aggregates, model ids, runtimes, and the user's own policy text. It carries no target, finding title, path, project, or conversation."
- Markers, verbatim: `# bw:catalog:begin` and `# bw:catalog:end`.
- Pack name, verbatim: `model-seats`. Any other `name` is refused.
- Statuses bw writes: `preferred`, `reserved`, `review-required`. "bw never writes `banned`; that is a human decision."
- Category for every generated entry: `model-seat`.
- "Strings are emitted as JSON strings, which are valid YAML double-quoted scalars."
- "It replaces only the lines between the markers and the `version:` line, atomically. Nothing else."
- "bw never edits esc's `CHANGELOG.md`." The bullet goes to stdout and to `state_dir/packs/model-seats-<version>.md`.
- "`--dry-run` prints the diff and the bullet and writes nothing."
- Version bump: "minor when any entry's status, model, or membership changes; patch when only numbers change."
- `metrics/store.py` is the only appender to the metrics log.
- Any change to a `bw` command, a module, or a config key also updates `CHANGELOG.md` `[Unreleased]` and regenerates `docs/usage-map/data.generated.js` with `python3 docs/usage-map/generate.py`, in the same commit.
- New config keys go in `metrics/config.py`, `balancewheel.example.json`, and `CHANGELOG.md` in the same commit.
- Commit summaries: lowercase, with a `feat:` / `fix:` / `test:` / `docs:` prefix.
- Do not write into the esc checkout during development or tests. Tests use temp dirs only.

## Review Focus

- **Policy text with YAML-hostile characters** (`"`, `#`, `: `, a newline, non-ASCII, U+2028): each emitted value stays on one line and `json.loads` of the scalar gives back the exact text. Test: `test_render_values_are_single_line_json` in Task 3.
- **A second export with no new panels:** nothing is written, the version stays, and the bullet is not saved. The record date is the date of the last counted panel, not today, so the text does not move by itself. Tests: `test_record_date_is_last_counted_panel` in Task 2, `test_second_export_is_a_no_op` in Task 4.
- **A seat whose model goes A, then B, then A again; a legacy panel with no model; a seat used before its first panel:** every panel under model A counts, the earlier ones too (the spec's rule: "only panels where the seat ran its current model count"). A legacy panel (seats as a list, model `None`) never counts. The month in the name comes from the first event of any kind under the model, so the first panel does not rename the entry. Tests: `test_numbers_use_every_panel_under_the_current_model`, `test_legacy_panel_without_model_not_counted`, `test_month_is_stable_from_usage_to_first_panel` in Task 2.
- **`pack.yaml` is a symlink, changes between read and write, has CRLF line endings, or has no final newline:** a symlink and a concurrent change are refused; CRLF and a missing final newline outside the block survive byte for byte. Tests: `test_symlink_refused`, `test_changed_during_export_refused` in Task 4; `test_crlf_and_no_final_newline_preserved` in Task 3.
- **Bad markers or version line:** two begin markers, end before begin, markers above the `catalog:` line, the `version:` line inside the block, or a version that is not `X.Y.Z` are refused with a message that names the problem. A multi-line value inside the markers is refused, not silently re-parsed. Tests: `test_bad_markers_refused`, `test_bad_version_refused`, `test_version_inside_block_refused`, `test_multiline_value_inside_block_refused` in Task 3.

---

## File Structure

- `metrics/config.py` — modify: seat key `title` (Task 1). `tier` is Plan 5's (its Task 5).
- `balancewheel.example.json` — modify: a real `policy` on every exported seat (Task 1).
- `packs/__init__.py` — create: package docstring only (Task 2).
- `packs/export.py` — create: entries (Task 2), pack text (Task 3), the export run (Task 4).
- `bin/bw` — modify: `bw pack export`, `cmd_pack`, a shared `_panel_data` (Task 4).
- `docs/usage-map/generate.py` — modify: add `"packs"` to `MODULE_DIRS` (Task 4).
- `tests/test_config_v2.py` — modify: seat key tests (Task 1).
- `tests/test_pack_export.py` — create: all pack tests (Tasks 2–4).
- `tests/fixtures/packs/model-seats/pack.yaml` — create: synthetic pack with markers and a hand `banned` entry (Task 3).
- `CHANGELOG.md`, `docs/usage-map/data.generated.js`, `CLAUDE.md`, `docs/roadmap.md`, `README.md` — modify (Tasks 1 and 4).
- `docs/usage-map/infra.js` — modify: the esc node text, the planned `packs/` node, its edges, and the `toesc` journey (Task 4).

---

### Task 1: Seat key `title`, and a real policy on every exported seat

Plan 5 owns `seats.*.tier` (`2026-11-22-plan-5-seats.md`, Task 5): `SEAT_TIERS`, its validation, its tests, and `"tier": "reserved"` on `fable` in the example. This plan does not add `tier` again. Execute this plan only after Plan 5 Task 5 has landed. Task 1 keeps only what is still missing: `title`, and the example policies. If a Project 1 or 2 branch has already landed `title`, skip Steps 1–4.

**Files:**
- Modify: `metrics/config.py` (`V2_SEAT_KEYS` at `:56`, `_validate_seat` at `:101`) (build ruling, 2026-11-23, as-built)
- Modify: `balancewheel.example.json` (seat policies)
- Modify: `CHANGELOG.md` (`[Unreleased]`, `### Added`), `docs/usage-map/data.generated.js` (regenerated)
- Test: `tests/test_config_v2.py`

**Interfaces:**
- Consumes: `config["seats"][name]["tier"] in config.SEAT_TIERS` from Plan 5 Task 5. Plan 5 Task 5 defines and
  names `SEAT_TIERS`; import it, do not write the set again (build ruling, 2026-11-23).
- Produces: every resolved seat dict gains `"title"` (str or `None`, default `None`). Accepted at schema 2 and 3 only (it joins `V2_SEAT_KEYS`).

- [ ] **Step 1: Write the failing tests** in `tests/test_config_v2.py`, class `SchemaV2`, using the file's `v2()` and `load_obj()` helpers.

```python
def test_seat_title_default_and_accepted(self):
    self.assertIsNone(load_obj(v2())["seats"]["beta"]["title"])
    o = v2(); o["seats"]["beta"]["title"] = "Escalation seat"
    self.assertEqual(load_obj(o)["seats"]["beta"]["title"], "Escalation seat")

def test_seat_title_must_be_one_nonempty_line(self):
    for bad in ("", "  ", "two\nlines", "cr\rline", 7):
        o = v2(); o["seats"]["beta"]["title"] = bad
        self.rejects(o, "seats.beta.title")

def test_schema_1_refuses_title(self):
    o = json.loads(V1.read_text()); seat = next(iter(o["seats"]))
    o["seats"][seat]["title"] = "x"
    self.rejects(o, f"seats.{seat}.title: unknown key")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_config_v2 -v`
Expected: the three new tests FAIL (`KeyError: 'title'` or "unknown key").

- [ ] **Step 3: Implement in `metrics/config.py`**

As built (build ruling, 2026-11-23, as-built): `SEAT_TIERS = frozenset({"default", "reserved"})` is at `metrics/config.py:57`. `tier` is in `V2_SEAT_KEYS` (`:56`), is validated at `:130-131`, and is returned at `:132`. No seat has `title` yet. Add `"title"` to `V2_SEAT_KEYS` at `:56`. In `_validate_seat` (`:101`), check `title` beside `tier`. Add `"title"` to the returned dict at `:132-134`. Check `title` with `f"{kp}.title: must be a non-empty single-line string"` (`None` allowed; a string must have `strip()` content and no `\n` or `\r`). Add `"title"` to the returned dict, next to Plan 5's `"tier"`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: all PASS.

- [ ] **Step 5: Update the example, the changelog, and the usage map**

In `balancewheel.example.json`, give each seat the export lists a real one-sentence `policy`. Replace `astra`'s placeholder text ("why this seat has these roles; the evidence cited; what would change them") too, because Task 2 refuses placeholder-like policies only by angle brackets or `TODO`, and a template sentence must not ship as policy. Texts:
- `astra`: "First voice on plan, design, and decision targets and an equal peer on code review; re-demote only on logged dispute evidence."
- `opus`: "The moderator's own model family in a fresh headless session, scored as a peer; the moderator must not favor it."
- `fable`: "Joins only deep panels and stuck debugging, or when the user asks; each run is scored like every other seat."
- `gem`: "Peer on review and the stand-in for capped seats; scored under its own name."
- `ox`: "Metered peer on every target kind at cents per review."
- `kimi`: "Peer on review; plan on trial." (It is disabled in the example, so the export skips it, but a user who enables it must not hit the empty-policy refusal.)

Update the example's `_comment` text if it lists seat keys.

In `CHANGELOG.md` `[Unreleased]` `### Added`: one bullet, "Seat key `title` (an optional catalog name for `bw pack export`), schema 2 and 3; the example's seats carry a real `policy`."

Run: `python3 docs/usage-map/generate.py`
Expected: it writes `docs/usage-map/data.generated.js` with no error. Then run `python3 -m unittest discover -s tests -v` again: all PASS.

- [ ] **Step 6: Commit**

```bash
git add metrics/config.py tests/test_config_v2.py balancewheel.example.json CHANGELOG.md docs/usage-map/data.generated.js
git commit -m "feat: seat key title, and a real policy on every example seat"
```

---

### Task 2: Catalog entries from the config and the log

**Files:**
- Create: `packs/__init__.py` (docstring: "packs — exports of seat policy to OpenEscapement rule packs.")
- Create: `packs/export.py` (module docstring says it owns the `model-seats` catalog block; this task adds the entry functions)
- Test: `tests/test_pack_export.py`

**Interfaces:**
- Consumes: resolved config from Task 1 and Plan 5 Task 5 (`seats.*.tier`, `seats.*.title`, `seats.*.effort`, `leads.*.plan`, `seats.*.policy`, `seats.*.enabled`, `seats.*.roles`, `seats.*.runtime`, `seats.*.model`, `profiles.*.checkpoints.*.seats`, `profiles.*.lead_order`, `default_profile`, `leads.*`, `watches.redundant_min_panels`); `store.load()` output; `summary.seat_rows(data, config)` (`metrics/summary.py:49-73`) and `summary.lead_rows(data, inbox_items)` (`:143-199`); `quota.lead_model(state, lead) -> (model, resolved)` (`lead/quota.py:266-273`), the one helper for the model a lead runs as, from Plan 6 Task 9 (build ruling, 2026-11-23); `seats.verify.seat_items(config)` from Plan 5 Task 10, the one reachability walk (build ruling, 2026-11-23).
- Produces:
  - `class PackError(Exception)` — every refusal; the message is the one line the user sees.
  - `exported_seats(config) -> list[str]` — returns `seats.verify.seat_items(config)`: enabled seats named in any profile checkpoint's `seats`, every enabled `fallback` of such a seat (followed until no new name), and every enabled seat with `tier == "reserved"` (moderator's call: the stand-in is real policy and the seat verification scope includes it). Fallback-only seats stay in the export. Do not repeat the walk here; call `seat_items` (build ruling, 2026-11-23). Call it as `verify.seat_items(c)` through the module (`from seats import verify`), so `mock.patch("seats.verify.seat_items")` works. It reads `profiles.*.checkpoints.*.seats` and `tier == "reserved"`, in config order (`seats/verify.py:59-74`). Add test `test_fallback_only_seat_is_exported`. A fallback-only seat scores under its own name: a panel record keys `seats` by the seat that ran, so a stand-in scores under the fallback's name, and the capped seat is in `absent` (`panel/record.py:151-158`). Expect such a seat at `review-required` until it has `redundant_min_panels` (5) panels (build ruling, 2026-11-23, as-built).
  - `model_panels(panels, seat, model) -> list[dict]` — every panel, sorted by `ts`, where `p["seats"].get(seat) == model`. A model that returns keeps its earlier panels (spec: "only panels where the seat ran its current model count").
  - `seat_role(config, seat) -> str`
  - `seat_entry(config, data, seat) -> dict` with keys `name`, `status`, `notes`.
  - `lead_model(config, state, lead) -> str` — returns `quota.lead_model(state, config["leads"][lead])[0]`. Pass the lead dict, not the id (build ruling, 2026-11-23, as-built). It is the model part of `quota.lead_model(state, lead)` (Plan 6 Task 9). That helper holds the rule: for a `claude-code` lead, `quota.read_model(state, lead["plan"])` when it returns a value, else the configured model; for every other runtime, the configured model. `lead/runner.py` calls the same helper when it logs a session, so the export matches the logged `model`. Do not repeat the rule here (build ruling, 2026-11-23).
  - `lead_entries(config, data, state) -> list[dict]` (same keys).
  - `entries(config, data, state) -> list[dict]` — seat entries, then lead entries, with names made unique (below).

Decisions this task fixes (moderator's calls, where the spec leaves a choice):
- **Role** (`seat_role`), first match wins, in the spec's order: `title` when set; `roles[0]` is `plan` or `decision` → `"Planning seat"`; `tier == "reserved"` → `"Reserved escalation seat"`; `runtime` equals the runtime of the first lead in the default profile's `lead_order` → `"Blind same-vendor seat"` (no lead: skip this rule); else `"Peer seat"`.
- **Name**: `f"{role} (reference roster, {month}, {model} via {runtime})"`. `month` is the `YYYY-MM` of the **first event of any kind** under the current model: the earliest `ts` among the model's panels and the `usage` events with this seat and model. With no such event, the literal `not yet run`. So a seat used before its first panel keeps its name when that panel arrives, and the month never comes from the clock. Events whose `ts` does not parse (`summary._parse` returns `None`) are skipped for the month and the date; they never crash a slice.
- **Unique names** (`entries`): when two entries would have the same name, add a distinguisher before the closing parenthesis: `f", effort {effort}"` for a seat (when `effort` is set), `f", {plan} plan"` for a lead. A name that is still not unique raises `PackError`: `"two catalog entries would be named <name>; set seats.<id>.title on one"`. Without this, `bump_kind` and `bullet`, which key by name, would merge two entries.
- **Record line** (model panels not empty): `f"Record under this model to {last}: {panels} panels, {confirmed} confirmed, {partial} partial, {refuted} refuted; {unique_recent} unique in the last {recent_panels} panels; {wins} of {involved} disputes won."` `last` is the `YYYY-MM-DD` of the last model panel. The numbers are the seat's row (pick it with `row["seat"] == seat`; a seat's panels are the panels with the seat in `findings`, not in `seats`, `metrics/summary.py:54`; `seat_names` lists every config seat first, so a row exists with zero panels, `:21-22`) (build ruling, 2026-11-23, as-built), from `summary.seat_rows` called on a copy of `data` whose `panels` is `model_panels(...)`. No model panels: `"No panels under this model yet."`
- **Status**: `reserved` for `tier == "reserved"`; `review-required` when the row's `panels` is under `config["watches"]["redundant_min_panels"]`, or `data["watches"]` (normalised, with `kind`, `target`, `status`; the status defaults to `open` when unknown, `metrics/store.py:152-163`) holds an `open` watch of kind `redundant-seat` whose `target` is the seat; else `preferred`.
- **Notes**: `policy.strip() + " " + record line`. `entries()` raises `PackError` that names every exported seat with an empty policy: `"seats with no policy (the pack must say why a seat holds its duties): <names>"`. It also raises `PackError` for a policy that still reads like a placeholder, that is, one that contains `<` and `>`, or `TODO`: `"seats whose policy is still a placeholder: <names>"`.
- **Leads**: one entry per lead id in any `lead_order` (default profile first, then the other profiles by name; each lead once). `model` is `lead_model(config, state, lead)`, so a configured alias (`opus`) shows as the logged model. Name `f"Lead (reference roster, {month}, {model} via {runtime})"`, month from the first session start with that `(runtime, model)`, else `not yet run`. Take the month and `last` from `s["start"]` of the sessions with that `(runtime, model)` in `data["sessions"]`, parsed with `summary._parse` (`metrics/summary.py:170-171` reads `s.get("start")`), not from the start event's `ts` (build ruling, 2026-11-23, as-built). Status always `review-required` (bw does not score lead quality yet). Notes: `"A lead in a lead order; bw does not score lead quality yet. "` plus `f"Record under this model to {last}: {sessions} sessions, {minutes} lead minutes, {good} good and {bad} bad ratings, {cap_switches} switches on a cap."` from the `summary.lead_rows(data, [])` row for `(runtime, model)` (pick it with `r["runtime"] == rt and r["model"] == model`; the list is sorted by `(runtime, model)`) (build ruling, 2026-11-23, as-built); `last` is the last session start date. No sessions: `"No sessions under this model yet."` No profile name, lead id, or project appears.

- [ ] **Step 1: Write the failing tests** in `tests/test_pack_export.py`.

Test setup: put the repo root on `sys.path`, import `lead.paths` (it puts `metrics/` on the path), then `config as cfg`, `store`, and `from packs import export`. **Environment:** `setUp` removes `BALANCEWHEEL_METRICS_LOG` and `BW_STATE_DIR` from `os.environ` with `mock.patch.dict(os.environ)` (started in `setUp`, stopped with `addCleanup`), as `tests/test_store.py` keeps the store on a temp log; every log path in these tests is inside a temp dir, so `store.log_path(config)` never reaches the user's real log. A helper `make_config(top=None, **seat_overrides)` writes a schema-3 config to a temp file and loads it; `top` merges top-level keys (Task 4 passes `state_dir` and `metrics_log`). Copy the minimal plan and lead shape from `tests/fixtures/config_v3.json`. Seats, all with a non-empty `policy`: `alpha` (codex, model `model-a`, roles `["plan","review"]`), `beta` (claude-code, `model-b`, roles `["review"]`), `gamma` (claude-code, `model-g`, roles `["debug"]`, `tier: "reserved"`), `eps` (pi, `model-e`, roles `["review"]`), `off` (pi, enabled `false`, policy `""`), `idle` (pi, not on a checkpoint). Lead `l1`: claude-code, model `m-lead`, plan `p1`. Profile `d`: `lead_order: ["l1"]`, checkpoint `review` with seats `alpha, beta, eps`.

A helper `panel(pid, ts, seats, counts=None, disputes=(), **over)` builds a `panel` event with `seats` as a `{seat: model}` dict. `findings` holds an entry for **every** seat in `seats`, as `metrics/log.py:119-131` writes it: the `counts` tuple `(confirmed, partial, refuted, unique)` when given, with `total` their sum, else all zeros. So a panel with `counts=None` still counts as one panel for each seat. Keep the helper on `store.append`, not `metrics/log.py`: `log.py` recomputes the counts from `findings_detail` ("computed is authoritative", `:117-131`), and `store.append` writes the event as given, so the given counts stand. Say so in the helper's docstring (build ruling, 2026-11-23, as-built). `over` sets other event keys (`target`, `notes`, `findings_detail`, `absent`). A helper `load(events)` appends events through `store.append` to a temp log and returns `store.load(path)`. `make_config(eps={...})` merges the dict into that seat.

```python
def test_exported_seats_are_enabled_and_reachable(self):
    self.assertEqual(export.exported_seats(make_config()), ["alpha", "beta", "gamma", "eps"])

def test_fallback_only_seat_is_exported(self):
    c = make_config(alpha={"fallback": "idle"})
    self.assertEqual(export.exported_seats(c), ["alpha", "beta", "gamma", "eps", "idle"])

def test_exported_seats_is_seat_items(self):                         # build ruling, 2026-11-23
    c = make_config(alpha={"fallback": "idle"})
    with mock.patch("seats.verify.seat_items", return_value=["beta"]) as si:
        self.assertEqual(export.exported_seats(c), ["beta"])
    si.assert_called_once_with(c)

def test_role_names(self):
    c = make_config()
    self.assertEqual([export.seat_role(c, s) for s in ("alpha", "gamma", "beta", "eps")],
                     ["Planning seat", "Reserved escalation seat", "Blind same-vendor seat", "Peer seat"])

def test_title_overrides_role(self):
    c = make_config(eps={"title": "Fast metered seat"})
    self.assertEqual(export.seat_role(c, "eps"), "Fast metered seat")

def test_numbers_use_every_panel_under_the_current_model(self):
    # alpha: model-a, then old-a, then model-a twice; the three model-a panels count, old-a does not
    data = load([panel("p1", "2026-09-01T00:00:00Z", {"alpha": "model-a"}, {"alpha": (9, 0, 0, 1)}),
                 panel("p2", "2026-10-01T00:00:00Z", {"alpha": "old-a"}, {"alpha": (7, 0, 0, 1)}),
                 panel("p3", "2026-11-02T00:00:00Z", {"alpha": "model-a"}, {"alpha": (2, 1, 0, 1)}),
                 panel("p4", "2026-11-09T00:00:00Z", {"alpha": "model-a"}, {"alpha": (3, 0, 1, 0)})])
    e = export.seat_entry(make_config(), data, "alpha")
    self.assertEqual(e["name"], "Planning seat (reference roster, 2026-09, model-a via codex)")
    self.assertIn("to 2026-11-09: 3 panels, 14 confirmed, 1 partial, 1 refuted; 2 unique in the last 3 panels", e["notes"])

def test_legacy_panel_without_model_not_counted(self):
    legacy = panel("p1", "2026-11-01T00:00:00Z", {"alpha": "model-a"}); legacy["seats"] = ["alpha"]
    e = export.seat_entry(make_config(), load([legacy]), "alpha")
    self.assertTrue(e["notes"].endswith("No panels under this model yet."))
    self.assertIn("not yet run", e["name"])

def test_month_is_stable_from_usage_to_first_panel(self):
    use = {"type": "usage", "seat": "alpha", "model": "model-a", "ok": True, "ts": "2026-10-20T00:00:00Z"}
    before = export.seat_entry(make_config(), load([use]), "alpha")["name"]
    after = export.seat_entry(make_config(), load([use, panel("p1", "2026-11-02T00:00:00Z", {"alpha": "model-a"})]), "alpha")["name"]
    self.assertEqual(before, after)
    self.assertIn("2026-10", after)

def test_record_date_is_last_counted_panel(self):
    data = load([panel("p1", "2026-11-03T10:00:00Z", {"alpha": "model-a"})])
    self.assertIn("to 2026-11-03: 1 panels,", export.seat_entry(make_config(), data, "alpha")["notes"])

def test_status_rules(self):
    five = [panel(f"p{i}", f"2026-11-0{i}T00:00:00Z", {"alpha": "model-a", "beta": "model-b"},
                  {"alpha": (1, 0, 0, 1), "beta": (1, 0, 0, 1)}) for i in range(1, 6)]
    c = make_config()
    data = load(five)
    self.assertEqual(export.seat_entry(c, data, "alpha")["status"], "preferred")
    self.assertEqual(export.seat_entry(c, load(five[:4]), "alpha")["status"], "review-required")
    self.assertEqual(export.seat_entry(c, data, "gamma")["status"], "reserved")
    w = {"type": "watch", "id": "w1", "kind": "redundant-seat", "target": "alpha", "status": "open"}
    self.assertEqual(export.seat_entry(c, load(five + [w]), "alpha")["status"], "review-required")

def test_disputes_counted_under_current_model(self):
    d = {"summary": "s", "challenger": "beta", "proposals": {"alpha": "x", "beta": "y"}, "winner": "alpha"}
    data = load([panel("p1", "2026-11-01T00:00:00Z", {"alpha": "model-a", "beta": "model-b"}, disputes=[d])])
    notes = export.seat_entry(make_config(), data, "alpha")["notes"]
    self.assertIn(": 1 panels,", notes); self.assertIn("1 of 1 disputes won", notes)

def test_empty_policy_refused(self):
    with self.assertRaises(export.PackError) as e:
        export.entries(make_config(eps={"policy": ""}), load([]), self.state)
    self.assertIn("eps", str(e.exception))

def test_placeholder_policy_refused(self):
    for text in ("<why this seat holds its duties>", "TODO: write this"):
        with self.subTest(text), self.assertRaises(export.PackError) as e:
            export.entries(make_config(eps={"policy": text}), load([]), self.state)
        self.assertIn("placeholder", str(e.exception))

def test_colliding_names_get_a_distinguisher(self):
    # two codex seats, same role, model, and month, different effort
    c = make_config(eps={"runtime": "codex", "model": "model-a", "roles": ["plan"], "effort": "high"},
                    alpha={"effort": "low"})
    names = [e["name"] for e in export.entries(c, load([]), self.state)]
    self.assertEqual(len(names), len(set(names)))
    self.assertTrue(any(", effort high)" in n for n in names))

def test_collision_that_remains_is_refused(self):
    c = make_config(eps={"runtime": "codex", "model": "model-a", "roles": ["plan"]})  # no effort on either
    with self.assertRaises(export.PackError) as e:
        export.entries(c, load([]), self.state)
    self.assertIn("title", str(e.exception))

def test_lead_entry(self):
    s = {"type": "session", "phase": "start", "session_id": "s1", "lead": "l1", "runtime": "claude-code",
         "model": "m-lead", "ts": "2026-11-05T09:00:00Z", "project": "secret-project"}
    leads = export.lead_entries(make_config(), load([s]), self.state)
    self.assertEqual(leads, [{"name": "Lead (reference roster, 2026-11, m-lead via claude-code)",
                              "status": "review-required", "notes": leads[0]["notes"]}])
    self.assertIn("to 2026-11-05: 1 sessions", leads[0]["notes"])
    self.assertNotIn("secret-project", leads[0]["notes"])

def test_lead_alias_resolves_to_logged_model(self):
    # configured alias "opus"; the statusline recorded the resolved id, and the session logged it
    os.makedirs(os.path.join(self.state, "models"))
    with open(os.path.join(self.state, "models", "p1.json"), "w") as f:
        json.dump({"model": "model-opus-9"}, f)
    s = {"type": "session", "phase": "start", "session_id": "s1", "lead": "l1", "runtime": "claude-code",
         "model": "model-opus-9", "ts": "2026-11-05T09:00:00Z"}
    c = make_config(top={"leads": {"l1": {"runtime": "claude-code", "model": "opus", "plan": "p1"}}})
    (lead,) = export.lead_entries(c, load([s]), self.state)
    self.assertIn("model-opus-9 via claude-code", lead["name"]); self.assertIn("1 sessions", lead["notes"])

def test_two_leads_on_one_model_named_by_plan(self):
    ...  # leads l1 (plan p1) and l2 (plan p2), both claude-code m-lead → names end ", p1 plan)" and ", p2 plan)"
```

`self.state` is a temp dir from `setUp`. The `...` test adds plan `p2` and lead `l2` through `top`, and is one `assertEqual` on the two names.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_pack_export -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'packs'`.

- [ ] **Step 3: Implement the functions in `packs/export.py`**

Signatures as in the Interfaces block. Import `summary` after `from lead import paths  # noqa: F401`, as `cockpit/views.py` does, `from lead import quota` for `lead_model`, and `from seats import verify` for `seat_items` (build ruling, 2026-11-23). Importing `seats.verify` pulls in `seats.adapters`, `seats.grants`, `lead.sandbox`, and `lead.containment` at import time, with no side effect. Plan 9 edits no file in any key (`lead/sandbox.py`, `lead/runtimes.py`, `lead/codepaths.py`, `seats/`), so no lead or seat pass resets (build ruling, 2026-11-23, as-built). Do not write `seat_rows`, the reachability walk, or the lead-model rule again: call them.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_pack_export -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add packs/__init__.py packs/export.py tests/test_pack_export.py
git commit -m "feat: pack catalog entries from the seat scoreboard under each current model"
```

(No usage-map run here: no `bw` command exists yet, and `packs/` joins `MODULE_DIRS` in Task 4. Task 4 regenerates the map.)

---

### Task 3: The pack text — markers, render, version bump, bullet

**Files:**
- Modify: `packs/export.py`
- Create: `tests/fixtures/packs/model-seats/pack.yaml`
- Test: `tests/test_pack_export.py`

**Interfaces:**
- Consumes: entry dicts (`name`, `status`, `notes`) from Task 2; `PackError`.
- Produces:
  - `MARK_BEGIN = "# bw:catalog:begin"`, `MARK_END = "# bw:catalog:end"`, `PACK_NAME = "model-seats"`, `MARKER_HELP` (the one-time instruction: "put `# bw:catalog:begin` and `# bw:catalog:end` around the catalog entries bw will own").
  - `pack_name(text: str) -> str | None` — the top-level `name:` value, quotes stripped.
  - `find_block(text: str) -> (start: int, end: int, indent: str)` — character offsets of the text strictly between the begin marker's line end and the end marker's line start; `indent` is the begin marker's leading whitespace.
  - `find_version(text: str) -> (start: int, end: int, version: tuple[int, int, int])` — offsets of the version number only, so quotes and spacing stay.
  - `render(entries: list[dict], indent: str, newline: str) -> str` — the new block text.
  - `parse_block(block: str) -> list[dict]` — the entries in the current block.
  - `bump_kind(old: list[dict], new: list[dict]) -> "minor" | "patch" | None`
  - `next_version(version: tuple, kind: str) -> str`
  - `rewrite(text: str, entries: list[dict]) -> (new_text: str, old_version: str, new_version: str | None, old_entries: list[dict])` — `new_version` is `None` and `new_text == text` when `bump_kind` is `None`.
  - `bullet(old_version: str, new_version: str, old: list[dict], new: list[dict], today: str) -> str`

Rules this task fixes:
- Markers match a whole line: `^[ \t]*# bw:catalog:begin[ \t]*\r?$` (multiline), the same for end. Refuse, with `PackError`, when: either is missing (message is `MARKER_HELP`); either appears twice; end comes before begin; no top-level `^catalog:` line comes before begin.
- `version:` is the one top-level line `^version:[ \t]*(["']?)(\d+)\.(\d+)\.(\d+)\1[ \t]*\r?$`. Refuse when it is missing, appears twice, is not `X.Y.Z`, or lies inside the block.
- Newline style: `"\r\n"` when the begin marker's line ends with `\r\n`, else `"\n"`.
- `render`: four lines per entry, each `json.dumps(value, ensure_ascii=True)` (ASCII escapes, so no U+2028 or U+0085 line break can reach the YAML):
  `{indent}- name: <json>`, `{indent}  category: model-seat`, `{indent}  status: <status>`, `{indent}  notes: <json>`, each ending in `newline`.
- `parse_block`: read `- name:` items and their `status:` and `notes:` lines. A value starting with `"` is `json.loads`-ed; any other value is stripped plain text. Every non-blank line inside the markers must be one of `- name: …`, `category: …`, `status: …`, `notes: …` (after the indent), or a `#` comment. Any other line, such as a YAML continuation line, a block scalar (`|`, `>`), or a quoted value that ends on a later line, raises `PackError`: `"line <n> inside the bw:catalog markers is not a one-line catalog field; bw owns this block — move hand-written entries outside the markers"`. A `json.JSONDecodeError` raises `PackError` with the same line number. One-line hand entries inside the markers parse, and the rewrite replaces them: the bullet names them under `removed`.
- `bump_kind`: `None` when the lists are equal; `"minor"` when the sets of names differ (the name holds role, month, model, and runtime, so a model change is a membership change) or a name's status differs; else `"patch"`.
- `next_version`: minor `X.Y.Z → X.(Y+1).0`; patch `X.Y.Z → X.Y.(Z+1)`.
- `rewrite` applies the later span first, so the earlier offsets stay valid. Bytes outside the block and the version number never change.
- `bullet` is one markdown line: ``f"- `model-seats` {new} ({today}, exported by `bw pack export` from the balancewheel log; was {old}): "`` then the parts that apply, joined by `"; "`: `added <names>`, `removed <names>`, `<name>: <from> to <to>` per status change, `record lines refreshed for <n> entries`. Names in quotes.

The fixture `tests/fixtures/packs/model-seats/pack.yaml`, synthetic, in this shape: `schema: 1`, `name: model-seats`, `version: 0.1.0`, a one-line synthetic `description`, `rules:` with `  - rules/synthetic.md`, `catalog:`, one hand entry `Moderator seat (synthetic)` with status `preferred`, then `  # bw:catalog:begin`, `  # bw:catalog:end` (empty block), then one hand entry `A synthetic banned pattern` with status `banned`. Plain scalars, as esc writes them.

- [ ] **Step 1: Write the failing tests**

```python
FX = HERE / "fixtures" / "packs" / "model-seats" / "pack.yaml"
E1 = {"name": "Planning seat (reference roster, 2026-11, model-a via codex)", "status": "preferred", "notes": "P. Record ..."}

def test_rewrite_keeps_hand_entries_byte_for_byte(self):
    text = FX.read_text()
    new, old_v, new_v, _ = export.rewrite(text, [E1])
    b0, e0, _ = export.find_block(text); b1, e1, _ = export.find_block(new)
    self.assertEqual(text[:b0].replace("version: 0.1.0", ""), new[:b1].replace("version: 0.2.0", ""))
    self.assertEqual(text[e0:], new[e1:])
    self.assertEqual((old_v, new_v), ("0.1.0", "0.2.0"))
    self.assertEqual(export.parse_block(new[b1:e1]), [E1])

def test_render_values_are_single_line_json(self):
    hostile = 'Quote " hash # colon: ok\nnew line, café, sep end'
    block = export.render([dict(E1, notes=hostile)], "  ", "\n")
    lines = block.splitlines()
    self.assertEqual(len(lines), 4)
    self.assertTrue(all(l.isascii() for l in lines))
    self.assertEqual(json.loads(lines[3].split("notes: ", 1)[1]), hostile)

def test_crlf_and_no_final_newline_preserved(self):
    text = FX.read_text().replace("\n", "\r\n").rstrip("\r\n")
    new, *_ = export.rewrite(text, [E1])
    self.assertFalse(new.endswith("\n"))
    self.assertNotIn("\n", new.replace("\r\n", ""))

def test_bad_markers_refused(self):
    base = FX.read_text()
    begin, end = "  # bw:catalog:begin\n", "  # bw:catalog:end\n"
    cases = {"missing": base.replace(begin, ""), "twice": base.replace(begin, begin * 2),
             "order": base.replace(begin, "@@").replace(end, begin).replace("@@", end),
             "above catalog": begin + base.replace(begin, "")}
    for label, text in cases.items():
        with self.subTest(label), self.assertRaises(export.PackError):
            export.find_block(text)

def test_missing_markers_message_is_the_instruction(self):
    with self.assertRaises(export.PackError) as e:
        export.find_block(FX.read_text().replace("  # bw:catalog:begin\n", ""))
    self.assertIn("# bw:catalog:begin", str(e.exception))

def test_bad_version_refused(self):
    base = FX.read_text()
    for label, text in {"not semver": base.replace("0.1.0", "0.1"), "twice": base.replace("version: 0.1.0", "version: 0.1.0\nversion: 0.1.0"),
                        "missing": base.replace("version: 0.1.0\n", "")}.items():
        with self.subTest(label), self.assertRaises(export.PackError):
            export.find_version(text)

def test_version_inside_block_refused(self):  # Review Focus 5
    text = FX.read_text().replace("version: 0.1.0\n", "").replace("  # bw:catalog:end\n", "version: 0.1.0\n  # bw:catalog:end\n")
    with self.assertRaises(export.PackError):
        export.rewrite(text, [E1])

def test_multiline_value_inside_block_refused(self):
    for bad in ('  - name: "a\n    b"\n', "  - name: x\n    notes: |\n      two\n      lines\n", '  - name: "unterminated\n'):
        with self.subTest(bad), self.assertRaises(export.PackError) as e:
            export.parse_block(bad)
        self.assertIn("inside the bw:catalog markers", str(e.exception))

def test_bump_rule(self):
    num = dict(E1, notes="P. Record ... 3 panels")
    self.assertIsNone(export.bump_kind([E1], [E1]))
    self.assertEqual(export.bump_kind([E1], [num]), "patch")
    self.assertEqual(export.bump_kind([E1], [dict(E1, status="review-required")]), "minor")
    self.assertEqual(export.bump_kind([E1], [dict(E1, name=E1["name"].replace("model-a", "model-a2"))]), "minor")
    self.assertEqual(export.bump_kind([E1], []), "minor")
    self.assertEqual([export.next_version((0, 5, 3), k) for k in ("minor", "patch")], ["0.6.0", "0.5.4"])

def test_no_change_keeps_text_and_version(self):
    once, *_ = export.rewrite(FX.read_text(), [E1])
    again, old_v, new_v, _ = export.rewrite(once, [E1])
    self.assertEqual((again, new_v), (once, None))

def test_bullet_names_changes(self):
    b = export.bullet("0.1.0", "0.2.0", [], [E1], "2026-11-22")
    self.assertTrue(b.startswith("- `model-seats` 0.2.0 (2026-11-22,"))
    self.assertIn("added", b); self.assertIn(E1["name"], b)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_pack_export -v`
Expected: the new tests FAIL with `AttributeError: module 'packs.export' has no attribute 'rewrite'` (and the others like it); Task 2's tests still PASS.

- [ ] **Step 3: Implement the functions in `packs/export.py`** with the signatures and rules above.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_pack_export -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add packs/export.py tests/test_pack_export.py tests/fixtures/packs/model-seats/pack.yaml
git commit -m "feat: rewrite the marked model-seats catalog block and bump the pack version"
```

---

### Task 4: `bw pack export`, the write, and the docs

**Files:**
- Modify: `packs/export.py` (the run)
- Modify: `bin/bw` (module-level import, docstring, `COMMANDS` at `:66-67`, parser, `cmd_pack`, `_panel_data`, `_dispatch` at `:447` with its map at `:468`, `_suggestions` at `:311-317`) (build ruling, 2026-11-23, as-built). `seat` and `panel` parse their own argv before argparse (`:433-437`). `pack` does not: give it a normal argparse subparser.
- Modify: `docs/usage-map/generate.py:24` (`MODULE_DIRS` is now `("lead", "lead/guard", "metrics", "cockpit", "seats", "panel")`; append `"packs"`) and `:115` (the tuple is now `("guard", "triage", "sandbox", "improve", "setup", "seat", "panel")`; append `"pack"`) (build ruling, 2026-11-23, as-built)
- Modify: `docs/usage-map/infra.js` (the `esc` node's desc, the `packsexport` node, its two planned edges, the `toesc` journey)
- Modify: `CHANGELOG.md`, `docs/usage-map/data.generated.js`, `CLAUDE.md:43-51`, `docs/roadmap.md:48-56`, `README.md` (command list near line 346; the "planned `packs/`" sentences near lines 633-657)
- Test: `tests/test_pack_export.py`

**Interfaces:**
- Consumes: `rewrite`, `bullet`, `entries`, `pack_name`, `PackError` (Tasks 2–3); `lead.handoff._git_rc(worktree, *args) -> (rc, out, err)` (the safe git runner: hooks, fsmonitor, attributes off, `GIT_*` scrubbed). It also returns rc `CONFIG_UNREADABLE = 125` with no git call, when the repo's filter config cannot be read (`lead/handoff.py:26`, `:70-76`) (build ruling, 2026-11-23, as-built); `store.append`, `store.log_path`, `store.now_ts`.
- Produces:
  - `export(config, data, pack_dir: str, state: str, dry_run: bool = False, today: str | None = None, git_rc=None) -> dict` with keys `path` (str), `changed` (bool), `old_version` (str), `new_version` (str or None), `diff` (str, unified diff, empty when unchanged), `bullet` (str or None), `bullet_path` (str or None). Raises `PackError` on every refusal. `git_rc` defaults to `handoff._git_rc`; `today` defaults to `store.now_ts()[:10]`.
  - `bin/bw`: `bw pack export --to DIR [--dry-run]`; `cmd_pack(a, config, state) -> int`; `_panel_data(config) -> dict` — move `_suggestions`' loop over `improve.panel_logs` (`bin/bw:311-317`, which merges only `panels` today) into it (build ruling, 2026-11-23, as-built): `store.load(store.log_path(config))`, then for each path in `config["improve"]["panel_logs"]` the `panels`, `watches`, and `usage` of `store.load(path)` appended to the same lists. Panels are de-duplicated by `id` (the first log's copy wins: the main log first, then `panel_logs` in config order), then sorted by `str(ts)`. `_suggestions` calls it too. `bin/bw` imports `from packs import export as pack_export` at module level, so `docs/usage-map/generate.py` (which reads only module-level imports) draws the `cmd → packs/export` edge.

`export` does these steps, in order:
1. `path = os.path.join(pack_dir, "pack.yaml")`. Refuse a missing file, a symlink (`os.lstat`), or a non-regular file.
2. Read the bytes with `os.open(path, os.O_RDONLY | os.O_NOFOLLOW)`; decode as strict UTF-8 (refuse otherwise). Refuse when `pack_name(text) != "model-seats"`: `"<path>: name is <x>, not model-seats"`.
3. Git checks, both through `git_rc`:
   - `git_rc(pack_dir, "ls-files", "--error-unmatch", "--", "pack.yaml")`. rc 124 (the runner's timeout): `"git timed out in <dir>; try again"`. rc 125 (as built, build ruling, 2026-11-23): `"git config in <dir> could not be read; git not run"`; map it before the "not a git checkout" check, and for the `status` call too. Another non-zero rc: if `git_rc(pack_dir, "rev-parse", "--git-dir")` also fails, `"<dir> is not a git checkout; bw writes pack.yaml only where git can show the change"`; else `"pack.yaml is not tracked by git (untracked or ignored); commit it first"`. This closes the hole where an ignored file shows nothing in `git status`.
   - `git_rc(pack_dir, "status", "--porcelain", "--untracked-files=all", "--ignored", "--", "pack.yaml")`. rc 124: the timeout message. Any output: `"pack.yaml has uncommitted changes; commit or discard them first"`.
4. `rewrite(text, entries(config, data, state))`. No bump: return `changed: False`; write nothing.
5. Build the diff (`difflib.unified_diff`, labels `a/pack.yaml`, `b/pack.yaml`) and the bullet.
6. `dry_run`: return; write nothing anywhere (no pack, no bullet file, no event).
7. Save the bullet to `<state>/packs/model-seats-<new version>.md` with `_write_text_atomic` (below). This comes **before** the pack write, so a failure here leaves `pack.yaml` unchanged. Do not use `paths.write_json_atomic`: it JSON-encodes its argument.
8. Re-read the file bytes with `O_NOFOLLOW`. Refuse when they differ from step 2, or when the path is now a symlink: `"pack.yaml changed during the export; run it again"`. Then `_write_text_atomic(path, new_bytes, mode=<the old file's st_mode & 0o7777>)`.
9. Append one event through `store.append(store.log_path(config), ...)`: `{"type": "pack_export", "pack": "model-seats", "from": old, "to": new, "bump": kind, "entries": n}`. No path, no dir name. This is the record `lead/improve.py` can learn from (the standing goal). An `OSError` here does not undo the write: `export` returns `changed: True` with `"event_error": "<type name>"`, and `cmd_pack` prints one warning line.

`_write_text_atomic(path: str, data: bytes, mode: int = 0o644) -> None` — a temp file `f".{basename}.{pid}.tmp"` in the same directory, opened with `O_CREAT | O_EXCL | O_WRONLY`, written, `os.fchmod` to `mode`, closed, then `os.replace` onto `path`. In a `try/except BaseException`, unlink the temp file and re-raise, so a failure never leaves a stray file in the esc checkout.

`cmd_pack` output:
- changed: print the diff, a blank line, the bullet, then `bw: wrote <path> (<old> to <new>)`, then `bw: next: paste the bullet in <bullet path> into esc's CHANGELOG.md, re-sign the pack with esc, and commit both in the esc repo`. Exit 0.
- dry run: print the diff and the bullet, then `bw: dry run; nothing written`. Exit 0.
- unchanged: `bw: pack is current at <old>; nothing written`. Exit 0.
- `PackError`: `bw: pack export: <message>` on stderr. Exit 1.
- `OSError` from steps 7–8 (a disk or permission error): `bw: pack export: could not write <path>: <strerror>` on stderr. Exit 1.

- [ ] **Step 1: Write the failing tests**

A helper `pack_repo(tmp)` copies the fixture into `tmp`, runs `git init -q`, `git add pack.yaml`, and `git -c user.name=t -c user.email=t@example.invalid -c commit.gpgsign=false commit -qm init` with `subprocess.run(..., check=True)`. `commit_all(repo)` runs the same `add` and `commit`. `setUp` makes a temp dir, `repo = pack_repo(tmp/"esc")`, `state = tmp/"state"`, `cfg_ = make_config(top={"state_dir": str(state), "metrics_log": str(tmp/"m.jsonl")})`, and `data_ = load([...])` with one `alpha` panel under `model-a`.

```python
def test_export_writes_block_version_bullet_and_event(self):
    r = export.export(cfg_, data_, repo, state, today="2026-11-22")
    text = (Path(repo) / "pack.yaml").read_text()
    self.assertTrue(r["changed"]); self.assertIn("version: 0.2.0", text)
    self.assertTrue(Path(state, "packs", "model-seats-0.2.0.md").read_text().startswith("- `model-seats` 0.2.0"))
    self.assertEqual([e["type"] for e in store.load(cfg_["metrics_log"])["events"]][-1], "pack_export")

def test_dry_run_writes_nothing(self):
    before = (Path(repo) / "pack.yaml").read_bytes()
    r = export.export(cfg_, data_, repo, state, dry_run=True)
    self.assertTrue(r["diff"]); self.assertTrue(r["bullet"])
    self.assertEqual((Path(repo) / "pack.yaml").read_bytes(), before)
    self.assertFalse(Path(state, "packs").exists())
    self.assertFalse(Path(cfg_["metrics_log"]).exists())

def test_second_export_is_a_no_op(self):
    export.export(cfg_, data_, repo, state); commit_all(repo)
    r = export.export(cfg_, data_, repo, state)
    self.assertFalse(r["changed"]); self.assertIsNone(r["new_version"])

def test_wrong_name_refused(self): ...        # name: other-pack, committed → PackError mentioning "not model-seats"
def test_dirty_file_refused(self): ...        # append a line, no commit → PackError "uncommitted changes"
def test_untracked_file_refused(self): ...    # pack.yaml never added → PackError "not tracked by git"
def test_ignored_untracked_file_refused(self): ...  # .gitignore lists pack.yaml, never added → PackError "not tracked by git"
def test_git_timeout_reported(self): ...      # git_rc returns (124, "", "...") → PackError "git timed out"
def test_not_a_git_checkout_refused(self): ...  # plain temp dir → PackError "not a git checkout"
def test_bullet_write_failure_leaves_pack_unchanged(self): ...  # state/packs is a file, not a dir → OSError; pack.yaml bytes unchanged, no stray temp file in repo
def test_symlink_refused(self): ...           # pack.yaml is a symlink to a committed copy → PackError

def test_changed_during_export_refused(self):
    def git_rc(d, *a):
        (Path(d) / "pack.yaml").write_text("changed\n")  # changes the file after the read
        return 0, "", ""
    with self.assertRaises(export.PackError):
        export.export(cfg_, data_, repo, state, git_rc=git_rc)

def test_privacy_no_target_title_path_or_project_leaks(self):
    canary = "CANARY-7f3a"
    dispute = {"summary": f"{canary}-dsummary", "challenger": "alpha", "winner": "alpha",
               "proposals": {"alpha": f"{canary}-proposal"}, "reason": f"{canary}-dreason"}
    events = [panel("p1", "2026-11-01T00:00:00Z", {"alpha": "model-a"}, {"alpha": (1, 0, 0, 1)},
                    target=f"{canary}-target", notes=f"{canary}-note", disputes=[dispute],
                    absent={"beta": f"{canary}-absent"},
                    findings_detail=[{"seat": "alpha", "title": f"{canary}-title", "where": f"/{canary}/x.py:1",
                                      "claim": f"{canary}-claim", "evidence": f"{canary}-evidence",
                                      "ref": f"{canary}-ref", "notes": f"{canary}-fnote",
                                      "verdict": "confirmed", "severity": "major"}]),
              {"type": "watch", "id": "w1", "kind": "redundant-seat", "target": "alpha", "status": "open",
               "detail": f"{canary}-wdetail"},
              {"type": "session", "phase": "start", "session_id": "s1", "lead": "l1", "runtime": "claude-code",
               "model": "m-lead", "project": f"{canary}-project", "worktree": f"/{canary}", "ts": "2026-11-01T00:00:00Z"},
              {"type": "usage", "seat": "alpha", "model": "model-a", "dir": f"/{canary}/repo", "ok": True}]
    r = export.export(cfg_, load(events), repo, state, today="2026-11-22")
    for out in (r["diff"], r["bullet"], (Path(repo) / "pack.yaml").read_text(),
                Path(r["bullet_path"]).read_text(), Path(cfg_["metrics_log"]).read_text().splitlines()[-1]):
        self.assertNotIn(canary, out)
```

Each `...` test above is one `assertRaises(export.PackError)` (or `OSError` for the bullet case) with the named message part. The `setUp` of Task 2 (environment cleared, temp log) applies to this class too.

CLI tests, through `subprocess.run([sys.executable, BW, ...], env=env)`. `env` is a copy of `os.environ` without `BALANCEWHEEL_METRICS_LOG`, with `BALANCEWHEEL_CONFIG` and `BW_STATE_DIR` pointed into the temp dir:
- `test_cli_dry_run`: `bw pack export --to <repo> --dry-run` exits 0, prints `"bw: dry run; nothing written"`, and leaves the file unchanged.
- `test_cli_refusal`: a dirty `pack.yaml` gives exit 1 and `"bw: pack export:"` on stderr.
- `test_cli_success_says_paste_and_resign`: a real run exits 0, and stdout holds `"paste the bullet"` and `"re-sign the pack with esc"`.
- `test_cli_reads_every_log` (the `_panel_data` loading path): the config sets `improve.panel_logs` to a second temp log. The main log holds panel `p1` for `alpha` under `model-a`. The second log holds `p1` again (a duplicate id), `p2` for `alpha`, a `usage` event for `beta` under `model-b` dated `2026-10-07`, and an open `redundant-seat` watch for `alpha`. The written `pack.yaml` says `2 panels` for `alpha` (the duplicate counts once), gives `alpha` status `review-required` (the watch from the second log), and names `beta` with month `2026-10` (the usage event from the second log).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_pack_export -v`
Expected: the new tests FAIL (`AttributeError: ... 'export'`, and `bw` exits 2 with "invalid choice: 'pack'"); Tasks 2–3's tests still PASS.

- [ ] **Step 3: Implement `export(...)` in `packs/export.py`** with the steps above.

- [ ] **Step 4: Implement the CLI in `bin/bw`**

Add the docstring line `bw pack export --to <dir> [--dry-run]   regenerate the marked catalog block of esc's model-seats pack.yaml from the log (prints esc's changelog bullet)`. Add `from packs import export as pack_export  # noqa: E402` to the module-level imports. Add `"pack"` to `COMMANDS`. Add the `pack` parser with a required `export` subcommand, `--to` (required), and `--dry-run`. Add `cmd_pack` and the `"pack": cmd_pack` dispatch entry. Replace the panel-only merge in `_suggestions` (`bin/bw:311-317`) with `_panel_data(config)` (Interfaces block), and call it from both. Add `"pack"` after `"panel"` in `COMMANDS` (`:66-67`) (build ruling, 2026-11-23, as-built).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: all PASS, including `tests/test_improve.py` and `tests/test_bw_default.py` after the `_panel_data` move.

- [ ] **Step 6: Update the docs, the changelog, and the usage map**

- `CLAUDE.md` (the OpenEscapement paragraph): replace "never write the same file" with "write the same file in one place only: `bw pack export --to <dir>` rewrites the marked catalog block and the `version:` line of esc's `model-seats` `pack.yaml`, only when the user runs it". Replace "the planned `packs/` here is the export step that regenerates it from the log, not the pack itself" with "`packs/` here is that export step, not the pack itself". Add `packs/` to the code paragraph: "`packs/` holds `bw pack export` (`export.py`)".
- `docs/roadmap.md` "Planned shape" `packs/` bullet: replace "What `packs/` still owes is the export step: …" with "`packs/` is the export of one marked catalog block (`bw pack export`, landed 2026-11-22); the changelog bullet is printed for the user to paste, because esc owns its changelog". Edit only this bullet: Plan 5 Task 1 lands the 2026-11-22 roadmap amendment, and `docs/roadmap.md` has no dated preamble to update.
- `README.md`: add `bw pack export --to <dir> [--dry-run]` to the command list next to `bw improve`. In "Working with OpenEscapement", change "The planned `packs/` directory here is the export step that regenerates it" and "the export step that regenerates it from the log is what `packs/` still owes" to say `bw pack export` does it now, with markers the user adds once. Change "They never write the same file" to the one exception, in the same words as `CLAUDE.md`. Update the dated preamble if the README has one.
- `CHANGELOG.md` `[Unreleased]` `### Added`: "`bw pack export --to <dir> [--dry-run]` (`packs/export.py`): rewrites the `# bw:catalog:begin` / `# bw:catalog:end` block of esc's `model-seats` `pack.yaml` and its version (minor on a status, model, or membership change; patch on numbers only), refuses a wrong name, a dirty or untracked file, and missing markers, saves esc's changelog bullet to `state_dir/packs/`, and logs a `pack_export` event." Also under `### Changed`: "`CLAUDE.md`: balancewheel writes esc's `pack.yaml`, only through `bw pack export`."
- `CHANGELOG.md` also says, in the same bullet, that a `pack.yaml` git does not track (untracked or ignored) is refused.
- `docs/usage-map/generate.py`: add `"packs"` to `MODULE_DIRS`, and `"pack"` to the subcommand tuple at line 115 (`("guard", "triage", "sandbox", "improve", "setup", "seat", "panel")`; `MODULE_DIRS` at `:24` is `("lead", "lead/guard", "metrics", "cockpit", "seats", "panel")`) (build ruling, 2026-11-23, as-built), so the command is `cmd:pack.export`. Add a `titles` entry for `pack` if the generator has one per group.
- `docs/usage-map/infra.js` (`CLAUDE.md`: update `infra.js` in the same commit when a planned item ships):
  - the `esc` group's `desc`: replace "They never write the same file." with "They write one file in common: `bw pack export` rewrites the marked catalog block of the model-seats pack.yaml, only when the user runs it."
  - the `modelseats` node's `desc`: say the catalog block is regenerated by `bw pack export`; the rest stays hand-written.
  - the `packsexport` node: move it out of the `planned` group into the `esc` group, drop `status`/`plannedIn`, and give it `usage: 'bw pack export --to <dir> [--dry-run]'`.
  - the edges `metricslog → packsexport` (label `regenerates`) and `packsexport → modelseats`: drop `kind: 'planned'`. Drop the `dashboard → modelseats` "(by hand)" label wording.
  - the `toesc` journey: its `desc` loses "The export step is planned."; the `modelseats` step says the catalog block is generated; the `packsexport` step becomes "bw pack export rewrites the marked catalog block and the version, and prints the changelog bullet to paste." with `command: 'bw pack export --to <dir>'`; add a step on `escbin`: "Re-sign the pack with esc and commit it."

Run: `python3 docs/usage-map/generate.py`
Expected: no error; `git diff --stat docs/usage-map/data.generated.js` shows a change, and `grep -c "cmd:pack.export\|packs/export" docs/usage-map/data.generated.js` prints a number above 0.

Run: `grep -n "planned" docs/usage-map/infra.js | grep -i pack`
Expected: no output.

Run: `git diff | grep -n -i -E "/Users/"`
Expected: no output (privacy firewall).

Run: `python3 -m unittest discover -s tests -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add packs/export.py bin/bw tests/test_pack_export.py docs/usage-map/generate.py docs/usage-map/data.generated.js docs/usage-map/infra.js CHANGELOG.md CLAUDE.md docs/roadmap.md README.md
git commit -m "feat: bw pack export writes the model-seats catalog block from the log"
```
