# Usage map: Why · Who uses it · How it's built — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn each usage map into one page with three tabs (Why, Who uses it, How it's built),
with Windup Workshop persona storyboards that show what moves, where it is stored, and where
the source of truth is.

**Architecture:** A new vanilla-JS renderer `stories.js` draws the Why and Who tabs from a new
strict-JSON data file `stories-data.js`. `index.html` gets a tab bar and a GitHub link. `app.js`
keeps the map; it loses the Journeys overlay and learns `#how/<id>` hashes. Each generator
validates the stories. Balancewheel first, then the same renderer is copied to OpenEscapement.

**Tech Stack:** HTML, CSS, vanilla JS (no build, no dependency); Python 3.9 stdlib + unittest
(Balancewheel); Go stdlib (OpenEscapement gen).

**Spec:** `docs/superpowers/specs/2026-11-23-usage-map-why-who-how-design.md`

## Global Constraints

- Page works from `file://` and GitHub Pages; no fetch, no external script or font.
- Light and dark themes via the existing tokens in `index.html`; phone width (~400 px) works.
- Repo links: `https://github.com/tensorgroup/balancewheel`, `https://github.com/tensorgroup/openescapement`.
- Fictional org **Windup Workshop**; same cast in both maps: Mira (Platform, admin), Raj
  (Platform, pack author), Theo (Payments, team member, runs Balancewheel), Ana (Payments, new
  team member), Kai (Mobile, team member), Lena (Leadership, org observer).
- Balancewheel is per user: no story may suggest a shared or team metrics log.
- Planned parts carry `status: 'planned'` and render dashed.
- Prose in ASD-STE100 style: short sentences, active voice, one fact per sentence.
- Balancewheel privacy firewall: no personal paths, names of the author's private setup, or
  employers.
- Commit prefixes: Balancewheel `docs:`/`test:`/`feat:` lowercase; esc follows its log
  (`docs(usage-map): …`).

## Review Focus

1. Old links `#<nodeId>` (README, esc cross-links) must still open the How tab on that node.
2. Opening `#how` first, then switching tabs, must not leave the map mis-sized (layout while
   hidden yields zero width).
3. A story with a step whose `node` is missing, or a move to an unknown place, must fail the
   generator, not render a broken arrow.
4. A storyboard with 8+ steps and 5+ lanes must stay readable at 1280 px and fall back to the
   list at 400 px.
5. Keyboard only: tabs, story chips and steps reachable with Tab; ←/→ move steps; Esc does not
   break the map's own Esc handling.

---

## File structure (per repo, under `docs/usage-map/`)

- `stories-data.js` — new, hand-written. `window.USAGE_MAP_STORIES = {…strict JSON…};`
- `stories.js` — new. Tab router, Why tab, Who tab (org overview + storyboard + step panel).
  Identical in both repos.
- `index.html` — tab bar, GitHub link, `#why` and `#who` sections, CSS for them, script tag.
- `app.js` — remove Journeys; hash handling for `#how/<id>`; expose `window.USAGE_MAP.select(id)`
  and `window.USAGE_MAP.refit()` for `stories.js`. Identical in both repos after the change.
- `infra.js` — drop `flows`; add `repoUrl`.
- Balancewheel: `check_stories.py` (new), `generate.py` (calls it), `tests/test_usage_map_stories.py`.
- esc: `gen/stories.go` + `gen/stories_test.go` (new), `gen/main.go` (calls it).

### Data shape (`stories-data.js`)

```json
{ "why":   { "lead": "", "cards": [{ "id": "", "title": "", "text": "", "icon": "", "stories": [] }] },
  "org":   { "name": "Windup Workshop", "blurb": "", "teams": [{ "id": "", "name": "", "about": "" }],
             "people": [{ "id": "", "name": "", "team": "", "role": "", "goal": "", "persona": "", "stories": [] }] },
  "places":[{ "id": "", "title": "", "kind": "machine|repo|home|service|agent", "owner": "",
             "truthFor": [], "node": "", "status": "" }],
  "stories":[{ "id": "", "person": "", "title": "", "why": "", "status": "",
             "steps": [{ "place": "", "text": "", "command": "", "node": "", "truth": "", "status": "",
                         "moves": [{ "from": "", "to": "", "what": "", "kind": "share|store|read|sign" }] }] }] }
```
Empty-string fields are optional and may be omitted. `owner` is a team id or person id.

