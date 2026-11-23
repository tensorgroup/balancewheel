// app.js — zoomable usage-map engine. No libraries, no network; opens from disk.
// Inputs: window.USAGE_MAP_INFRA (infra.js, hand-written: people, places, services),
// window.USAGE_MAP_DATA (data.generated.js, generated from the code), and, for the "in
// stories" links, window.USAGE_MAP_STORIES (stories-data.js). The map lives in the "How it's
// built" tab; stories.js owns the tabs and the URL hash, and drives the map through
// window.USAGE_MAP. The map writes the hash (#how/<id>) only while its tab is shown.
(() => {
  'use strict';
  const INFRA = window.USAGE_MAP_INFRA;
  const DATA = window.USAGE_MAP_DATA;
  const NS = 'http://www.w3.org/2000/svg';

  document.title = INFRA.title;
  document.getElementById('brand-title').textContent = INFRA.title;
  document.getElementById('brand-sub').textContent = INFRA.tagline;
  // Without data.generated.js the page says how to generate it, instead of rendering half a map.
  if (!DATA) {
    const box = document.createElement('div');
    box.className = 'missing';
    const h = document.createElement('h2');
    h.textContent = 'Generate the map data first';
    const p = document.createElement('p');
    p.textContent = 'Part of this map is read from the code. From the repository root, run this once (and again after the code changes), then reload:';
    const pre = document.createElement('pre');
    pre.className = 'code';
    pre.textContent = INFRA.generateCommand;
    box.append(h, p, pre);
    document.querySelector('.stage').replaceChildren(box);
    return;
  }

  const W = INFRA.canvas.w;
  const H = INFRA.canvas.h;
  const svg = document.getElementById('map');
  const world = document.getElementById('world');
  const layers = {};
  // Resting edges draw under the nodes; highlighted edges move up to `edgesTop`.
  for (const name of ['clusters', 'edges', 'nodes', 'edgesTop']) {
    layers[name] = svgEl('g', { class: `layer-${name}` }, world);
  }

  /** id → { id, kind, title, rect, el, parent, data, children, edges } */
  const items = new Map();
  const edges = [];
  const TONES = new Map((INFRA.legend || []).map((l) => [l.tone, l]));

  // ── helpers ────────────────────────────────────────────────────────────────
  function svgEl(tag, attrs, parent) {
    const node = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v !== undefined && v !== null) node.setAttribute(k, v);
    if (parent) parent.appendChild(node);
    return node;
  }
  function htmlEl(tag, attrs, text) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined) continue;
      if (k === 'class') node.className = v;
      else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    }
    if (text !== undefined) node.textContent = text;
    return node;
  }
  const TIERS = [30, 22, 16, 12, 9, 7, 5.5, 4.2, 3.2, 2.4, 1.8, 1.3];
  function tierFor(size) {
    for (const t of TIERS) if (size >= t) return t;
    return TIERS[TIERS.length - 1];
  }
  const tierClass = (t) => `t${String(t).replace('.', '_')}`;
  function wrap(str, maxWidth, fs, maxLines) {
    const perLine = Math.max(4, Math.floor(maxWidth / (fs * 0.56)));
    const words = String(str).split(/\s+/);
    const lines = [];
    let line = '';
    for (const word of words) {
      const next = line ? `${line} ${word}` : word;
      if (next.length > perLine && line) {
        lines.push(line);
        line = word;
      } else line = next;
    }
    if (line) lines.push(line);
    if (lines.length > maxLines) {
      const kept = lines.slice(0, maxLines);
      kept[maxLines - 1] = kept[maxLines - 1].replace(/.{0,2}$/, '…');
      return kept;
    }
    return lines;
  }
  function text(parent, x, y, str, fs, cls, opts = {}) {
    const t = tierFor(fs);
    const node = svgEl('text', { x, y, class: `${tierClass(t)} ${cls || ''}`, 'font-size': fs, 'text-anchor': opts.anchor }, parent);
    if (opts.lines) {
      opts.lines.forEach((line, i) => {
        const span = svgEl('tspan', { x, dy: i === 0 ? 0 : fs * 1.25 }, node);
        span.textContent = line;
      });
    } else node.textContent = str;
    return node;
  }
  function register(id, info) {
    if (items.has(id)) console.warn('usage map: duplicate id', id);
    items.set(id, { id, children: [], edges: [], ...info });
    if (info.parent && items.has(info.parent)) items.get(info.parent).children.push(id);
    return items.get(id);
  }
  function inset(r, p) {
    return { x: r.x + p, y: r.y + p, w: Math.max(0, r.w - 2 * p), h: Math.max(0, r.h - 2 * p) };
  }
  const fmt = (n) => (typeof n === 'number' ? n.toLocaleString('en-US') : n);
  const isPlanned = (d) => d && d.status === 'planned';

  // ── squarified treemap ─────────────────────────────────────────────────────
  function squarify(list, rect) {
    const total = list.reduce((s, i) => s + i.value, 0) || 1;
    const scale = (rect.w * rect.h) / total;
    const queue = list.map((item) => ({ item, area: item.value * scale })).sort((a, b) => b.area - a.area);
    const out = [];
    const r = { ...rect };
    let row = [];
    const worst = (rw, side) => {
      if (!rw.length) return Infinity;
      const s = rw.reduce((a, n) => a + n.area, 0);
      const max = Math.max(...rw.map((n) => n.area));
      const min = Math.min(...rw.map((n) => n.area));
      return Math.max((side * side * max) / (s * s), (s * s) / (side * side * min));
    };
    const place = (rw) => {
      const s = rw.reduce((a, n) => a + n.area, 0);
      if (r.w >= r.h) {
        const w = s / r.h;
        let y = r.y;
        for (const n of rw) {
          const h = n.area / w;
          out.push({ item: n.item, x: r.x, y, w, h });
          y += h;
        }
        r.x += w;
        r.w -= w;
      } else {
        const h = s / r.w;
        let x = r.x;
        for (const n of rw) {
          const w = n.area / h;
          out.push({ item: n.item, x, y: r.y, w, h });
          x += w;
        }
        r.y += h;
        r.h -= h;
      }
    };
    while (queue.length) {
      const side = Math.min(r.w, r.h);
      if (worst(row.concat(queue[0]), side) <= worst(row, side)) row.push(queue.shift());
      else {
        place(row);
        row = [];
      }
    }
    if (row.length) place(row);
    return out;
  }

  /**
   * One font size for every name in a box: the largest at which each name fits its cell on
   * one line or, split at a camelCase hump / separator, on two. Per name: lines or null.
   */
  const CHAR_W = 0.64;
  function splitName(name) {
    const mid = name.length / 2;
    let best = -1;
    for (let i = 1; i < name.length; i++) {
      const hump = /[A-Z]/.test(name[i]) && /[a-z0-9]/.test(name[i - 1]);
      const sep = /[\s\-_./:]/.test(name[i - 1]);
      if ((hump || sep) && (best < 0 || Math.abs(i - mid) < Math.abs(best - mid))) best = i;
    }
    return best > 0 ? [name.slice(0, best).trimEnd(), name.slice(best)] : null;
  }
  function fitNames(names, avail, ch) {
    const splits = names.map(splitName);
    const best = names.map((name, i) => {
      const one = Math.min(ch * 0.34, avail / (name.length * CHAR_W));
      const two = splits[i] && ch > 0 ? Math.min(ch * 0.3, avail / (Math.max(splits[i][0].length, splits[i][1].length) * CHAR_W)) : 0;
      return Math.max(one, two);
    });
    const fs = names.length ? Math.min(...best) : 0;
    const lines = names.map((name, i) => (name.length * CHAR_W * fs <= avail + 1e-6 ? null : splits[i]));
    return { fs, lines };
  }

  // ── clusters and nodes ─────────────────────────────────────────────────────
  function renderCluster(c) {
    const g = svgEl('g', { class: `cluster tone-${c.tone}${isPlanned(c) ? ' planned' : ''}`, 'data-id': c.id }, layers.clusters);
    svgEl('rect', { x: c.x, y: c.y, width: c.w, height: c.h, rx: 22, class: 'cluster-bg' }, g);
    text(g, c.x + 24, c.y + 44, c.title, 30, 'cl-title');
    text(g, c.x + 24, c.y + 70, c.subtitle, 13, 'cl-sub');
    if (isPlanned(c)) text(g, c.x + c.w - 24, c.y + 40, 'PLANNED', 12, 'planned-tag', { anchor: 'end' });
    register(c.id, { kind: 'cluster', title: c.title, rect: { x: c.x, y: c.y, w: c.w, h: c.h }, el: g, data: c, tone: c.tone, parent: 'root' });
    items.get('root').children.push(c.id);
    const inner = { x: c.x + 18, y: c.y + 88, w: c.w - 36, h: c.h - 106 };
    if (c.treemap) renderTreemapCluster(c, inner);
    else gridNodes(c.nodes || [], inner, c.cols || 1, c.id, c.tone, c.nodeGap);
  }

  function gridNodes(nodes, rect, cols, parent, tone, gap = 14) {
    const rows = Math.ceil(nodes.length / cols) || 1;
    const cw = (rect.w - gap * (cols - 1)) / cols;
    const ch = (rect.h - gap * (rows - 1)) / rows;
    nodes.forEach((n, i) => {
      const col = i % cols;
      const row = Math.floor(i / cols);
      renderNode(n, { x: rect.x + col * (cw + gap), y: rect.y + row * (ch + gap), w: cw, h: ch }, parent, tone);
    });
  }

  function renderNode(n, r, parent, tone) {
    const planned = isPlanned(n);
    const g = svgEl('g', { class: `node tone-${n.tone || tone}${planned ? ' planned' : ''}${n.kind === 'persona' ? ' persona' : ''}`, 'data-id': n.id }, layers.nodes);
    svgEl('rect', { x: r.x, y: r.y, width: r.w, height: r.h, rx: n.kind === 'persona' ? r.h / 5 : 12, class: 'node-bg' }, g);
    const pad = Math.min(14, r.w * 0.06);
    let y = r.y + pad + 15;
    const titleFs = Math.min(24, r.h * 0.26, (r.w - pad * 2) / (n.title.length * 0.6));
    text(g, r.x + pad, y, n.title, titleFs, 'n-title');
    if (planned) text(g, r.x + r.w - pad, r.y + pad + 8, 'PLANNED', Math.max(5.5, titleFs * 0.4), 'planned-tag', { anchor: 'end' });
    y += titleFs * 0.9;
    if (n.sub) {
      const fs = Math.max(9, Math.min(13, titleFs * 0.62));
      const lines = wrap(n.sub, r.w - pad * 2, fs, 2);
      text(g, r.x + pad, y + fs, '', fs, 'n-sub', { lines });
      y += fs * 1.25 * lines.length + 6;
    }
    const facts = n.facts || [];
    if (facts.length) {
      const fs = Math.max(5.5, Math.min(8, r.w / 34));
      const room = Math.floor((r.y + r.h - pad - y) / (fs * 1.3));
      const lines = [];
      for (const f of facts) for (const l of wrap(`• ${f}`, r.w - pad * 2, fs, 3)) lines.push(l);
      if (room > 0) text(g, r.x + pad, y + fs, '', fs, 'n-fact', { lines: lines.slice(0, room) });
    }
    return register(n.id, { kind: n.kind || 'service', title: n.title, rect: r, el: g, parent, data: n, tone: n.tone || tone });
  }

  // A treemap cluster: an optional pipeline row, then generated groups of items.
  function renderTreemapCluster(c, inner) {
    let top = inner.y;
    const steps = c.pipeline || [];
    if (steps.length) {
      const band = { x: inner.x, y: inner.y, w: inner.w, h: c.pipelineHeight || 120 };
      const gap = 24;
      const sw = (band.w - gap * (steps.length - 1)) / steps.length;
      steps.forEach((s, i) => {
        const r = { x: band.x + i * (sw + gap), y: band.y, w: sw, h: band.h };
        renderNode({ ...s, kind: s.kind || 'step' }, r, c.id, c.tone);
        if (i > 0) {
          const y = r.y + r.h / 2;
          svgEl('path', { d: `M${r.x - gap + 4},${y} L${r.x - 4},${y}`, class: 'pipe', 'marker-end': 'url(#arrow-pipe)' }, layers.nodes);
        }
      });
      top = band.y + band.h + 20;
    }
    const tm = DATA.treemaps && DATA.treemaps[c.treemap];
    if (!tm) {
      console.warn('usage map: no generated treemap', c.treemap);
      return;
    }
    const area = { x: inner.x, y: top, w: inner.w, h: inner.y + inner.h - top };
    const groups = tm.groups.filter((g) => g.items.length);
    const cells = squarify(groups.map((g) => ({ ...g, value: g.items.length + (c.groupWeight ?? 1) })), area);
    for (const cell of cells) renderGroup(cell.item, inset(cell, 4), c);
  }

  function renderGroup(grp, r, c) {
    const id = grp.id;
    const planned = isPlanned(grp);
    // The tone class sits on the group itself: groups live in the nodes layer, outside the cluster's
    // element, so without it var(--tone) is undefined and the group fill falls back to black.
    const g = svgEl('g', { class: `group tone-${c.tone}${planned ? ' planned' : ''}`, 'data-id': id }, layers.nodes);
    svgEl('rect', { x: r.x, y: r.y, width: r.w, height: r.h, rx: 8, class: 'group-bg' }, g);
    const fs = Math.min(13, Math.max(3, Math.min(r.w / ((grp.title.length + 6) * 0.62), r.h * 0.14)));
    text(g, r.x + 6, r.y + fs + 4, `${grp.title} · ${grp.items.length}`, fs, 'grp-title');
    register(id, { kind: 'group', title: grp.title, rect: r, el: g, parent: c.id, data: grp, tone: c.tone });
    const body = { x: r.x + 3, y: r.y + fs + 9, w: r.w - 6, h: r.h - fs - 12 };
    const n = grp.items.length;
    if (!n || body.w <= 0 || body.h <= 0) return;
    const aspect = c.cellAspect || 1;
    const cols = Math.max(1, Math.min(n, Math.round(Math.sqrt((n * body.w) / body.h / aspect))));
    const rows = Math.ceil(n / cols);
    const cw = body.w / cols;
    const ch = body.h / rows;
    const fit = fitNames(grp.items.map((it) => it.name), cw - 2, ch * (grp.items.some((it) => it.detail) ? 0.72 : 1));
    const details = grp.items.filter((it) => it.detail);
    const dfs = details.length ? Math.min(fit.fs * 0.7, ...details.map((it) => (cw - 2.4) / (it.detail.length * 0.62))) : 0;
    grp.items.forEach((it, i) => {
      const x = body.x + (i % cols) * cw;
      const y = body.y + Math.floor(i / cols) * ch;
      const tone = TONES.get(it.tone);
      const ig = svgEl('g', { class: `item${isPlanned(it) ? ' planned' : ''}`, 'data-id': it.id, style: tone ? `--c:${tone.color}` : null }, layers.nodes);
      svgEl('rect', { x: x + 0.5, y: y + 0.5, width: Math.max(0.2, cw - 1), height: Math.max(0.2, ch - 1), rx: 1.2, class: 'item-bg' }, ig);
      if (fit.fs >= 0.6) {
        const lines = fit.lines[i];
        const nameY = it.detail ? y + ch * 0.42 : y + ch / 2;
        if (lines) text(ig, x + 1.2, nameY - fit.fs * 0.2, '', fit.fs, 'item-name', { lines });
        else text(ig, x + 1.2, nameY + fit.fs * 0.35, it.name, fit.fs, 'item-name');
        if (it.detail && dfs >= 0.5) text(ig, x + 1.2, nameY + fit.fs * (lines ? 1.9 : 1.35), it.detail, dfs, 'item-detail');
      }
      register(it.id, { kind: 'item', title: it.title || it.name, rect: { x, y, w: cw, h: ch }, el: ig, parent: id, data: it, tone: c.tone });
    });
  }

  // ── edges ──────────────────────────────────────────────────────────────────
  function anchor(r, tx, ty) {
    const cx = r.x + r.w / 2;
    const cy = r.y + r.h / 2;
    const dx = tx - cx;
    const dy = ty - cy;
    if (!dx && !dy) return [cx, cy];
    const s = Math.min(r.w / 2 / Math.abs(dx || 1e-9), r.h / 2 / Math.abs(dy || 1e-9));
    return [cx + dx * s, cy + dy * s];
  }
  function edgePath(a, b, bend = 0.12) {
    const ac = [a.x + a.w / 2, a.y + a.h / 2];
    const bc = [b.x + b.w / 2, b.y + b.h / 2];
    const [x1, y1] = anchor(a, bc[0], bc[1]);
    const [x2, y2] = anchor(b, ac[0], ac[1]);
    const mx = (x1 + x2) / 2;
    const my = (y1 + y2) / 2;
    const nx = -(y2 - y1) * bend;
    const ny = (x2 - x1) * bend;
    return { d: `M${x1.toFixed(1)},${y1.toFixed(1)} Q${(mx + nx).toFixed(1)},${(my + ny).toFixed(1)} ${x2.toFixed(1)},${y2.toFixed(1)}`, lx: mx + nx / 2, ly: my + ny / 2 };
  }
  function addEdge(e) {
    const a = items.get(e.from);
    const b = items.get(e.to);
    if (!a || !b) {
      console.warn('usage map: edge endpoint missing', e);
      return;
    }
    const { d, lx, ly } = edgePath(a.rect, b.rect, e.bend ?? 0.12);
    const g = svgEl('g', { class: `edge${e.derived ? ' derived' : ''} kind-${e.kind || 'data'}` }, layers.edges);
    svgEl('path', { d, class: 'edge-line', 'marker-end': 'url(#arrow)' }, g);
    if (e.label) text(g, lx, ly - 2, e.label, e.derived ? 4.2 : 9, 'edge-label', { anchor: 'middle' });
    const edge = { ...e, el: g };
    edges.push(edge);
    for (const end of [e.from, e.to]) {
      let cur = items.get(end);
      while (cur) {
        cur.edges.push(edge);
        cur = cur.parent ? items.get(cur.parent) : null;
      }
    }
  }

  // ── build ──────────────────────────────────────────────────────────────────
  register('root', { kind: 'root', title: INFRA.rootTitle || 'Overview', rect: { x: 0, y: -70, w: W, h: H + 300 }, data: { desc: INFRA.summary } });
  for (const c of INFRA.clusters) renderCluster(c);
  for (const e of INFRA.edges || []) addEdge(e);
  for (const e of DATA.edges || []) addEdge({ ...e, derived: true, bend: e.bend ?? 0.05 });

  // ── camera ─────────────────────────────────────────────────────────────────
  const cam = { cx: W / 2, cy: H / 2, upp: 1 };
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  let anim = null;
  const size = () => ({ w: svg.clientWidth || 1, h: svg.clientHeight || 1 });
  // While another tab is shown the SVG has no size; camera work then waits for show().
  const shown = () => svg.clientWidth > 0;
  // The whole-map view leaves room at the top for the breadcrumb and at the bottom for the minimap.
  const FIT_RECT = { x: 0, y: -70, w: W, h: H + 300 };
  const fitUpp = () => {
    const s = size();
    return Math.max(FIT_RECT.w / s.w, FIT_RECT.h / s.h) * 1.04;
  };
  const MIN_UPP = () => fitUpp() / 120;
  const MAX_UPP = () => fitUpp() * 1.6;
  function clampCam() {
    cam.upp = Math.min(MAX_UPP(), Math.max(MIN_UPP(), cam.upp));
    cam.cx = Math.min(W, Math.max(0, cam.cx));
    cam.cy = Math.min(H + 230, Math.max(-70, cam.cy));
  }
  let lastTierState = '';
  function applyCam() {
    if (!shown()) return;
    clampCam();
    const s = size();
    const vw = s.w * cam.upp;
    const vh = s.h * cam.upp;
    svg.setAttribute('viewBox', `${cam.cx - vw / 2} ${cam.cy - vh / 2} ${vw} ${vh}`);
    const ppu = 1 / cam.upp;
    // Level of detail: hide text tiers too small to read, fade tiers far too large.
    const hidden = TIERS.filter((t) => t * ppu < 6.5);
    const huge = TIERS.filter((t) => t * ppu > 140);
    const state = hidden.join() + '|' + huge.join();
    if (state !== lastTierState) {
      lastTierState = state;
      const keep = (svg.getAttribute('class') || '').replace(/\s*(hide|huge)-\S+/g, '');
      svg.setAttribute('class', `${keep} ${hidden.map((t) => `hide-${tierClass(t)}`).join(' ')} ${huge.map((t) => `huge-${tierClass(t)}`).join(' ')}`.trim());
    }
    zoomReadout.textContent = `${Math.round((fitUpp() / cam.upp) * 100)}%`;
    updateMinimap();
  }
  function flyTo(rect, pad = 1.12) {
    if (!shown()) return;
    const s = size();
    const target = {
      cx: rect.x + rect.w / 2,
      cy: rect.y + rect.h / 2,
      upp: Math.min(MAX_UPP(), Math.max(MIN_UPP(), Math.max(rect.w / s.w, rect.h / s.h) * pad)),
    };
    if (reduceMotion) {
      Object.assign(cam, target);
      applyCam();
      return;
    }
    const from = { ...cam };
    const start = performance.now();
    if (anim) cancelAnimationFrame(anim);
    const step = (now) => {
      const t = Math.min(1, (now - start) / 650);
      const e = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
      cam.cx = from.cx + (target.cx - from.cx) * e;
      cam.cy = from.cy + (target.cy - from.cy) * e;
      cam.upp = Math.exp(Math.log(from.upp) + (Math.log(target.upp) - Math.log(from.upp)) * e);
      applyCam();
      anim = t < 1 ? requestAnimationFrame(step) : null;
    };
    anim = requestAnimationFrame(step);
  }
  function stopAnim() {
    if (anim) {
      cancelAnimationFrame(anim);
      anim = null;
    }
  }
  function zoomAt(px, py, factor) {
    const s = size();
    const wx = cam.cx + (px - s.w / 2) * cam.upp;
    const wy = cam.cy + (py - s.h / 2) * cam.upp;
    const next = Math.min(MAX_UPP(), Math.max(MIN_UPP(), cam.upp / factor));
    cam.cx = wx - (px - s.w / 2) * next;
    cam.cy = wy - (py - s.h / 2) * next;
    cam.upp = next;
    applyCam();
  }

  // ── pointer input ──────────────────────────────────────────────────────────
  const pointers = new Map();
  let drag = null;
  svg.addEventListener('wheel', (e) => {
    e.preventDefault();
    stopAnim();
    const box = svg.getBoundingClientRect();
    const dy = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY;
    if (e.ctrlKey || e.metaKey || Math.abs(dy) >= 40 || !e.deltaX) zoomAt(e.clientX - box.left, e.clientY - box.top, Math.exp(-dy * (e.ctrlKey ? 0.01 : 0.0022)));
    else {
      cam.cx += e.deltaX * cam.upp;
      cam.cy += dy * cam.upp;
      applyCam();
    }
  }, { passive: false });
  svg.addEventListener('pointerdown', (e) => {
    svg.setPointerCapture(e.pointerId);
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    stopAnim();
    drag = pointers.size === 1 ? { x: e.clientX, y: e.clientY, moved: 0, target: e.target } : null;
  });
  svg.addEventListener('pointermove', (e) => {
    const prev = pointers.get(e.pointerId);
    if (!prev) return hover(e);
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      const before = Math.hypot(a.x - b.x, a.y - b.y);
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      const [c, d] = [...pointers.values()];
      const after = Math.hypot(c.x - d.x, c.y - d.y);
      const box = svg.getBoundingClientRect();
      if (before > 0) zoomAt((c.x + d.x) / 2 - box.left, (c.y + d.y) / 2 - box.top, after / before);
      return;
    }
    cam.cx -= (e.clientX - prev.x) * cam.upp;
    cam.cy -= (e.clientY - prev.y) * cam.upp;
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (drag) drag.moved += Math.abs(e.clientX - prev.x) + Math.abs(e.clientY - prev.y);
    svg.classList.add('panning');
    applyCam();
  });
  const endPointer = (e) => {
    pointers.delete(e.pointerId);
    svg.classList.remove('panning');
    if (drag && drag.moved < 6 && e.type === 'pointerup') {
      const hit = drag.target.closest && drag.target.closest('[data-id]');
      if (hit) {
        clickBox(hit.getAttribute('data-id'));
      } else if (selectedId) {
        // A click on empty canvas clears the selection and its lines; the view stays put.
        select('root');
      }
    }
    drag = null;
  };
  svg.addEventListener('pointerup', endPointer);
  svg.addEventListener('pointercancel', endPointer);
  // No double-click zoom: two quick clicks are the select → zoom-in steps of clickBox.
  window.addEventListener('resize', () => applyCam());

  // ── hover tag and selection highlighting ───────────────────────────────────
  // Hover only shows a name tag. Lines follow the selection (a click), so they do not
  // jump between boxes while the view zooms or pans under a still pointer.
  const tip = document.getElementById('tip');
  function hover(e) {
    const hit = e.target.closest && e.target.closest('[data-id]');
    if (!hit) {
      tip.hidden = true;
      return;
    }
    const it = items.get(hit.getAttribute('data-id'));
    tip.hidden = false;
    tip.textContent = '';
    tip.append(htmlEl('b', {}, it.title), htmlEl('span', {}, kindLabel(it)));
    const box = svg.parentElement.getBoundingClientRect();
    tip.style.left = `${Math.min(e.clientX - box.left + 14, box.width - 240)}px`;
    tip.style.top = `${e.clientY - box.top + 16}px`;
  }
  svg.addEventListener('pointerleave', () => {
    tip.hidden = true;
  });
  // An area or group lights only its hand-written links; generated links show per item.
  function shownEdges(it) {
    return ['cluster', 'group'].includes(it.kind) ? it.edges.filter((e) => !e.derived) : it.edges;
  }
  let lit = [];
  let raised = [];
  function highlight(id) {
    for (const node of lit) node.classList.remove('hl', 'peer');
    lit = [];
    for (const el of raised) layers.edges.appendChild(el);
    raised = [];
    svg.classList.toggle('focusing', !!id && id !== 'root');
    if (!id || id === 'root') return;
    const it = items.get(id);
    if (!it) return;
    if (it.el) {
      it.el.classList.add('hl');
      lit.push(it.el);
    }
    for (const e of shownEdges(it)) {
      e.el.classList.add('hl');
      lit.push(e.el);
      layers.edgesTop.appendChild(e.el);
      raised.push(e.el);
      for (const end of [e.from, e.to]) {
        const peer = items.get(end);
        if (peer && peer.el && peer.id !== id) {
          peer.el.classList.add('peer');
          lit.push(peer.el);
        }
      }
    }
  }

  // ── click cycle on one box ─────────────────────────────────────────────────
  // 1st click: select and show lines (view stays). 2nd: zoom in on the box.
  // 3rd: zoom out to the box plus everything its lines reach (whole map if none).
  // Further clicks alternate between the 2nd and 3rd steps.
  let clickStage = 0;
  const zoomPad = (it) => (it.kind === 'item' ? 3.2 : 1.1);
  function connectedRect(it) {
    const rects = [it.rect];
    for (const e of shownEdges(it)) {
      for (const end of [e.from, e.to]) {
        const peer = items.get(end);
        if (peer) rects.push(peer.rect);
      }
    }
    if (rects.length === 1) return null;
    const x = Math.min(...rects.map((r) => r.x));
    const y = Math.min(...rects.map((r) => r.y));
    return { x, y, w: Math.max(...rects.map((r) => r.x + r.w)) - x, h: Math.max(...rects.map((r) => r.y + r.h)) - y };
  }
  function clickBox(id) {
    const it = items.get(id);
    if (!it) return;
    if (id !== selectedId) select(id);
    else if (clickStage !== 1) {
      flyTo(it.rect, zoomPad(it));
      clickStage = 1;
    } else {
      const near = connectedRect(it);
      flyTo(near || items.get('root').rect, near ? 1.12 : 1);
      clickStage = 2;
    }
  }

  // ── selection, breadcrumb, panel ───────────────────────────────────────────
  let selectedId = null;
  const crumbs = document.getElementById('crumbs');
  const panel = document.getElementById('panel');
  const panelBody = document.getElementById('panel-body');
  function ancestry(id) {
    const out = [];
    let cur = items.get(id);
    while (cur) {
      out.unshift(cur);
      cur = cur.parent ? items.get(cur.parent) : null;
    }
    return out;
  }
  function kindLabel(it) {
    const d = it.data || {};
    const base = { cluster: 'Area', service: 'Component', step: 'Step', group: 'Group', persona: 'Who uses it', root: 'Whole map', place: 'Place', file: 'File' }[it.kind] || it.kind;
    const label = it.kind === 'item' ? d.kindLabel || 'Item' : d.kindLabel || base;
    return isPlanned(d) ? `${label} · planned` : label;
  }
  function select(id, { fly = false } = {}) {
    if (!items.has(id)) return;
    selectedId = id === 'root' ? null : id;
    const it = items.get(id);
    if (fly) flyTo(it.rect, zoomPad(it));
    // A fly (search, breadcrumb, Esc) lands zoomed in, so the next click on the box zooms out.
    clickStage = fly ? 1 : 0;
    highlight(selectedId);
    renderCrumbs(id);
    renderPanel(it);
    if (!shown()) return;
    try {
      history.replaceState(null, '', id === 'root' ? '#how' : `#how/${encodeURIComponent(id)}`);
    } catch {
      /* file:// in some browsers */
    }
  }
  function renderCrumbs(id) {
    crumbs.textContent = '';
    ancestry(id).forEach((it, i, all) => {
      if (i) crumbs.append(htmlEl('span', { class: 'sep', 'aria-hidden': 'true' }, '›'));
      const b = htmlEl('button', { type: 'button', onclick: () => select(it.id, { fly: true }) }, it.title);
      if (i === all.length - 1) b.setAttribute('aria-current', 'true');
      crumbs.append(b);
    });
  }
  function link(id, label) {
    const it = items.get(id);
    return htmlEl('button', { type: 'button', class: 'linkish', onclick: () => select(id, { fly: true }) }, label || (it ? it.title : id));
  }
  function fileLink(path) {
    const wrapEl = htmlEl('span', { class: 'file' });
    wrapEl.append(htmlEl('a', { href: INFRA.repoBlob + path, target: '_blank', rel: 'noopener' }, path));
    if (location.protocol === 'file:') {
      const root = decodeURIComponent(location.pathname).replace(/\/docs\/usage-map\/[^/]*$/, '');
      wrapEl.append(htmlEl('a', { href: `vscode://file${root}/${path}`, class: 'editor', title: 'Open in VS Code' }, 'edit'));
    }
    return wrapEl;
  }
  function section(title, ...children) {
    const s = htmlEl('section', { class: 'p-sec' });
    s.append(htmlEl('h3', {}, title), ...children.filter(Boolean));
    return s;
  }
  function list(children) {
    const ul = htmlEl('ul', { class: 'p-list' });
    for (const c of children) {
      const li = htmlEl('li');
      if (Array.isArray(c)) li.append(...c.filter((x) => x !== '' && x !== null && x !== undefined));
      else li.append(c);
      ul.append(li);
    }
    return ul;
  }
  const chip = (t, cls = '') => htmlEl('span', { class: `chip ${cls}` }, t);
  function chips(values, cls = 'mono') {
    const c = htmlEl('div', { class: 'chips' });
    c.append(...values.map((v) => chip(v, cls)));
    return c;
  }
  function connections(it) {
    const own = shownEdges(it);
    if (!own.length) return null;
    const rows = own.slice(0, 60).map((e) => {
      const out = ancestry(e.from).some((a) => a.id === it.id);
      return [htmlEl('span', { class: 'dir' }, out ? '→' : '←'), link(out ? e.to : e.from), e.label ? htmlEl('span', { class: 'muted' }, ` ${e.label}`) : ''];
    });
    return section(`Connections (${own.length})`, list(rows), own.length > 60 ? htmlEl('p', { class: 'muted' }, `…and ${own.length - 60} more`) : null);
  }
  // Stories (stories-data.js) that name this box: a persona's own stories, or any story with
  // a step on the box. Each links to its storyboard on the "Who uses it" tab.
  const STORIES = window.USAGE_MAP_STORIES || {};
  const PEOPLE = new Map(((STORIES.org && STORIES.org.people) || []).map((p) => [p.id, p]));
  function storiesFor(id) {
    return (STORIES.stories || []).filter((st) => {
      const person = PEOPLE.get(st.person);
      return (person && (person.personas || []).includes(id)) || (st.steps || []).some((x) => x.node === id);
    });
  }
  function storyLink(st) {
    const person = PEOPLE.get(st.person);
    return htmlEl('a', { class: 'linkish', href: `#who/${st.id}` }, `${person ? `${person.name}: ` : ''}${st.title}`);
  }

  function renderPanel(it) {
    panelBody.textContent = '';
    panel.classList.add('open');
    const head = htmlEl('header', { class: `p-head tone-${it.tone || 'core'}` });
    head.append(htmlEl('span', { class: 'p-kind' }, kindLabel(it)), htmlEl('h2', {}, it.title));
    panelBody.append(head);
    const d = it.data || {};
    if (it.kind === 'root') return renderOverviewPanel();
    if (isPlanned(d)) panelBody.append(htmlEl('p', { class: 'planned-note' }, `Planned, not built yet.${d.plannedIn ? ` See ${d.plannedIn}.` : ''}`));
    if (d.desc || d.blurb) panelBody.append(htmlEl('p', { class: 'p-desc' }, d.desc || d.blurb));
    if (d.usage) panelBody.append(section('Use it', htmlEl('pre', { class: 'code' }, d.usage)));
    if (d.wants) panelBody.append(section('What they want', htmlEl('p', { class: 'p-plain' }, d.wants)));
    const sts = storiesFor(it.id);
    if (sts.length) panelBody.append(section(it.kind === 'persona' ? `Their stories (${sts.length})` : `In stories (${sts.length})`, list(sts.map((st) => [storyLink(st)]))));
    if (d.facts && d.facts.length) panelBody.append(section('How it works', list(d.facts)));
    if (d.flags && d.flags.length) {
      panelBody.append(section(`Options (${d.flags.length})`, list(d.flags.map((f) => [chip(f.name, 'mono'), f.desc ? htmlEl('span', { class: 'muted' }, ` ${f.desc}`) : '']))));
    }
    if (d.chips && d.chips.length) panelBody.append(section(d.chipsLabel || 'Details', chips(d.chips)));
    if (d.files && d.files.length) panelBody.append(section('Where in the code', list(d.files.map(fileLink))));
    if (d.file) panelBody.append(section('Where in the code', list([fileLink(d.file)])));
    if (it.kind === 'cluster' || it.kind === 'group') {
      const rows = it.children.map((cid) => {
        const k = items.get(cid);
        const extra = k.kind === 'group' ? ` · ${k.data.items.length}` : k.data.detail ? ` · ${k.data.detail}` : k.data.sub ? ` · ${k.data.sub}` : '';
        return [link(k.id), htmlEl('span', { class: 'muted' }, extra), isPlanned(k.data) ? chip('planned', 'flag') : ''];
      });
      if (rows.length) panelBody.append(section(`Inside (${rows.length})`, list(rows)));
    }
    const conn = connections(it);
    if (conn) panelBody.append(conn);
  }

  function renderOverviewPanel() {
    panelBody.append(htmlEl('p', { class: 'p-desc' }, INFRA.summary));
    const personas = [...items.values()].filter((x) => x.kind === 'persona');
    if (personas.length) panelBody.append(section('Who uses it', list(personas.map((p) => [link(p.id), htmlEl('span', { class: 'muted' }, ` ${p.data.sub || ''}`)]))));
    if ((STORIES.stories || []).length) panelBody.append(section('See it in a story', list(STORIES.stories.map((st) => [storyLink(st)]))));
    panelBody.append(section('How to use this map', list(INFRA.howTo)));
    panelBody.append(section('Areas', list(INFRA.clusters.map((c) => [link(c.id), htmlEl('span', { class: 'muted' }, ` ${c.subtitle}`), isPlanned(c) ? chip('planned', 'flag') : '']))));
  }

  // ── search ─────────────────────────────────────────────────────────────────
  const search = document.getElementById('search');
  const results = document.getElementById('results');
  const index = [...items.values()].filter((it) => it.kind !== 'root').map((it) => ({
    id: it.id,
    title: it.title,
    hay: `${it.title} ${it.data?.sub || ''} ${it.data?.detail || ''} ${(it.data?.chips || []).join(' ')} ${(it.data?.flags || []).map((f) => f.name).join(' ')}`.toLowerCase(),
    kind: kindLabel(it),
  }));
  let activeResult = 0;
  let currentResults = [];
  function runSearch() {
    const q = search.value.trim().toLowerCase();
    results.textContent = '';
    if (!q) {
      results.hidden = true;
      return;
    }
    const scored = [];
    for (const e of index) {
      const t = e.title.toLowerCase();
      let score = -1;
      if (t === q) score = 0;
      else if (t.startsWith(q)) score = 1;
      else if (t.includes(q)) score = 2;
      else if (e.hay.includes(q)) score = 3;
      if (score >= 0) scored.push([score, e]);
    }
    scored.sort((a, b) => a[0] - b[0] || a[1].title.length - b[1].title.length);
    currentResults = scored.slice(0, 40).map((s) => s[1]);
    activeResult = 0;
    if (!currentResults.length) results.append(htmlEl('li', { class: 'muted empty' }, 'No match'));
    currentResults.forEach((r, i) => {
      const li = htmlEl('li', { role: 'option', 'aria-selected': String(i === 0), onmousedown: (ev) => { ev.preventDefault(); choose(i); } });
      li.append(htmlEl('b', {}, r.title), htmlEl('span', {}, r.kind));
      results.append(li);
    });
    results.hidden = false;
  }
  function choose(i) {
    const r = currentResults[i];
    if (!r) return;
    results.hidden = true;
    search.blur();
    select(r.id, { fly: true });
  }
  search.addEventListener('input', runSearch);
  search.addEventListener('focus', runSearch);
  search.addEventListener('blur', () => setTimeout(() => (results.hidden = true), 120));
  search.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      activeResult = Math.max(0, Math.min(currentResults.length - 1, activeResult + (e.key === 'ArrowDown' ? 1 : -1)));
      [...results.children].forEach((li, i) => li.setAttribute('aria-selected', String(i === activeResult)));
      results.children[activeResult]?.scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'Enter') choose(activeResult);
    else if (e.key === 'Escape') search.blur();
  });

  // ── minimap ────────────────────────────────────────────────────────────────
  const mini = document.getElementById('minimap');
  mini.setAttribute('viewBox', `0 0 ${W} ${H}`);
  for (const c of INFRA.clusters) svgEl('rect', { x: c.x, y: c.y, width: c.w, height: c.h, rx: 30, class: `mm-c tone-${c.tone}` }, mini);
  const miniView = svgEl('rect', { class: 'mm-view', rx: 12 }, mini);
  function updateMinimap() {
    const s = size();
    const vw = s.w * cam.upp;
    const vh = s.h * cam.upp;
    miniView.setAttribute('x', cam.cx - vw / 2);
    miniView.setAttribute('y', cam.cy - vh / 2);
    miniView.setAttribute('width', vw);
    miniView.setAttribute('height', vh);
  }
  const miniJump = (e) => {
    const box = mini.getBoundingClientRect();
    const scale = Math.max(W / box.width, H / box.height);
    cam.cx = (e.clientX - box.left - (box.width - W / scale) / 2) * scale;
    cam.cy = (e.clientY - box.top - (box.height - H / scale) / 2) * scale;
    applyCam();
  };
  mini.addEventListener('pointerdown', (e) => {
    mini.setPointerCapture(e.pointerId);
    stopAnim();
    miniJump(e);
  });
  mini.addEventListener('pointermove', (e) => {
    if (e.buttons) miniJump(e);
  });

  // ── legend, controls and keys ──────────────────────────────────────────────
  const key = document.getElementById('key-rows');
  for (const l of INFRA.legend || []) {
    if (l.key === false) continue; // colors items, but stays out of the on-screen key
    const s = htmlEl('span');
    s.append(htmlEl('i', { class: 'dot', style: `background:${l.color}` }), l.label);
    key.append(s);
  }
  const plannedKey = htmlEl('span');
  plannedKey.append(htmlEl('i', { class: 'dot planned-dot' }), 'planned');
  key.append(plannedKey);
  const derivedToggle = document.getElementById('toggle-derived');
  document.getElementById('derived-label').textContent = INFRA.derivedLabel || 'Generated links';
  if (!(DATA.edges || []).length) derivedToggle.closest('label').hidden = true;
  derivedToggle.addEventListener('change', () => svg.classList.toggle('show-derived', derivedToggle.checked));

  const zoomReadout = document.getElementById('zoom-level');
  document.getElementById('zoom-in').addEventListener('click', () => zoomAt(size().w / 2, size().h / 2, 1.8));
  document.getElementById('zoom-out').addEventListener('click', () => zoomAt(size().w / 2, size().h / 2, 1 / 1.8));
  document.getElementById('zoom-fit').addEventListener('click', () => {
    select('root');
    flyTo(items.get('root').rect, 1);
  });
  document.getElementById('panel-close').addEventListener('click', () => panel.classList.remove('open'));
  document.getElementById('panel-open').addEventListener('click', () => panel.classList.add('open'));
  document.addEventListener('keydown', (e) => {
    if ((e.target.matches && e.target.matches('input, textarea')) || !shown()) return;
    if (e.key === '/') {
      e.preventDefault();
      search.focus();
    } else if (e.key === 'Escape') {
      const it = selectedId ? items.get(selectedId) : null;
      select(it && it.parent ? it.parent : 'root', { fly: true });
    } else if (e.key === '0') document.getElementById('zoom-fit').click();
    else if (e.key === '+' || e.key === '=') document.getElementById('zoom-in').click();
    else if (e.key === '-') document.getElementById('zoom-out').click();
  });
  const root = document.documentElement;
  try {
    const saved = localStorage.getItem('usage-map-theme');
    if (saved) root.setAttribute('data-theme', saved);
  } catch {
    /* storage blocked */
  }
  document.getElementById('theme').addEventListener('click', () => {
    const dark = root.getAttribute('data-theme') ? root.getAttribute('data-theme') === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches;
    const next = dark ? 'light' : 'dark';
    root.setAttribute('data-theme', next);
    try {
      localStorage.setItem('usage-map-theme', next);
    } catch {
      /* storage blocked */
    }
  });

  // ── header stats ───────────────────────────────────────────────────────────
  const stats = document.getElementById('stats');
  for (const s of [...(DATA.stats || []), ...(INFRA.stats || [])]) {
    const b = htmlEl('button', { type: 'button', class: 'stat-chip', onclick: () => select(items.has(s.target) ? s.target : 'root', { fly: true }) });
    b.append(htmlEl('b', {}, fmt(s.n)), htmlEl('span', {}, s.label));
    stats.append(b);
  }
  document.getElementById('gen-info').textContent = `Generated parts read from the code on ${DATA.generatedAt.slice(0, 10)} at ${DATA.commit}. Regenerate and reopen: ${INFRA.generateCommand}`;

  // ── start ──────────────────────────────────────────────────────────────────
  // stories.js owns the hash. It calls show() each time the "How it's built" tab appears,
  // because the SVG has no size while another tab is shown.
  let fitted = false;
  let shownOnce = false;
  select('root');
  window.USAGE_MAP = {
    has: (id) => items.has(id),
    select: (id) => select(items.has(id) ? id : 'root', { fly: true }),
    show(id) {
      stopAnim();
      cam.upp = fitUpp();
      if (!fitted) cam.cy = H / 2 + 80;
      fitted = true;
      applyCam();
      const first = !shownOnce;
      shownOnce = true;
      const target = id && items.has(id) ? id : selectedId || 'root';
      if (target === 'root') {
        select('root');
        flyTo(items.get('root').rect, 1);
      } else select(target, { fly: true });
      // On a phone the details sheet starts closed, so the map is visible first.
      if (first && !id && window.innerWidth < 760) panel.classList.remove('open');
    },
  };
  if (window.innerWidth < 760) panel.classList.remove('open');
})();
