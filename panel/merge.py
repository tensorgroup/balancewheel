"""merge.py — parse seat outputs, group findings, find candidate disputes, build findings.json.

Pure functions. Seat output is hostile input: bw never executes it, bounds every size, and shows
control characters as escapes. bw never grades a finding; the lead does.
"""
import json
import re

from lead import paths  # noqa: F401  (puts metrics/ on sys.path)
from lead.guard.review import safe
import log
import watches

MAX_PER_SEAT = 50
TITLE_CHARS = 200
WHERE_CHARS = 200
CLAIM_CHARS = 1500
LINE_WINDOW = 5
JACCARD = 0.5
RANK = {"critical": 0, "major": 1, "minor": 2, "nit": 3}
UNPARSED_TITLE = "unparsed output"
MAX_BLOCK_CHARS = 1_000_000


def _blocks(text):
    """(bodies, discarded): the bodies of fenced blocks that open with json or no info string, and the
    count of such blocks that never closed. A fence line with an info string always opens a block, so a
    stray fence cannot hide a later block. A bare fence closes the open block, or opens one."""
    out, cur, open_, wanted, discarded = [], [], False, False, 0
    for line in text.split("\n"):
        if line.startswith("```"):
            info = line[3:].strip().lower()
            if info:
                if open_ and wanted:
                    discarded += 1
                open_, wanted, cur = True, info == "json", []
            elif open_:
                if wanted:
                    out.append("\n".join(cur))
                open_, cur = False, []
            else:
                open_, wanted, cur = True, True, []
        elif open_ and wanted:
            cur.append(line)
    if open_ and wanted:
        discarded += 1
    return out, discarded


def _text(value, limit):
    return safe(value if isinstance(value, str) else "")[:limit]


def _entry(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("title"), str) or not raw["title"]:
        return None
    sev = raw.get("severity")
    sev = sev.lower() if isinstance(sev, str) else ""
    return {"severity": sev if sev in log.SEVERITIES else "minor",
            "where": _text(raw.get("where"), WHERE_CHARS),
            "title": _text(raw["title"], TITLE_CHARS),
            "claim": _text(raw.get("claim"), CLAIM_CHARS)}


def parse_detail(text):
    """(entries, lost): the entries of the last fenced block that parses to a JSON list (None when no
    block does), and counts of what bw could not use: skipped_blocks, skipped_entries, truncated."""
    lost = {"skipped_blocks": 0, "skipped_entries": 0, "truncated": 0}
    if not isinstance(text, str):
        return None, lost
    bodies, lost["skipped_blocks"] = _blocks(text)
    found = None
    for body in bodies:
        data = None
        if len(body) <= MAX_BLOCK_CHARS:
            try:
                data = json.loads(body)
            except (ValueError, RecursionError):
                pass
        if isinstance(data, list):
            found = data
        else:
            lost["skipped_blocks"] += 1
    if found is None:
        return None, lost
    out = []
    for raw in found:
        e = _entry(raw)
        if e is None:
            lost["skipped_entries"] += 1
        elif len(out) >= MAX_PER_SEAT:
            lost["truncated"] += 1
        else:
            out.append(e)
    return out, lost


def parse(text):
    """The entries of the last fenced block that parses to a JSON list, or None."""
    return parse_detail(text)[0]


def _number(outputs, order):
    out, losses = [], {}
    for seat in order:
        entries, lost = parse_detail(outputs.get(seat))
        if any(lost.values()):
            losses[seat] = {k: v for k, v in lost.items() if v}
        if entries is None:
            entries = [{"severity": "minor", "where": "", "title": UNPARSED_TITLE,
                        "claim": f"bw found no JSON block; read seats/{seat}.md"}]
        for e in entries:
            out.append({"id": f"f{len(out) + 1}", "seat": seat, "severity": e["severity"],
                        "where": e["where"], "title": e["title"], "claim": e["claim"]})
    return out, losses


def number(outputs, order):
    """Findings with ids f1... in seat order, then block order."""
    return _number(outputs, order)[0]


def _loc(where):
    path, sep, line = where.rpartition(":")
    if sep and path and line.isdigit():
        return path, int(line)
    return None


def _words(title):
    return set(w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) >= 3)


def _near(a, b):
    la, lb = _loc(a["where"]), _loc(b["where"])
    if la and lb and la[0] == lb[0] and abs(la[1] - lb[1]) <= LINE_WINDOW:
        return True
    wa, wb = _words(a["title"]), _words(b["title"])
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) >= JACCARD


def group(findings):
    """Union-find over the near test; group ids follow the first member's finding order."""
    parent = list(range(len(findings)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(findings)):
        for j in range(i + 1, len(findings)):
            if _near(findings[i], findings[j]):
                a, b = find(i), find(j)
                if a != b:
                    parent[max(a, b)] = min(a, b)
    members = {}
    for i, f in enumerate(findings):
        members.setdefault(find(i), []).append(f)
    return [{"group": f"g{n}", "title": m[0]["title"], "members": m}
            for n, (_, m) in enumerate(sorted(members.items()), 1)]


def candidate_disputes(groups):
    out = []
    for g in groups:
        ms = g["members"]
        if len({m["seat"] for m in ms}) < 2:
            continue
        ranks = [RANK.get(m["severity"], RANK["minor"]) for m in ms]
        if max(ranks) - min(ranks) >= 2:
            hi = next(k for k, v in RANK.items() if v == min(ranks))
            lo = next(k for k, v in RANK.items() if v == max(ranks))
            out.append({"id": f"d{len(out) + 1}", "group": g["group"], "reason": f"severity {hi} vs {lo}"})
    return out


def triage_required(findings_raw, seat_count, watches_cfg):
    return watches.is_bloated(findings_raw, seat_count, watches_cfg)


def build(meta, outputs, seats, absent, short, short_reasons, watches_cfg, failed=None):
    """The findings.json document. `outputs` holds only seats whose status is ok. A seat with output
    but no entry in `seats` is still merged and marked `unexpected_output`. A seat whose findings bw
    could not all use gets a `lost` entry (skipped_blocks, skipped_entries, truncated)."""
    seats = {s: dict(v) for s, v in seats.items()}
    order = [s for s in seats if s in outputs]
    for s in outputs:
        if s not in seats:
            seats[s] = {"status": "ok", "on_roster": False, "unexpected_output": True}
            order.append(s)
    findings, losses = _number(outputs, order)
    for s, lost in losses.items():
        seats[s]["lost"] = lost
    groups = group(findings)
    return {"panel_id": meta["panel_id"], "checkpoint": meta["checkpoint"], "target": meta["target"],
            "tier": meta["tier"], "short": short, "short_reasons": short_reasons, "failed": failed,
            "seats": seats, "absent": absent, "groups": groups,
            "candidate_disputes": candidate_disputes(groups), "findings_raw": len(findings),
            "triage_required": triage_required(len(findings), len(order), watches_cfg)}
