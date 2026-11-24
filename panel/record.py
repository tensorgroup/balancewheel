"""record.py — the `panel` record from bw's findings.json plus the lead's verdicts.

Pure functions. bw fills every mechanical field; the lead sends only its judgement (REQUEST_KEYS).
Lead text is hostile input: every string is escaped with safe() and cut to a bound. Every shape fault
is a ValueError with one message the lead can read in its response.
"""
import copy
import re

from lead import paths  # noqa: F401  (puts metrics/ on sys.path)
from lead.guard.review import safe
from panel import merge
import log

REQUEST_KEYS = ("verdicts", "groups", "severity", "added", "disputes", "notes")
GROUP_ID = re.compile(r"[A-Za-z0-9_-]{1,32}")
SUMMARY_CHARS = 500
REASON_CHARS = 500
PROPOSAL_CHARS = 1500
NOTES_CHARS = 2000


NOTE_CHARS = 200
_cut = merge.cut


def _unscored(doc):
    return {s for s, v in doc["seats"].items() if v.get("unexpected_output") and not v.get("on_roster", True)}


def _bw_notes(doc, unscored):
    out = []
    for s, v in doc["seats"].items():
        name = _cut(s, 40)
        if s in unscored:
            out.append(f"seat {name} ran off-roster; not scored")
            continue
        lost = v.get("lost")
        if not isinstance(lost, dict):
            continue
        bits = [f"{lost[k]} {text}" for k, text in (("skipped_blocks", "blocks skipped"),
                ("skipped_entries", "entries skipped"), ("truncated", "findings over the cap"))
                if isinstance(lost.get(k), int) and lost[k] > 0]
        if bits:
            out.append(f"seat {name}: " + ", ".join(bits))
    return [_cut(n, NOTE_CHARS) for n in out]


def _str_map(req, key):
    value = req.get(key, {})
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise ValueError(f"{key} must be an object of string to string")
    return value


def _entries(req, key):
    value = req.get(key, [])
    if not isinstance(value, list) or not all(isinstance(x, dict) for x in value):
        raise ValueError(f"{key} must be a list of objects")
    return value


def _need(entry, label, keys):
    for k in keys:
        if k not in entry:
            raise ValueError(f"{label} missing {k}")
        if not isinstance(entry[k], str):
            raise ValueError(f"{label}.{k} must be a string")


def _ok_seat(seat, ok, label):
    if seat not in ok:
        raise ValueError(f"{label}: {safe(seat)[:40]} is not a seat that ran")


def _added(req, ok):
    out = []
    for i, a in enumerate(_entries(req, "added")):
        label = f"added[{i}]"
        _need(a, label, ("seat", "title", "severity", "verdict"))
        for k in ("where", "group"):
            if k in a and not isinstance(a[k], str):
                raise ValueError(f"{label}.{k} must be a string")
        _ok_seat(a["seat"], ok, label)
        if a["severity"] not in log.SEVERITIES:
            raise ValueError(f"{label}.severity must be one of {list(log.SEVERITIES)}")
        if a["verdict"] not in log.VERDICTS:
            raise ValueError(f"{label}.verdict must be one of {list(log.VERDICTS)}")
        group = a.get("group") or f"ga{i + 1}"
        if not GROUP_ID.fullmatch(group):
            raise ValueError(f"{label}.group is not a valid group id")
        out.append({"id": f"a{i + 1}", "seat": a["seat"], "group": group,
                    "title": _cut(a["title"], merge.TITLE_CHARS), "severity": a["severity"],
                    "verdict": a["verdict"], "where": _cut(a.get("where", ""), merge.WHERE_CHARS)})
    return out


def _disputes(req, ok):
    out = []
    for i, d in enumerate(_entries(req, "disputes")):
        label = f"disputes[{i}]"
        _need(d, label, ("summary", "challenger", "winner", "reason"))
        if "proposals" not in d:
            raise ValueError(f"{label} missing proposals")
        p = d["proposals"]
        if not isinstance(p, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in p.items()):
            raise ValueError(f"{label}.proposals must be an object of string seat to string proposal")
        for k in ("challenger", "winner"):
            _ok_seat(d[k], ok, f"{label}.{k}")
        for k in p:
            _ok_seat(k, ok, f"{label}.proposals")
        out.append({"summary": _cut(d["summary"], SUMMARY_CHARS), "challenger": d["challenger"],
                    "proposals": {k: _cut(v, PROPOSAL_CHARS) for k, v in p.items()},
                    "winner": d["winner"], "reason": _cut(d["reason"], REASON_CHARS)})
    return out


