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