---

### Task 1: Story check (Balancewheel)

**Files:**
- Create: `docs/usage-map/check_stories.py`
- Modify: `docs/usage-map/generate.py` (`main()`)
- Test: `tests/test_usage_map_stories.py`

**Interfaces:**
- Produces: `load_js_json(text: str, var: str) -> dict` (strips `window.<var> = ` and the
  trailing `;`, ignores leading `//` comment lines); `map_ids(generated: dict, infra_text: str)
  -> set[str]` (every `"id"` value anywhere in `generated`, plus every `id: '<x>'` in
  `infra_text`, plus `root`); `check(stories: dict, ids: set[str]) -> list[str]` (error
  messages, empty when valid).

- [ ] **Step 1: Write failing tests** in `tests/test_usage_map_stories.py`, loading the module
  by path (`importlib.util.spec_from_file_location`). Synthetic data only.
  - `test_load_js_json_strips_wrapper`: `"// c\nwindow.X = {\"a\": 1};\n"` → `{"a": 1}`.
  - `test_map_ids_collects_generated_and_infra`: generated `{"t": {"groups": [{"id": "g", "items": [{"id": "cmd:lead"}]}]}}`, infra `"{ id: 'you', x: 1 }"` → contains `g`, `cmd:lead`, `you`, `root`.
  - `test_valid_stories_pass`: minimal valid dict → `[]`.
  - `test_unknown_place`, `test_unknown_move_end`, `test_unknown_node`, `test_unknown_person`,
    `test_unknown_card_story`, `test_unknown_truth`, `test_person_team_unknown`: each returns one
    error that names the story id (or card id) and the bad value.
  - `test_real_data_is_valid`: load the repo's `stories-data.js`, `data.generated.js`,
    `infra.js` → `check(...) == []` (skip with `unittest.skipUnless` until the file exists).
- [ ] **Step 2:** `python3 -m unittest tests.test_usage_map_stories -v` → FAIL (module missing).
- [ ] **Step 3: Implement** the three functions. Also `main() -> int` that loads the three files
  next to it, prints each error, returns 1 on errors. In `generate.py` `main()`, after writing
  `OUT`, run the check when `stories-data.js` exists and `sys.exit(1)` on errors.
- [ ] **Step 4:** tests PASS; full suite `python3 -m unittest discover -s tests` passes.
- [ ] **Step 5: Commit** `test: the usage map checks its stories against the map`.

### Task 2: Tab shell and map changes (Balancewheel)

**Files:** Modify `docs/usage-map/index.html`, `docs/usage-map/app.js`, `docs/usage-map/infra.js`.
Create `docs/usage-map/stories.js` (router only in this task).

**Interfaces:**
- Produces in `app.js`: `window.USAGE_MAP = { select(id: string): void, refit(): void, has(id): boolean }`.
- Produces in `stories.js`: `route(hash: string) -> {tab: 'why'|'who'|'how', story?: string, step?: number, node?: string}`
  (pure, exported on `window.USAGE_MAP_ROUTE` for a console check). Rules: `''`→why; `why`→why;
  `who`→who; `who/<s>`→who+story; `who/<s>/<n>`→+step (1-based); `how`→how; `how/<id>`→how+node;
  any other `<id>`→how+node (old links).
- Consumes: `INFRA.repoUrl`.

- [ ] **Step 1:** In `index.html` add a `<nav class="tabs" role="tablist">` with three buttons
  (`data-tab="why|who|how"`, `aria-selected`), and an `<a class="repo" href=INFRA.repoUrl>`
  "GitHub ↗" in the top bar. Wrap the current `#stats` + `main.stage` in
  `<section id="tab-how" hidden>`; add empty `<section id="tab-why">` and `<section id="tab-who">`.
  Search and theme stay in the top bar; search shows only on How.