def _detail(doc, verdicts, groups, severity, unscored):
    every = [dict(m, group=g["group"]) for g in doc["groups"] for m in g["members"]]
    ids = {m["id"] for m in every}
    base = sorted((m for m in every if m["seat"] not in unscored), key=lambda m: int(m["id"][1:]))
    unknown = sorted({k for src in (verdicts, groups, severity) for k in src} - ids)
    if unknown:
        raise ValueError("unknown finding ids: " + ", ".join(safe(u)[:40] for u in unknown))
    missing = [m["id"] for m in base if m["id"] not in verdicts]
    if missing:
        raise ValueError("missing verdicts: " + ", ".join(missing))
    for fid, v in verdicts.items():
        if v not in log.VERDICTS:
            raise ValueError(f"verdict for {fid} must be one of {list(log.VERDICTS)}")
    for fid, s in severity.items():
        if s not in log.SEVERITIES:
            raise ValueError(f"severity for {fid} must be one of {list(log.SEVERITIES)}")
    for fid, g in groups.items():
        if not GROUP_ID.fullmatch(g):
            raise ValueError(f"group for {fid} is not a valid group id")
    return [{"id": m["id"], "seat": m["seat"], "group": groups.get(m["id"], m["group"]),
             "title": m["title"], "severity": severity.get(m["id"], m["severity"]),
             "verdict": verdicts[m["id"]], "where": m.get("where", "")} for m in base]


def build(doc, req, config, moderator=None, rounds=1):
    """(record, ignored keys). Raises ValueError for any fault in the lead's request."""
    if not isinstance(req, dict):
        raise ValueError("request must be an object")
    verdicts, groups, severity = (_str_map(req, k) for k in ("verdicts", "groups", "severity"))
    notes = req.get("notes", "")
    if not isinstance(notes, str):
        raise ValueError("notes must be a string")
    unscored = _unscored(doc)
    ok = {s: v for s, v in doc["seats"].items() if v.get("status") == "ok" and s not in unscored}
    detail = _detail(doc, verdicts, groups, severity, unscored) + _added(req, ok)
    disputes = _disputes(req, ok)
    seats = {}
    for s, v in ok.items():
        model = v.get("model") or config.get("seats", {}).get(s, {}).get("model")
        seats[s] = model if isinstance(model, str) else None
    rec = {"target": safe(doc["target"]), "kind": doc["checkpoint"], "tier": doc["tier"], "seats": seats,
           "absent": dict(doc["absent"]), "rounds": rounds,
           "immediate_agreement": not disputes and rounds == 1, "findings_raw": doc["findings_raw"]}
    if doc.get("triage_required"):
        rec["findings_after_triage"] = sum(1 for f in detail if f["verdict"] != "dropped")
    notes = " | ".join([n for n in [_cut(notes, NOTES_CHARS)] if n] + _bw_notes(doc, unscored))
    rec.update({"findings_detail": detail, "findings": log.compute_counts(detail), "disputes": disputes,
                "notes": notes})
    if moderator:
        rec["moderator"] = moderator
    return rec, [k for k in req if k not in REQUEST_KEYS]


def summary_line(rec, doc):
    tier = "Deep " if rec.get("tier") == "deep" else ""
    if doc.get("failed"):
        status = f", failed: {safe(doc['failed'])}"
    else:
        status = ", short" if doc.get("short") else ""
    unscored = _unscored(doc)
    ran = [s for s, v in doc["seats"].items() if v.get("status") == "ok" and s not in unscored]
    names = [f"{s} for {doc['seats'][s]['stand_in_for']}"
             if doc["seats"][s].get("stand_in_for") and not doc["seats"][s].get("on_roster", True) else s
             for s in ran]
    absent = ", ".join(f"{s} absent: {why}" for s, why in doc["absent"].items())
    if names:
        inner = ", ".join(names) + (f"; {absent}" if absent else "")
    else:
        inner = "no seat returned" + (f"; {absent}" if absent else "")
    verdicts = [f["verdict"] for f in rec["findings_detail"]]
    partial = verdicts.count("partial")
    parts = [f"{len(verdicts)} findings", f"{verdicts.count('confirmed')} confirmed"]
    if partial:
        parts.append(f"{partial} partial")
    parts += [f"{verdicts.count('refuted')} refuted", f"{verdicts.count('dropped')} dropped"]
    return f"{tier}{rec['kind'].capitalize()} panel{status} ({inner}): {', '.join(parts)}."


def judged(req):
    return {k: req[k] for k in REQUEST_KEYS if k in req}


def merge_patch(old, patch):
    new = copy.deepcopy(old)
    for k in ("verdicts", "groups", "severity"):
        if k in patch:
            if not isinstance(patch[k], dict):
                raise ValueError(f"{k} must be an object of string to string")
            new[k] = {**new.get(k, {}), **patch[k]}
    for k in ("added", "notes"):
        if k in patch:
            new[k] = copy.deepcopy(patch[k])
    return new
