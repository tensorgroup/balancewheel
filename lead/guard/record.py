"""record.py — the guard's decision log: one locked JSONL, written only by bw's own code.

A decision row is the broker's: `decision` is the final decision, `rule_decision` the rules' own, and
`source` says who decided (`rule`, `judge`, `judge-failed`, `judge-skipped`; a legacy row says `gray`).
A mark row is `bw guard review`'s. An outcome row is the broker's, when a client gave up before the
reply (`client-timeout`). All go through append(), which
creates the file 0600 first (store.append alone would create it at the umask). load() folds marks
and outcomes into their decisions (an outcome replaces `outcome` and keeps the first as
`outcome_first`), and reports marks or outcomes whose decision is missing instead of dropping them.
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
    orphan_outcomes = []
    for o in (e for e in events if e["type"] == "outcome"):
        d = by_id.get(o.get("decision_id"))
        if d is None:
            orphan_outcomes.append(o)
        else:
            d.setdefault("outcome_first", d.get("outcome"))
            d["outcome"] = o.get("outcome")
    errors = [e for e in events if e["type"] == "error"]
    return {"decisions": decisions, "marks": marks, "orphan_marks": orphans, "orphan_outcomes": orphan_outcomes,
            "errors": errors, "warnings": warnings}


def policy_digest(protected_branches, deploy_commands):
    blob = json.dumps([sorted(protected_branches or []), sorted(deploy_commands or [])], separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


EVIDENCE_KEY = ("profile", "runtime", "rules_version", "policy_digest", "judge_model", "prompt_hash")


def evidence_key(decision):
    """Evidence about the guard resets when any part of this changes. The runtime version is not a part:
    an update of the lead's CLI changes no rule and no judge."""
    return tuple(decision.get(k) for k in EVIDENCE_KEY)


def is_gray(decision):
    """The rules did not decide this call. A legacy row has source "gray"; it is gray too."""
    return decision.get("source") != "rule"


def rule_decision(decision):
    """The rules' own decision. A legacy row has no rule_decision: its decision was the rules'."""
    return decision.get("rule_decision", decision.get("decision"))
