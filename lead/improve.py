"""improve.py — what bw has seen of how the user works, turned into changes worth making.

Balancewheel's standing goal is that working with it gets easier, faster, and better the more it is
used. Everything here reads what is already recorded: triage verdicts, decisions the user reversed,
questions the user only agreed with, what shadow mode would have done against what the user did,
ratings, cap switches, leads forced by hand, seats that missed panels, and the `@improve` notes
leads publish. Each rule has a minimum sample, so a thin log says nothing rather than a guess.

Evidence counts for WINDOW_S, and after `bw improve done <key>` only evidence newer than that
counts, so a suggestion dealt with stays gone unless it happens again. Agreement alone never argues
for weakening the outward or high-stakes gates: being fine with an answer does not show that
asking was unnecessary. Suggestions never change anything; config and protocol edits are the user's.
"""
import os
import re
from datetime import datetime, timezone

from . import triage
from .paths import read_json, write_json_atomic

DONE_FILE = "improve.json"
WINDOW_S = 30 * 86400
MIN_SAMPLE = 5          # decisions or questions before a rate means anything
MIN_REPEATS = 3         # the same thing happening before it is a pattern
REVERSED_RATE = 0.2
STAMPED_RATE = 0.6
SKIPPED_RATE = 0.3
SHADOW_READY_RATE = 0.8
PANELS_SEEN = 5
_LOOSENABLE = ("strong_case", "only_person_knows")
_STAMP = re.compile(r"(yes|yep|yeah|ok|okay|sure|agree[d]?|go|go ahead|go for it|do it|sounds good|looks good|lgtm|fine"
                    r"|recommended|your recommendation|go with (it|that|your recommendation)|use (it|that)"
                    r"|what you recommend)")


def rubber_stamp(text):
    """A short reply that only agrees: the question did not need asking."""
    parts = [p.strip() for p in re.split(r"[,.!;]+", str(text).lower())]
    parts = [p for p in parts if p]
    return bool(parts) and len(parts) <= 3 and all(_STAMP.fullmatch(p) for p in parts)


def _when(ts):
    """Epoch seconds from a float or an ISO string; 0 for anything else, which no window admits."""
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        return ts
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return 0
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).timestamp()


