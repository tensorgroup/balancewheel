# Usage map: Why · Who uses it · How it's built

Written 2026-11-23. Covers both usage maps: `balancewheel/docs/usage-map/` and
`openescapement/docs/usage-map/` (a copy of this design lives in each repo).

## Intent

Today each usage map is one zoomable architecture canvas. The persona journeys are an overlay
on that canvas. A reader must understand the architecture before a journey makes sense. That
mixes two questions in one view and confuses first-time readers.

The new page answers three questions, in this order:

1. **Why** should I care? (benefits, one or two sentences each)
2. **Who uses it**, and how does a day with it look? (persona storyboards in a fictional org)
3. **How is it built?** (the existing map, unchanged)

The reader is someone who must decide whether to adopt the tool, or must explain it to
others: an engineering lead, a platform team, a new developer. Success: a reader understands
in about two minutes what each person does, what each step stores or shares, and where the
source of truth is, without reading the architecture.

## Goals

- One page per project, with three tabs: **Why · Who uses it · How it's built**.
- A link to the project's GitHub repo in the top bar of every tab.
- **Why** tab: three or four benefit cards. Each card has a headline and one or two sentences.
- **Who uses it** tab: one fictional org, **Windup Workshop**, used in both projects. An org
  overview (teams, people, places) and one storyboard per persona.
- Each storyboard is visual. It shows the places involved as swimlanes. Labelled arrows show
  what moves between places. Markers show where data is **stored**, what is **shared**, and
  which place is the **source of truth**. Hover or click a step to see details.
- Each storyboard step can link to the matching box in the "How it's built" tab.
- Works from disk (`file://`), on GitHub Pages, in light and dark themes, and at phone width.

## Non-goals

- No change to the architecture map's layout or generated data, except for the hash change
  and the Journeys removal below.
- No build step, no framework, no new dependency. Vanilla JS, like today.
- No real people, companies, or private data. Windup Workshop is fully fictional.
- No shared package between the two repos. Each repo keeps its own copy of the engine.

## Anti-goals (a failure even if it works)

- A storyboard that needs the architecture to make sense.
- A "why" that lists features instead of benefits.
- Balancewheel content that names the author's private setup, paths, or employers (privacy
  firewall in `balancewheel/CLAUDE.md`).
- Story text that states something the code or roadmap does not support. Planned parts are
  marked as planned, as on the map today.

## Approaches considered

- **A. One page, a new `stories.js` renderer next to the current map (chosen).** A tab bar in
  `index.html`. `app.js` stays as it is, except for the hash change. A new `stories.js` draws
  the Why tab and the Who tab. Trade-off: one more JS file per repo.
- **B. Build the modes into `app.js`.** Same result, but the 47 KB canvas engine grows, and a
  linear storyboard fits a zoomable canvas badly. Rejected.
- **C. Hand-drawn SVG infographics.** Best polish per picture, but little interaction, and
  they go out of date when the code changes. Rejected.

We chose A on 2026-11-23.

## Decided

- Fictional org name: **Windup Workshop**, with a few teams. Used in both maps, so a reader
  who reads both sees the same people.
- The esc personas include: admin, team member, new team member, pack author, org observer
  (several teams). The Balancewheel personas include: developer who runs a panel, new
  developer setting up, the person who changes seat policy from the numbers, and the person
  who exports that policy to an esc pack (the link between the two projects).
- Why statements (our agreed wording, to be tightened, not replaced):
  - esc: keep track of which models to use and when, without being an expert who follows
    each model's strengths, weaknesses and costs.
  - esc: give a central team or person visibility into what their teams use, with possible
    enablement and possible governance.
  - esc: keep teams in sync, so the development environment is more consistent.
  - Balancewheel: use the same model information to run models together, in ways that use
    their strengths, weaknesses and costs.

## The design

### Page shell (`index.html`)

