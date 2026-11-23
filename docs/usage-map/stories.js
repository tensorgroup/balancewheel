// stories.js — the "Why" and "Who uses it" tabs, and the tab router for the whole page.
// Input: window.USAGE_MAP_STORIES (stories-data.js, strict JSON checked by the generator),
// window.USAGE_MAP_INFRA (for the repo link), and window.USAGE_MAP (app.js; absent when the
// generated data is missing). No libraries, no network; the same file serves every repo that
// carries this map, so it holds no repo-specific text.
//
// URL hash (this file is its only reader):
//   #why · #who · #who/<story> · #who/<story>/<step 1-based> · #how · #how/<map id>
//   a bare #<map id> (old links) opens the map on that box.
(() => {
  'use strict';
  const S = window.USAGE_MAP_STORIES || { why: { cards: [] }, org: { teams: [], people: [] }, places: [], stories: [] };
  const INFRA = window.USAGE_MAP_INFRA || {};
  const MAP = () => window.USAGE_MAP;
  const TABS = ['why', 'who', 'how'];
  const NARROW = window.matchMedia('(max-width: 760px)');
  const KIND = {
    share: { verb: 'shares', label: 'Shared' },
    store: { verb: 'stores', label: 'Stored' },
    read: { verb: 'reads', label: 'Read' },
    sign: { verb: 'signs', label: 'Signed' },
  };
  const PLACE_ICON = { machine: '💻', repo: '📁', home: '🏠', service: '☁️', agent: '🤖' };

  // One polite live region for the whole page: a region added with its text already in it is
  // often not announced, so story steps announce through this one.
  const live = document.createElement('div');
  live.className = 'sr-only';
  live.setAttribute('aria-live', 'polite');
  document.body.append(live);
  function replaceHash(hash) {
    try {
      history.replaceState(null, '', hash);
    } catch {
      /* file:// in some browsers */
    }
  }

  const people = new Map(S.org.people.map((p) => [p.id, p]));
  const teams = new Map(S.org.teams.map((t) => [t.id, t]));
  const places = new Map(S.places.map((p) => [p.id, p]));
  const stories = new Map(S.stories.map((s) => [s.id, s]));

  // ── helpers ────────────────────────────────────────────────────────────────
  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === 'class') n.className = v;
      else if (k.startsWith('on')) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? '' : v);
    }
    for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== '') n.append(kid);
    return n;
  }
  const NS = 'http://www.w3.org/2000/svg';
  function svgEl(tag, attrs, parent) {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v !== undefined && v !== null) n.setAttribute(k, v);
    if (parent) parent.append(n);
    return n;
  }
  const ownerName = (id) => (teams.get(id) || people.get(id) || {}).name || '';
  const placeTitle = (id) => (places.get(id) || { title: id }).title;
  const storyHref = (s, step) => `#who/${s}${step ? `/${step}` : ''}`;
  const planned = (x) => x && x.status === 'planned';
  function personOf(story) {
    return people.get(story.person) || { name: '?' };
  }
  function nodeLink(node, label) {
    const map = MAP();
    if (!node) return null;
    if (!map || !map.has(node)) {
      console.warn('usage map: story points at a box the map does not draw', node);
      return null;
    }
    return el('a', { class: 'btn small', href: `#how/${encodeURIComponent(node)}` }, label || 'Show in architecture →');
  }

  // ── router ─────────────────────────────────────────────────────────────────
  /** Pure: hash (without '#') → { tab, story?, step?, node? }. */
  function route(hash) {
    let h = hash || '';
    try {
      h = decodeURIComponent(h);
    } catch {
      /* keep the raw hash */
    }
    if (!h || h === 'why') return { tab: 'why' };
    if (h === 'who') return { tab: 'who' };
    if (h.startsWith('who/')) {
      const rest = h.slice(4);
      const cut = rest.indexOf('/');
      const story = cut < 0 ? rest : rest.slice(0, cut);
      const step = cut < 0 ? 0 : parseInt(rest.slice(cut + 1), 10) || 0;
      return { tab: 'who', story, step };
    }
    if (h === 'how') return { tab: 'how' };
    if (h.startsWith('how/')) return { tab: 'how', node: h.slice(4) };
    // Anything else is an old link to a map box (#cli, #p-author): open the map on it.
    return { tab: 'how', node: h };
  }
  window.USAGE_MAP_ROUTE = route;

  const panels = Object.fromEntries(TABS.map((t) => [t, document.getElementById(`tab-${t}`)]));
  const tabBtns = Object.fromEntries(TABS.map((t) => [t, document.getElementById(`tabbtn-${t}`)]));
  const search = document.querySelector('.search-wrap');
  let current = null;

  function apply(r, { focus = false } = {}) {
    const prev = current;
    const tabChanged = !prev || prev.tab !== r.tab;
    for (const t of TABS) {
      panels[t].hidden = t !== r.tab;
      tabBtns[t].setAttribute('aria-selected', String(t === r.tab));
      tabBtns[t].tabIndex = t === r.tab ? 0 : -1;
    }
    if (search) search.hidden = r.tab !== 'how';
    document.body.dataset.tab = r.tab;
    current = r;
    if (r.tab === 'why') renderWhy();
    else if (r.tab === 'who') renderWho(r, prev, focus);
    else if (MAP()) {
      // The map fits itself again only when its tab appears; within the tab it just flies.
      if (tabChanged) MAP().show(r.node || null);
      else if (r.node) MAP().select(r.node);
    }
    if (focus && tabChanged && r.tab !== 'who') panels[r.tab].focus({ preventScroll: true });
  }
  function go(hash) {
    const h = hash.replace(/^#/, '');
    if (location.hash.slice(1) === h) apply(route(h), { focus: true });
    else location.hash = h;
  }
  window.addEventListener('hashchange', () => apply(route(location.hash.slice(1)), { focus: true }));
  for (const t of TABS) {
    tabBtns[t].addEventListener('click', () => go(t === 'how' && current && current.tab === 'how' ? location.hash.slice(1) : t));
    tabBtns[t].addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
      const next = TABS[(TABS.indexOf(t) + (e.key === 'ArrowRight' ? 1 : TABS.length - 1)) % TABS.length];
      tabBtns[next].focus();
      go(next);
    });
  }
  const repo = document.getElementById('repo-link');
  if (repo && INFRA.repoUrl) repo.href = INFRA.repoUrl;
  else if (repo) repo.hidden = true;

  // ── Why ────────────────────────────────────────────────────────────────────
  function renderWhy() {
    const root = panels.why;
    if (root.dataset.done) return;
    root.dataset.done = '1';
    const wrap = el('div', { class: 'page' });
    wrap.append(el('p', { class: 'why-lead' }, S.why.lead || ''));
    const cards = el('div', { class: 'why-cards' });
    for (const c of S.why.cards) {
      const links = (c.stories || []).map((sid) => {
        const st = stories.get(sid);
        return st ? el('li', {}, el('a', { href: storyHref(sid) }, `${personOf(st).name}: ${st.title} →`)) : null;
      });
      cards.append(el('article', { class: 'why-card' },
        el('div', { class: 'why-icon', 'aria-hidden': 'true' }, c.icon || '•'),
        el('h2', {}, c.title),
        el('p', {}, c.text),
        links.length ? el('ul', { class: 'why-links' }, links) : null,
        c.href ? el('p', { class: 'why-out' }, el('a', { href: c.href, target: '_blank', rel: 'noopener' }, c.linkLabel || 'Open →')) : null));
    }
    wrap.append(cards);

    const truth = S.places.filter((p) => (p.truthFor || []).length);
    if (truth.length) {
      wrap.append(el('h2', { class: 'sec-title' }, 'Where the truth lives'),
        el('p', { class: 'muted sec-sub' }, S.why.truthNote || 'Each item has one source of truth. Everything else is a copy or a view of it.'));
      wrap.append(el('div', { class: 'truth-grid' }, truth.map((p) => el('div', { class: `truth-card${planned(p) ? ' planned' : ''}` },
        el('div', { class: 'truth-head' }, el('span', { 'aria-hidden': 'true' }, PLACE_ICON[p.kind] || '•'), el('b', {}, p.title), planned(p) ? el('span', { class: 'chip flag' }, 'planned') : null),
        p.owner ? el('div', { class: 'muted' }, `Owner: ${ownerName(p.owner)}`) : null,
        el('ul', {}, p.truthFor.map((t) => el('li', {}, el('span', { class: 'star', role: 'img', 'aria-label': 'source of truth' }, '★'), ' ', t)))))));
    }

    wrap.append(el('h2', { class: 'sec-title' }, 'Three ways to read this page'));
    wrap.append(el('div', { class: 'views' },
      el('a', { class: 'view-card', href: '#why' }, el('b', {}, 'Why'), el('span', {}, 'The benefits, and where the truth lives.')),
      el('a', { class: 'view-card', href: '#who' }, el('b', {}, 'Who uses it'), el('span', {}, `The people of ${S.org.name || 'the example org'}, and a storyboard for each task.`)),
      el('a', { class: 'view-card', href: '#how' }, el('b', {}, "How it's built"), el('span', {}, 'The zoomable map of every part, read from the code.'))));
    root.replaceChildren(wrap);
  }

  // ── Who: org overview ──────────────────────────────────────────────────────
  function storyChips(activeId) {
    const nav = el('nav', { class: 'story-chips', 'aria-label': 'Stories' });
    for (const st of S.stories) {
      nav.append(el('a', { class: `story-chip${st.id === activeId ? ' on' : ''}${planned(st) ? ' planned' : ''}`, href: storyHref(st.id), 'aria-current': st.id === activeId ? 'page' : null },
        el('span', { class: 'who' }, personOf(st).name), ' ', st.title));
    }
    return nav;
  }

  function renderOrg() {
    const wrap = el('div', { class: 'page' });
    wrap.append(el('h1', { class: 'org-name' }, S.org.name), el('p', { class: 'why-lead' }, S.org.blurb || ''));
    const grid = el('div', { class: 'teams' });
    for (const t of S.org.teams) {
      const members = S.org.people.filter((p) => p.team === t.id);
      const owned = S.places.filter((p) => (p.owner === t.id || members.some((m) => m.id === p.owner)) && (p.kind === 'repo' || (p.truthFor || []).length));
      grid.append(el('section', { class: 'team' },
        el('h2', {}, t.name), t.about ? el('p', { class: 'muted' }, t.about) : null,
        members.map((p) => {
          const sts = (p.stories || []).map((sid) => stories.get(sid)).filter(Boolean);
          return el('article', { class: `person${sts.length ? '' : ' elsewhere'}` },
            el('div', { class: 'person-head' }, el('span', { class: 'avatar', 'aria-hidden': 'true' }, p.name.slice(0, 1)), el('div', {}, el('b', {}, p.name), el('div', { class: 'muted' }, p.role))),
            p.goal ? el('p', { class: 'goal' }, p.goal) : null,
            sts.length ? el('ul', { class: 'person-stories' }, sts.map((st) => el('li', {}, el('a', { href: storyHref(st.id) }, `${st.title} →`), planned(st) ? el('span', { class: 'chip flag' }, 'planned') : null))) : null,
            p.elsewhere ? el('p', { class: 'muted' }, el('a', { href: p.elsewhere.href, target: '_blank', rel: 'noopener' }, p.elsewhere.label)) : null);
        }),
        owned.length ? el('div', { class: 'owned' }, el('h3', {}, 'Owns'), owned.map((p) => el('div', { class: 'owned-place' },
          el('span', { 'aria-hidden': 'true' }, PLACE_ICON[p.kind] || '•'), ' ', p.title, (p.truthFor || []).length ? el('span', { class: 'star', title: `Source of truth for: ${p.truthFor.join(', ')}` }, ' ★') : null))) : null));
    }
    wrap.append(grid);
    wrap.append(el('h2', { class: 'sec-title' }, 'Every story'), storyChips(null));
    return wrap;
  }

  // ── Who: storyboard ────────────────────────────────────────────────────────
  const G = { label: 160, col: 214, row: 116, cardW: 178, cardH: 64, pad: 10, head: 30 };

  function lanesOf(story) {
    const order = [];
    const add = (id) => id && !order.includes(id) && order.push(id);
    for (const st of story.steps) {
      add(st.place);
      for (const m of st.moves || []) {
        add(m.from);
        add(m.to);
      }
      add(st.truth);
    }
    return order;
  }

  function renderBoard(story, sel) {
    const lanes = lanesOf(story);
    const n = story.steps.length;
    // Long stories get narrower columns, so most fit without a sideways scroll.
    G.col = n > 6 ? 156 : 196;
    G.cardW = G.col - 22;
    const W = n * G.col + 12;
    const H = G.head + lanes.length * G.row;
    const board = el('div', { class: 'board', style: `width:${W}px;height:${H}px`, role: 'group', 'aria-label': `${story.title}: ${n} steps` });
    const labels = el('div', { class: 'lane-col', style: `width:${G.label}px;height:${H}px` });
    const svg = svgEl('svg', { class: 'board-svg', width: W, height: H, viewBox: `0 0 ${W} ${H}`, 'aria-hidden': 'true' });
    const defs = svgEl('defs', {}, svg);
    for (const k of Object.keys(KIND)) {
      const m = svgEl('marker', { id: `mk-${k}`, viewBox: '0 0 10 10', refX: 8, refY: 5, markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse' }, defs);
      svgEl('path', { d: 'M0,0 L10,5 L0,10 z', class: `mk mk-${k}` }, m);
    }
    board.append(svg);
    const laneY = (id) => G.head + lanes.indexOf(id) * G.row;

    lanes.forEach((id, i) => {
      const p = places.get(id) || { title: id };
      svgEl('rect', { x: 0, y: G.head + i * G.row, width: W, height: G.row, class: `lane-bg${i % 2 ? ' odd' : ''}` }, svg);
      const isTruth = sel && story.steps[sel - 1].truth === id;
      labels.append(el('div', { class: `lane-label${isTruth ? ' truth-now' : ''}${planned(p) ? ' planned' : ''}`, style: `top:${G.head + i * G.row}px;height:${G.row}px` },
        el('span', { class: 'lane-icon', 'aria-hidden': 'true' }, PLACE_ICON[p.kind] || '•'),
        el('b', {}, p.title),
        p.owner ? el('span', { class: 'muted' }, ownerName(p.owner)) : null,
        (p.truthFor || []).length ? el('span', { class: 'lane-truth', title: `Source of truth for: ${p.truthFor.join(', ')}` }, `★ ${p.truthFor[0]}${p.truthFor.length > 1 ? ' …' : ''}`) : null));
    });

    story.steps.forEach((st, i) => {
      const x0 = i * G.col;
      const cx = x0 + G.col / 2;
      const on = sel === i + 1;
      board.append(el('div', { class: `step-num${on ? ' on' : ''}`, style: `left:${x0}px;width:${G.col}px` }, String(i + 1)));
      if (on) svgEl('rect', { x: x0 + 2, y: G.head, width: G.col - 4, height: H - G.head, class: 'col-on', rx: 10 }, svg);
      const cardTop = laneY(st.place) + G.pad;
      const card = el('button', {
        type: 'button', class: `step-card${on ? ' on' : ''}${planned(st) || planned(story) ? ' planned' : ''}`,
        style: `left:${cx - G.cardW / 2}px;top:${cardTop}px;width:${G.cardW}px;height:${G.cardH}px`,
        'data-step': i + 1, 'aria-current': on ? 'step' : null, 'aria-label': `Step ${i + 1}: ${st.label || st.text}`,
        onclick: () => go(storyHref(story.id, i + 1)),
      }, el('b', {}, st.label || ''), el('span', {}, st.label ? '' : st.text));
      board.append(card);

      const moves = st.moves || [];
      const below = new Map(); // lane id → pills already placed in this column
      const under = { n: 0 }; // pills under this column's card
      moves.forEach((m, k) => {
        const off = moves.length > 1 ? (k - (moves.length - 1) / 2) * 30 : 0;
        const x = cx + off;
        const toY = laneY(m.to);
        const fromY = laneY(m.from);
        const slot = below.get(m.to) || 0;
        below.set(m.to, slot + 1);
        const down = toY > fromY;
        let pillY;
        if (m.from === m.to) {
          // Kept in the same place (a local store): the pill sits under the card or mid-lane.
          pillY = m.to === st.place ? cardTop + G.cardH + 14 + under.n++ * 22 : toY + G.row / 2 + slot * 22;
        } else if (m.to === st.place) {
          // Into the card's own lane: the arrow stops at the card, the pill sits on the arrow.
          const startY = fromY + G.row / 2;
          const endY = down ? cardTop : cardTop + G.cardH;
          pillY = (startY + endY) / 2;
          svgEl('path', { d: `M${x},${startY} L${x},${endY}`, class: `mv mv-${m.kind}${on ? ' on' : ''}`, 'marker-end': `url(#mk-${m.kind})` }, svg);
        } else {
          // To another lane: the arrow ends at the pill, which names what arrives there.
          pillY = toY + G.row / 2 + (slot - 0.5) * 22 + 11;
          const startY = m.from === st.place ? (down ? cardTop + G.cardH : cardTop) : fromY + G.row / 2;
          svgEl('path', { d: `M${x},${startY} L${x},${down ? pillY - 12 : pillY + 12}`, class: `mv mv-${m.kind}${on ? ' on' : ''}`, 'marker-end': `url(#mk-${m.kind})` }, svg);
        }
        pillY = Math.min(pillY, toY + G.row - 12);
        board.append(el('div', { class: `pill pill-${m.kind}${on ? ' on' : ''}`, style: `left:${x}px;top:${pillY}px;max-width:${G.col - 16 - Math.abs(off)}px`, title: `${placeTitle(m.from)} → ${placeTitle(m.to)}` },
          el('span', { class: 'pill-kind' }, KIND[m.kind].verb), ' ', m.what));
      });
      if (st.truth) {
        board.append(el('div', { class: `truth-badge${on ? ' on' : ''}`, style: `left:${x0 + G.col - 26}px;top:${laneY(st.truth) + 6}px`, title: `Source of truth: ${placeTitle(st.truth)}` }, '★'));
      }
    });
    board.addEventListener('keydown', (e) => {
      if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
      e.preventDefault();
      const next = Math.max(1, Math.min(n, (sel || 0) + (e.key === 'ArrowRight' ? 1 : -1)));
      go(storyHref(story.id, next));
    });
    return el('div', { class: 'board-frame' }, labels, el('div', { class: 'board-scroll' }, board));
  }

  function renderList(story, sel) {
    const ol = el('ol', { class: 'step-list' });
    story.steps.forEach((st, i) => {
      ol.append(el('li', { class: `${sel === i + 1 ? 'on' : ''}${planned(st) || planned(story) ? ' planned' : ''}`, 'aria-current': sel === i + 1 ? 'step' : null },
        el('a', { href: storyHref(story.id, i + 1), class: 'step-list-head' }, el('span', { class: 'n' }, String(i + 1)), el('b', {}, st.label || placeTitle(st.place)), el('span', { class: 'muted' }, ` · ${placeTitle(st.place)}`), planned(st) || planned(story) ? el('span', { class: 'chip flag' }, 'planned') : null),
        el('p', {}, st.text),
        (st.moves || []).length ? el('ul', { class: 'moves' }, st.moves.map((m) => el('li', { class: `mv-row mv-row-${m.kind}` }, `${placeTitle(m.from)} → ${placeTitle(m.to)}: `, el('b', {}, m.what), el('span', { class: 'muted' }, ` (${KIND[m.kind].verb})`)))) : null,
        st.truth ? el('p', { class: 'truth-line' }, el('span', { class: 'star' }, '★'), ` Source of truth: ${placeTitle(st.truth)}`) : null,
        st.command ? el('pre', { class: 'code' }, st.command) : null,
        nodeLink(st.node)));
    });
    return ol;
  }

  function renderStepPanel(story, sel) {
    const st = story.steps[sel - 1];
    const n = story.steps.length;
    const box = el('aside', { class: 'step-panel', 'aria-label': `Step ${sel} details` });
    const what = el('div', { class: 'sp-main' });
    what.append(el('div', { class: 'muted' }, `Step ${sel} of ${n} · ${placeTitle(st.place)}`));
    if (planned(st) || planned(story)) what.append(el('p', { class: 'planned-note' }, 'Planned, not built yet.'));
    what.append(el('h3', {}, st.label || `Step ${sel}`), el('p', {}, st.text));
    if (st.command) what.append(el('pre', { class: 'code' }, st.command));
    box.append(what);
    const side = el('div', { class: 'sp-side' });
    if ((st.moves || []).length) {
      side.append(el('h4', {}, 'What moves'), el('ul', { class: 'moves' }, st.moves.map((m) => el('li', { class: `mv-row mv-row-${m.kind}` },
        el('span', { class: 'pill-kind' }, KIND[m.kind].label), ' ', el('b', {}, m.what), el('div', { class: 'muted' }, `${placeTitle(m.from)} → ${placeTitle(m.to)}`)))));
    } else side.append(el('h4', {}, 'What moves'), el('p', { class: 'muted' }, 'Nothing leaves this place in this step.'));
    if (st.truth) {
      const p = places.get(st.truth) || {};
      side.append(el('h4', {}, 'Source of truth'), el('p', {}, el('span', { class: 'star' }, '★'), ` ${p.title}`, (p.truthFor || []).length ? el('span', { class: 'muted' }, ` — for ${p.truthFor.join(', ')}`) : null));
    }
    box.append(side);
    const navBtn = (to, cls, text) => (to < 1 || to > n
      ? el('span', { class: `btn ${cls} off` }, text)
      : el('a', { class: `btn ${cls}`, href: storyHref(story.id, to), 'data-nav': cls || 'back' }, text));
    const nav = el('div', { class: 'step-nav' }, navBtn(sel - 1, '', '← Back'), navBtn(sel + 1, 'primary', 'Next →'));
    const link = nodeLink(st.node);
    if (link) nav.append(link);
    what.append(nav);
    return box;
  }

  function renderStory(r) {
    const story = stories.get(r.story);
    const n = story.steps.length;
    const sel = Math.max(1, Math.min(n, r.step || 1));
    const person = personOf(story);
    const wrap = el('div', { class: 'page story' });
    wrap.append(el('p', { class: 'crumb' }, el('a', { href: '#who' }, `← ${S.org.name}`)), storyChips(story.id));
    wrap.append(el('header', { class: 'story-head' },
      el('h1', { tabindex: '-1' }, story.title, planned(story) ? el('span', { class: 'chip flag' }, 'planned') : null),
      el('p', { class: 'story-who' }, el('span', { class: 'avatar', 'aria-hidden': 'true' }, person.name.slice(0, 1)), ` ${person.name}, ${person.role || ''}`),
      story.why ? el('p', { class: 'why-lead' }, story.why) : null));
    wrap.append(el('div', { class: 'legend-row' },
      Object.entries(KIND).map(([k, v]) => el('span', { class: `key-item key-${k}` }, el('i', {}), v.verb)),
      el('span', { class: 'key-item' }, el('span', { class: 'star' }, '★'), ' source of truth'),
      el('span', { class: 'key-item key-planned' }, el('i', {}), 'planned')));
    const body = el('div', { class: 'story-body' });
    if (NARROW.matches) body.append(renderList(story, sel));
    else body.append(renderBoard(story, sel), renderStepPanel(story, sel));
    wrap.append(body);
    return { wrap, sel };
  }

  function renderWho(r, prev, focus) {
    const root = panels.who;
    if (r.story && !stories.has(r.story)) {
      replaceHash('#who');
      r = { tab: 'who' };
      current = r;
    }
    if (!r.story) {
      root.replaceChildren(renderOrg());
      if (focus) root.focus({ preventScroll: true });
      return;
    }
    const story = stories.get(r.story);
    const n = story.steps.length;
    if (r.step && (r.step < 1 || r.step > n)) {
      r = { ...r, step: Math.max(1, Math.min(n, r.step)) };
      current = r;
      replaceHash(storyHref(r.story, r.step));
    }
    const sameStory = prev && prev.tab === 'who' && prev.story === r.story;
    const navFocus = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.nav : null;
    const keepScroll = sameStory ? root.querySelector('.board-scroll')?.scrollLeft || 0 : 0;
    const { wrap, sel } = renderStory(r);
    root.replaceChildren(wrap);
    if (!sameStory) root.scrollTop = 0;
    const st = story.steps[sel - 1];
    if (r.step) live.textContent = `Step ${sel} of ${n}: ${st.label}. ${st.text}`;
    const scroller = root.querySelector('.board-scroll');
    if (scroller) {
      scroller.scrollLeft = keepScroll;
      const card = root.querySelector(`.step-card[data-step="${sel}"]`);
      if (card) {
        const left = card.offsetLeft;
        if (left < scroller.scrollLeft || left + card.offsetWidth > scroller.scrollLeft + scroller.clientWidth) scroller.scrollLeft = Math.max(0, left - 40);
      }
    }
    if (!focus) return;
    // Keep keyboard focus where the reader was: on Back/Next, on the chosen step, or on the
    // story's heading when a new story opens.
    const target = (navFocus && root.querySelector(`[data-nav="${navFocus}"]`))
      || (r.step && sameStory && (root.querySelector(`.step-card[data-step="${sel}"]`) || root.querySelector('.step-list > li.on .step-list-head')))
      || root.querySelector('.story-head h1');
    if (target) {
      target.focus({ preventScroll: !!scroller });
      if (!scroller && r.step && sameStory) target.scrollIntoView({ block: 'nearest' });
    }
  }
  NARROW.addEventListener('change', () => current && current.tab === 'who' && renderWho(current));

  // ── start ──────────────────────────────────────────────────────────────────
  apply(route(location.hash.slice(1)));
})();