- [ ] **Step 2:** In `app.js`: remove the Journeys button, menu, flow bar, `startFlow`,
  `clearFlow`, `renderFlowBar`, `flowsFor`, the "Walk through a journey" and "Their journeys"
  panel sections, and the `flowbar` element and its CSS. Persona panel instead lists
  `window.USAGE_MAP_STORIES.people` whose `persona` equals the node id, each as a link to
  `#who/<story>`. Hash handler: strip a leading `how/`; ignore `why` and `who…`. Expose
  `window.USAGE_MAP`. `refit()` re-measures the SVG and calls the existing fit.
- [ ] **Step 3:** In `infra.js` remove `flows` (keep a copy in your scratchpad: Task 3 turns each
  journey into a story) and add `repoUrl: 'https://github.com/tensorgroup/balancewheel'`.
- [ ] **Step 4:** `stories.js`: `route()`, tab switching on `hashchange` and tab clicks (sets the
  hash), `USAGE_MAP.refit()` the first time How is shown, `select(node)` for `how/<id>`.
- [ ] **Step 5: Verify** with headless Chrome (see "Browser check" below) for `#how`,
  `#how/you`, `#you`, `#why`: no console errors, map visible and fitted on `#how` after load
  on `#why`. Unit tests still pass.
- [ ] **Step 6: Commit** `docs: the usage map gets why, who and how tabs`.

### Task 3: Why and Who renderers + Balancewheel content

**Files:** Modify `docs/usage-map/stories.js`, `docs/usage-map/index.html` (CSS, script tag).
Create `docs/usage-map/stories-data.js`.

**Interfaces:** Consumes `window.USAGE_MAP_STORIES` (shape above), `window.USAGE_MAP`,
`route()`. Produces `renderWhy(root: HTMLElement)`, `renderWho(root, storyId?, step?)`.

- [ ] **Step 1: Content.** Write `stories-data.js`:
  - `why`: 4 cards — use models together by strengths, weaknesses and costs (the agreed line);
    blind second opinions from other model families; work continues when a plan runs out;
    reviewer choice from logged numbers. Plus a 5th "Works with OpenEscapement" card linking to
    `https://tensorgroup.github.io/openescapement/usage-map/#who`. One or two sentences each.
  - `org`: the six people and four teams from Global Constraints. Only Theo, Ana (setting up
    her machine) and Raj (receives the export) have Balancewheel stories; others appear in the
    overview greyed with "Uses OpenEscapement →".
  - `places`: Theo's machine (home dir: config, metrics log, state — `truthFor`: seat results,
    seat policy evidence), Payments repo (worktree), vendor plans (service), seat CLIs (agent),
    esc pack repo (repo, owner Platform, `truthFor`: org policy).
  - `stories` (each step `node` = an existing map id; each journey removed in Task 2 maps to
    a story so no fact is lost): Ana sets up a machine; Theo starts the day; Theo's plan runs
    out; Theo's lead needs a decision; Theo runs a panel at a checkpoint; Theo changes seat
    policy from his scoreboard; Theo audits the guard (if it was a journey); Theo exports seat
    policy to an esc pack and Raj releases it.
- [ ] **Step 2: Why tab.** Lead sentence, card grid (`grid-template-columns:
  repeat(auto-fit, minmax(240px, 1fr))`), "See it in a story →" links, then the "Where the
  truth lives" strip from `places[].truthFor`.
- [ ] **Step 3: Who overview.** Team columns, person cards (name, role, goal, story links);
  places each team owns listed under the team.
- [ ] **Step 4: Storyboard.** Persona chips on top. Inline SVG inside an `overflow-x:auto`
  wrapper: one row per place used in the story (in first-use order), one column per step
  (column width 180 px, row height 88 px). Step card in its `place` row. Each move: a vertical
  arrow in that column from row `from` to row `to`, labelled `what`; dash by kind (share solid,
  store `6 3`, read `2 3`, sign double stroke) plus a text kind label. ★ on the `truth` row;
  store icon on a store move's target. Planned steps dashed. Below 700 px render a `<ol>`
  instead: step text, then "From → To: what (kind)" lines.