- Top bar: title, a tab bar (**Why · Who uses it · How it's built**), a GitHub link
  (`github.com/tensorgroup/<repo>`), search (How tab only), theme toggle.
- Default tab: **Why**. The tab is in the URL hash:
  - `#why`
  - `#who` (org overview), `#who/<storyId>` (one storyboard), `#who/<storyId>/<step>`
  - `#how` or `#how/<nodeId>` (the map, focused on a node)
- Compatibility: a bare `#<nodeId>` (today's links) still opens the How tab on that node.
- **Stories replace the Journeys (decided 2026-11-23).** Remove the "▶ Journeys" button, its
  menu, the flow bar, the "Walk through a journey" list in the panel, and `flows` in
  `infra.js`. Move each journey's facts into a story first, so nothing is lost. The persona
  panel on the map links to that persona's stories instead.
- The other change to `app.js`: its hash handler reads `#how/<id>` and bare `#<id>`, and it
  ignores `#why` and `#who…`. It starts its layout only when the How tab is first shown (or
  it lays out while hidden, if that is simpler and works — builder's call).

### Data (`stories-data.js`, hand-written, a new file)

The new sections live in their own file, `docs/usage-map/stories-data.js`. It holds one line
of JS around strict JSON: `window.USAGE_MAP_STORIES = { …strict JSON… };`. Reason: the Python
(standard library only) and Go generators can then strip the wrapper and parse it with their
JSON libraries, with no JS parser. `infra.js` keeps the map data. (Builder's call, from the
builder check.)

```js
why: {
  lead: 'One sentence: what the project is.',
  cards: [{ id, title, text, icon, stories: ['storyId'] }],   // 3–4 cards
},
org: {
  name: 'Windup Workshop',
  blurb: 'One sentence about the fictional org.',
  teams:  [{ id, name, about }],
  people: [{ id, name, team, role, persona /* map persona id */, story /* story id */ }],
},
places: [            // the swimlanes; shared by all stories in this map
  { id, title, kind: 'machine' | 'repo' | 'home' | 'service' | 'agent',
    owner /* team id or person id */, truthFor: ['what this place is the source of truth for'],
    node /* map node id, optional */ },
],
stories: [
  { id, person /* org.people id */, title, why /* one sentence */, status /* 'planned' optional */,
    steps: [
      { place,                       // lane of the main action
        text,                        // one or two sentences
        command,                     // optional
        node,                        // optional map node id for "Show in architecture"
        moves: [{ from, to, what, kind: 'share' | 'store' | 'read' | 'sign' }],
        truth,                       // optional: place id that is the source of truth here
        status },                    // optional 'planned'
    ] },
],
```

### Why tab

- A lead sentence, then 3–4 cards in a responsive grid. Each card: icon, headline,
  one or two sentences, and "See it in a story →" links to the stories that show it.
- Under the cards: a small "Where the truth lives" strip. It lists each place with
  `truthFor` entries, so the reader sees at a glance what is canonical and what is derived.
- Balancewheel's Why tab and esc's Why tab each end with one card that links to the sibling
  project's map and says how the two connect.

### Who uses it tab

- **Org overview:** the Windup Workshop teams as columns. Each person is a card with name,
  role and a one-line goal. The places each team owns sit under the team. Click a person to
  open their storyboard.
- **Storyboard:** an inline SVG. Rows are the places used in the story (only those). Columns
  are steps. Each step is a card in the row of its main place. Each `move` is an arrow from
  one row to another in that column, labelled with `what`. Arrow style by `kind`: share,
  store, read, sign. A ★ marks the source-of-truth place for the step, and a 🗄-style
  icon marks a store. Planned steps are dashed, as on the map.
- **Step detail:** hover shows a tooltip. Click (or ←/→ keys, or Prev/Next buttons) selects a
  step and fills a side panel: text, command, moves as a list ("Shared: … from … to …"),
  source of truth, and **Show in architecture →** (goes to `#how/<node>`).
- **Narrow screens:** below about 700 px, the storyboard turns into a vertical list of steps.
  Each step shows its moves as "Place A → Place B: what". The SVG scrolls sideways inside its
  own container if shown.
- A persona switcher (chips) at the top of the tab moves between stories.

### Theme, look, accessibility

- Reuse the existing CSS tokens and dark-mode rules in `index.html`. Add only what the new tabs
  need.
- Tabs are real buttons with `aria-selected`. Story steps are focusable. Colour is never the
  only signal: arrow kinds also differ by dash pattern and label.

### Content: Windup Workshop

The builder drafts the content, and we review it. Starting point (builder may adjust names
and teams; keep the cast small and the same in both maps):

- Teams: **Platform** (owns the rule pack and the portal), **Payments**, **Mobile**, and
  **Leadership** (an org-wide view).
- People:
  - Mira, Platform, admin: runs the portal, publishes pack changes.
  - Raj, Platform, pack author: edits rules, skills and model guidance; signs a release.
  - Theo, Payments, team member: works in a governed repo; runs a Balancewheel panel.
  - Ana, Payments, new team member: first day; clones a repo and is in policy at once.
  - Kai, Mobile, team member: a second team, to show "teams stay in sync".
  - Lena, Leadership, org observer: wants to know what all teams use and what it costs.
- esc stories (at least): admin publishes a change; pack author releases a pack; team member
  syncs and sees drift in CI; new team member's first day; org observer reads the fleet
  (current parts real, planned parts dashed); model guidance changes and reaches every team.
- Balancewheel stories (at least): developer runs a panel at a checkpoint; new developer sets
  up a machine; a plan runs out and the next lead takes over; someone changes seat policy from
  the scoreboard; someone exports seat policy to an esc pack, which Raj then releases.
- **Balancewheel is per user.** Each developer's metrics log, config and scoreboard stay under
  that developer's home directory. Nothing in Balancewheel shares them with other people. The
  seat-policy story shows one person who reads their *own* scoreboard. The only path from one
  person's results to the whole org is the export to an esc pack, which Raj then releases.
  The stories and the "Where the truth lives" strip must not suggest a shared or team log.
  (Answered from the code and the roadmap.)
- **Balancewheel why cards (drafted by the builder for review):** the agreed statement
  (use models together by strengths, weaknesses and costs), plus cards drawn from the README's
  core ideas: blind second opinions from other model families, work continues when a plan
  runs out, and reviewer choice backed by logged numbers. The last card links to esc.
- Each story's facts come from that repo's code, README and docs. The existing `flows` in
  `infra.js` are the best source; each new story should name the map nodes its steps touch.

### Checks

- The generator (Balancewheel `generate.py`; esc `docs/usage-map/gen`) reads
  `stories-data.js` and `data.generated.js` and validates the hand-written stories when it
  runs: each step's `place` and each move's `from`/`to` is in
  `places`; each `node` exists on the map (generated ids from `data.generated.js`,
  hand-written ids found by a simple `id: '<x>'` scan of `infra.js`); each `person` exists in `org.people`; each
  `why.cards[].stories` entry exists. It fails on a bad reference. If that is awkward in a
  generator that does not parse `infra.js`, a small separate check script is fine.
- Balancewheel: add a unittest for that check, as for other code here.
- A browser check: each tab renders with no console errors, in both themes, at 1280 px and
  400 px. Update `preview.png`.

### Docs to update in the same change

- Each README's usage-map paragraph: describe the three tabs.
- Balancewheel `CLAUDE.md`: the usage-map entry names the new sections in `infra.js` and says
  to update stories when a journey changes. (Same for esc's equivalent instructions, if any.)
- Balancewheel `CHANGELOG.md` is not affected (no contract moves).

## Left to the builder

- Exact visual style of the storyboard (card size, arrow routing, icons), within the existing
  look.
- Whether `app.js` lays out lazily or while hidden.
- The final wording of story text and why cards, in STE style, for review.
- The order of the work: build in Balancewheel first, then copy `stories.js` and the shell
  to esc and write esc's content.

## Changes made while building (2026-11-23)

The plan panel and the build changed these details. The plan's "Panel revisions" section has
the full list.

- `org.people[].persona` became `personas: []`; a person can stand for several map personas.
  People who appear only in the sibling map carry `elsewhere: {label, href}`.
- Each step has a short `label` (shown on its card) as well as its `text`.
- `why.cards[]` may carry `href` and `linkLabel` (the sibling-project card); `why.truthNote`
  is the line under "Where the truth lives".
- `stories.js` owns the URL hash. The map writes `#how/<id>` only while its tab is shown, and
  waits for `show()` while hidden. The map's keys work only on its own tab.
- The step panel sits under the storyboard, not beside it, so long stories fit.
- The check also rejects an unknown status or place kind, a story with no steps, a story id
  that is not lowercase letters, digits and dashes, and a step on a planned map box that is
  not itself planned.
- esc: `.escapement/config.yaml` is the source of truth for the requested pack version;
  `escapement.lock` records what it resolved to.
