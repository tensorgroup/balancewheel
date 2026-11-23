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
    """Fenced blocks with an info string of json or none. A fence line inside an open block closes it."""
    out, cur, open_ = [], [], False
    for line in text.split("\n"):
        if line.startswith("```"):
            if open_:
                out.append("\n".join(cur))
                cur, open_ = [], False
            else:
                open_ = line[3:].strip().lower() in ("json", "")
                cur = []
        elif open_:
            cur.append(line)
    return out


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


def parse(text):
    """The entries of the last fenced block that parses to a JSON list, or None."""
    if not isinstance(text, str):
        return None
    found = None
    for body in _blocks(text):
        if len(body) > MAX_BLOCK_CHARS:
            continue
        try:
            data = json.loads(body)
        except (ValueError, RecursionError):
            continue
        if isinstance(data, list):
            found = data
    if found is None:
        return None
    out = []
    for raw in found:
        e = _entry(raw)
        if e:
            out.append(e)
            if len(out) >= MAX_PER_SEAT:
                break
    return out


def number(outputs, order):
    """Findings with ids f1... in seat order, then block order."""
    out = []
    for seat in order:
        entries = parse(outputs.get(seat))
        if entries is None:
            entries = [{"severity": "minor", "where": "", "title": UNPARSED_TITLE,
                        "claim": f"bw found no JSON block; read seats/{seat}.md"}]
        for e in entries:
            out.append({"id": f"f{len(out) + 1}", "seat": seat, "severity": e["severity"],
                        "where": e["where"], "title": e["title"], "claim": e["claim"]})
    return out


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
    """The findings.json document. `outputs` holds only seats whose status is ok."""
    order = [s for s in seats if s in outputs]
    findings = number(outputs, order)
    groups = group(findings)
    return {"panel_id": meta["panel_id"], "checkpoint": meta["checkpoint"], "target": meta["target"],
            "tier": meta["tier"], "short": short, "short_reasons": short_reasons, "failed": failed,
            "seats": seats, "absent": absent, "groups": groups,
            "candidate_disputes": candidate_disputes(groups), "findings_raw": len(findings),
            "triage_required": triage_required(len(findings), len(order), watches_cfg)}