- [ ] **Step 5: Step panel.** Hover → tooltip with text. Click / Enter / ←/→ / Prev-Next →
  select step, set hash `#who/<s>/<n>`, panel shows text, command (`<pre>`), moves list, source
  of truth, and "Show in architecture →" (`#how/<node>`) when `node` is set.
- [ ] **Step 6:** `python3 docs/usage-map/generate.py` → exit 0; `test_real_data_is_valid`
  passes. Browser check for `#why`, `#who`, every `#who/<story>`, one `#who/<s>/3`, both
  themes, 1280 and 400 px; look at the screenshots.
- [ ] **Step 7: Commit** `docs: windup workshop stories show who uses balancewheel and how`.

### Task 4: Balancewheel docs and preview

**Files:** `README.md` (usage-map paragraph), `CLAUDE.md` (usage-map entry: name
`stories-data.js`; update a story in the same commit as a change to a journey it tells; the
generator checks the stories), `docs/usage-map/preview.png` (screenshot of `#who/<panel story>`
at 1600×1000), `docs/usage-map/data.generated.js` (regenerate).

- [ ] **Step 1:** Edit the docs; regenerate; screenshot.
- [ ] **Step 2:** Full unit suite passes; generator exits 0.
- [ ] **Step 3: Commit** `docs: describe the usage map's three tabs`.

### Task 5: OpenEscapement port

**Files (esc worktree):** copy `stories.js` and `app.js` from Balancewheel (diff the esc
`app.js` first: it is identical today, so the copy is safe; if not, port the changes by hand);
port the `index.html` changes (keep esc's own title/tagline/CSS differences); `infra.js` (drop
`flows`, add `repoUrl: 'https://github.com/tensorgroup/openescapement'`); create
`gen/stories.go`, `gen/stories_test.go`, `stories-data.js`; call the check from `gen/main.go`
after writing the data (`fail` on errors).

**Interfaces:** Go mirror of Task 1: `loadJSJSON(text, varName string) (map[string]any, error)`,
`mapIDs(generated map[string]any, infra string) map[string]bool`,
`checkStories(s map[string]any, ids map[string]bool) []string`. Same test cases as Task 1, as
`TestLoadJSJSON`, `TestMapIDs`, `TestCheckStories*` table test, `TestRealDataValid`.

- [ ] **Step 1:** Tests first (`go test ./docs/usage-map/gen/ -run Stories` → FAIL), then
  implement, then PASS.
- [ ] **Step 2: Content.** `why`: model choice without being an expert who follows every
  model's strengths, weaknesses and costs; visibility for a central team, with optional
  enablement and governance; teams stay in sync so the development environment is consistent;
  plus "Works with Balancewheel" card linking to
  `https://tensorgroup.github.io/balancewheel/usage-map/#who`. Same org. Places: pack repo
  (`truthFor`: org policy, model guidance; owner Platform), Payments repo and Mobile repo
  (`truthFor`: the pinned pack version — `escapement.lock`), developer machines, CI, portal
  (derived view; planned parts dashed). Stories: Raj writes and releases a pack; Mira publishes
  a change from the portal; Theo syncs and CI catches drift; Ana's first day (clone, init/sync,
  agent reads rules); Kai's team moves to the new version, so both teams match; Raj curates
  model guidance and it reaches every team; Lena reads the fleet (current) and audit views
  (planned); Theo ships seat policy from Balancewheel. Each removed journey maps to a story.
- [ ] **Step 3:** `go run ./docs/usage-map/gen` exits 0; `go test ./...` passes; browser check
  as in Task 3; update `preview.png`; README usage-map paragraph; `AGENTS.md` usage-map entry.
- [ ] **Step 4: Commit** `docs(usage-map): why, who and how tabs with windup workshop stories`.

## Browser check (used by Tasks 2, 3, 5)

```bash
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
P="file://$PWD/docs/usage-map/index.html"
"$CHROME" --headless=new --disable-gpu --window-size=1280,900 --virtual-time-budget=3000 \
  --enable-logging=stderr --v=0 --screenshot="$SCRATCH/why.png" "$P#why" 2>&1 | grep -i "console\|uncaught" || true
```
Pass: no `Uncaught` lines; the screenshot shows the expected tab. Repeat per hash and with
`--window-size=400,860`. Dark theme: `--force-dark-mode` plus `--blink-settings=preferredColorScheme=0`.

