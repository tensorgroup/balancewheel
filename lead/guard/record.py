"""record.py — the guard's decision log: one locked JSONL, written only by bw's own code.

A decision row is the broker's; a mark row is `bw guard review`'s. Both go through append(), which
creates the file 0600 first (store.append alone would create it at the umask). load() folds marks
into their decisions and reports marks whose decision is missing instead of dropping them.
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))), "metrics"))
import store  # noqa: E402

FILE = "guard.jsonl"


def path(state_dir):
    return os.path.join(state_dir, FILE)


def append(state_dir, event):
    p = path(state_dir)
    os.makedirs(state_dir, exist_ok=True)
    os.close(os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600))
    store.append(p, event)


def load(state_dir):
    events, warnings = store.read_events(path(state_dir))
    decisions = [dict(e) for e in events if e["type"] == "decision"]
    by_id = {d.get("id"): d for d in decisions}
    marks, orphans = [], []
    for m in (e for e in events if e["type"] == "mark"):
        marks.append(m)
        d = by_id.get(m.get("decision_id"))
        if d is None:
            orphans.append(m)
        else:
            d["mark"] = {"verdict": m.get("verdict"), "sampled": m.get("sampled")}
    errors = [e for e in events if e["type"] == "error"]
    return {"decisions": decisions, "marks": marks, "orphan_marks": orphans, "errors": errors,
            "warnings": warnings}


def policy_digest(protected_branches, deploy_commands):
    blob = json.dumps([sorted(protected_branches or []), sorted(deploy_commands or [])], separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def evidence_key(decision):
    """Evidence about the rules resets when any part of this changes."""
    return tuple(decision.get(k) for k in ("profile", "runtime", "runtime_version", "rules_version", "policy_digest"))