class _Rules:
    def __init__(self, data, items, now, done, thresholds):
        self.data, self.items, self.now, self.done = data, items, now, done
        self.thresholds = thresholds or triage.DEFAULT_THRESHOLDS

    def fresh(self, key, ts):
        return _when(ts) > max(self.now - WINDOW_S, self.done.get(key, 0))

    def triage_events(self, key):
        return [e for e in self.data["events"] if e.get("type") == "triage" and self.fresh(key, e.get("ts"))]

    def reversed_(self):
        key = "triage-reversed"
        decided = [i for i in self.items if self.fresh(key, (i.get("answer") or {}).get("ts")) and
                   (i.get("reversed") or (i.get("status") == "answered" and i["answer"].get("from") == "triage"
                                          and not i["answer"].get("shadow")))]
        n = sum(1 for i in decided if i.get("reversed"))
        if len(decided) >= MIN_SAMPLE and n / len(decided) >= REVERSED_RATE:
            yield key, f"you reversed {n} of {len(decided)} decisions made without you", "tighten triage.thresholds"

    def stamped(self):
        for gate in _LOOSENABLE:
            key = f"triage-rubber-stamped:{gate}"
            stopped_here = {}
            for e in self.triage_events(key):
                scores = e.get("scores")
                if e.get("verdict") == "ask" and not e.get("failed") and isinstance(scores, dict) \
                        and triage.failing(scores, self.thresholds) == [gate]:  # loosening this alone would have decided it
                    stopped_here[e.get("id")] = True
            answers = [i["answer"].get("text") for i in self.items if i.get("id") in stopped_here
                       and i.get("status") == "answered" and i["answer"].get("from") == "user"]
            n = sum(1 for t in answers if rubber_stamp(t))
            if len(answers) >= MIN_SAMPLE and n / len(answers) >= STAMPED_RATE:
                yield key, f"you only agreed with {n} of {len(answers)} questions stopped by {gate} alone", \
                    f"loosen triage.thresholds.{gate} a little"

    def shadow(self):
        key = "triage-shadow"
        answers = [i["answer"].get("text") for i in self.items
                   if (i.get("triage") or {}).get("verdict") == "decide" and i.get("status") == "answered"
                   and i["answer"].get("from") == "user" and self.fresh(key, i["answer"].get("ts"))]
        if len(answers) < MIN_SAMPLE:
            return
        n = sum(1 for t in answers if rubber_stamp(t))
        if n / len(answers) >= SHADOW_READY_RATE:
            yield key, f"shadow mode would have decided {len(answers)} questions and you agreed with {n}", \
                "switch triage.mode to acting for this profile"
        else:
            yield key, f"shadow mode would have decided {len(answers)} questions and you only agreed with {n}", \
                "tighten triage.thresholds before turning it on"

    def skipped(self):
        key = "ask-no-recommendation"
        evs = self.triage_events(key)
        n = sum(1 for e in evs if e.get("reason") == "no recommendation")
        if len(evs) >= MIN_SAMPLE and n / len(evs) >= SKIPPED_RATE:
            yield key, f"{n} of {len(evs)} questions had no Recommendation line, so none could be settled", \
                "strengthen the Recommendation rule in lead/protocol/checkpoint.md"

    def failing(self):
        key = "triage-failing"
        bad = [e for e in self.triage_events(key)[-20:] if e.get("failed")]
        if len(bad) >= MIN_REPEATS:
            yield key, f"{len(bad)} recent judgments failed, latest: {bad[-1].get('reason')}", \
                "check triage.key_env is set where bw runs, and triage.model"

    def ratings(self):
        lead_of = {s.get("session_id"): s.get("lead") for s in self.data["sessions"]}
        last = {}
        for r in self.data["ratings"]:  # a re-rated session counts once, as its latest rating
            if lead_of.get(r.get("session_id")):
                last[r["session_id"]] = r
        for lead in sorted({lead_of[s] for s in last}):
            key = f"lead-rated-poorly:{lead}"
            rated = [r for s, r in last.items() if lead_of[s] == lead and self.fresh(key, r.get("ts"))]
            poor = sum(1 for r in rated if r.get("value") == "bad")
            if poor >= MIN_REPEATS and poor / len(rated) >= 0.5:
                yield key, f"you rated {poor} of {len(rated)} sessions with {lead} poorly", \
                    f"move {lead} down lead_order, or try another model for it"

    def switches(self):
        for reason, side, prefix in (("capped", "from", "lead-capped"), ("override", "to", "lead-forced")):
            names = {}
            for e in self.data["leads"]:
                name = (e.get(side) or {}).get("lead")
                if e.get("reason") == reason and name and self.fresh(f"{prefix}:{name}", e.get("ts")):
                    names[name] = names.get(name, 0) + 1
            for name, n in names.items():
                if n >= MIN_REPEATS:
                    yield (f"lead-capped:{name}", f"{name} hit its cap {n} times", "put a lead with more room first in lead_order") \
                        if reason == "capped" else (f"lead-forced:{name}", f"you chose {name} by hand {n} times",
                                                    f"put {name} first in lead_order")

    def seats(self):
        recent = self.data["panels"][-PANELS_SEEN:]
        seats = {s for p in recent for s in p.get("absent") or {}}
        for seat in sorted(seats):
            key = f"seat-absent:{seat}"
            n = sum(1 for p in recent if seat in (p.get("absent") or {}) and self.fresh(key, p.get("ts")))
            if n >= MIN_REPEATS:
                yield key, f"{seat} missed {n} of the last {len(recent)} panels", f"take {seat} out of the checkpoints"


def suggestions(data, items, now, notes=(), done=None, thresholds=None):
    """Every suggestion the evidence supports: {"key", "evidence", "action"}."""
    done = done or {}
    r = _Rules(data, items, now, done, thresholds)
    found = [*r.reversed_(), *r.stamped(), *r.shadow(), *r.skipped(), *r.failing(), *r.ratings(), *r.switches(), *r.seats()]
    found += [(f"note:{n['id']}", f"{n.get('from', 'someone')} noted: {n.get('text', '')}", "decide what to change")
              for n in notes if n.get("id") and f"note:{n['id']}" not in done]
    return [{"key": k, "evidence": e, "action": a} for k, e, a in found]


def load_done(state):
    done = read_json(os.path.join(state, DONE_FILE), {})
    if not isinstance(done, dict):
        return {}
    return {k: v for k, v in done.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}


def mark_done(state, key, now):
    done = load_done(state)
    done[key] = now
    write_json_atomic(os.path.join(state, DONE_FILE), done)