---

## Panel revisions (2026-11-23, verified against the code; these override the tasks above)

1. **One hash owner.** `stories.js` owns startup routing and `hashchange`. `app.js` must not
   route at start or on `hashchange`. `app.js` `select()` writes the hash only when the How
   tab is shown, and writes `#how` (root) or `#how/<encodeURIComponent(id)>`. `route()` takes
   the whole decoded remainder after `how/` (ids contain `/`, `:` and spaces); `who/<s>/<n>`
   splits on the first two `/` only. Unknown story → `#who`; step out of range → clamp.
2. **Layout chain.** `#tab-how { flex:1; min-height:0; display:flex; flex-direction:column }`;
   `#tab-why, #tab-who { flex:1; min-height:0; overflow:auto }`.
3. **Hidden map.** `applyCam()` and the resize handler no-op while `svg.clientWidth === 0`.
   `USAGE_MAP.show()` runs on *every* switch to How: `stopAnim()`, `cam.upp = fitUpp()`,
   `applyCam()`, then fly to the selected node (or the node in the hash).
4. **Keys.** The `app.js` `keydown` handler returns unless the How tab is shown.
5. **Script order.** `data.generated.js`, `infra.js`, `stories-data.js`, `app.js`, `stories.js`.
   `app.js` reads `(window.USAGE_MAP_STORIES?.org?.people) || []`; `stories.js` guards a
   missing `window.USAGE_MAP` (no generated data).
6. **Persona links.** A person may list several map personas: `persona` becomes `personas: []`.
   The map persona panel lists every story whose person names that persona, and every story
   with a step on that box ("In stories (n)") for non-persona boxes.
7. **Flows go last.** `flows` is deleted in Task 3, after the stories exist. Every journey
   maps to a story: Balancewheel `install, daily, cap, ask, panel, scoreboard, guardaudit,
   improve, toesc`; esc `install, onboard, sync, daily, ci, update, author, portal, telemetry,
   models, balancewheel, future`. `infra.js` `howTo`, tagline and summary lose journey wording.
8. **Planned stays planned.** The export to an esc pack is planned (`packsexport`); today the
   model-seats pack is written by hand. The check enforces this (done in Task 1).
9. **Remove every flow reference** in `app.js`: `layers.flow`, `clearFlow` calls (485, 634,
   879, 945, 957, 1004), `flow` reads (487, 957, 963–964), `goStep`, `togglePlay`,
   `flowButton`, `flowsFor`, the flow menu, `#flowbar`, `#arrow-flow`, `.flow*`/`.fb-*` CSS.
10. **Schema additions:** `why.cards[].href` + `linkLabel` (cross-project card);
    `org.people[].elsewhere: {label, href}` for people who appear only in the sibling map.
    `stories.js` holds no repo-specific text.
11. **Storyboard layout:** each column is a card track plus an arrow gutter; moves stack in
    the gutter; lane label column 140 px; card 170×64; row 110; colours from tokens mixed
    with `--ink` (dark-safe). List view below 760 px (the page's breakpoint), re-rendered on
    `matchMedia` change.
12. **ARIA:** `role=tab/tabpanel`, `aria-controls`, `aria-labelledby`; step `<g>` gets
    `tabindex=0`, `role=button`, `aria-label`; ←/→ on the storyboard only; focus moves to the
    new panel on tab switch. Top bar wraps tabs to a second row below 760 px.
13. **Browser check:** a small Node+CDP-free check is not available, so use headless Chrome
    `--dump-dom` plus screenshots, and a forced-error run to confirm console capture; test
    the #why → #how switch by loading `#how` after a scripted tab click (`?selftest`).
14. **esc truth:** `.escapement/config.yaml` is the source of truth for the requested pack
    ref; `escapement.lock` records what it resolved to (commit, hashes).
15. **Go tests:** name all story tests `TestStories*` so `-run Stories` fails before code.
16. **Docs:** also `AGENTS.md` (both repos) and esc `index.html` header comment.
