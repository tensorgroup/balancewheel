"""rotation.py — which lead should hold a project, as a pure function of quota, caps, and time.

Stored quota windows carry `used_pct`; every threshold here is on remaining = 100 - used_pct.
Unknown quota (no snapshot, or a snapshot taken before its own window reset) counts as
eligible: rotation never depends on a probe being right, only on observed caps.

`caps` should include expired records (quota.read_caps(..., include_expired=True)): an expired
cap is what makes a recovering plan meet the return threshold before it is used again.

Moments: "start" (a new session), "running" (polling a live session: leave only on an
observed cap, and only if someone else can take over), "boundary" (the session ended: return
to an earlier lead whose cap has reset and whose quota is back above the return threshold).
"""
from collections import namedtuple

Decision = namedtuple("Decision", "lead reason notice")

LOW_REMAINING_PCT = 10
LOW_RESET_HORIZON_S = 3600


def cap_record(now, resets_at=None, expiry_hours=5, window="unknown", source="observed"):
    return {"resets_at": float(resets_at) if resets_at is not None else now + expiry_hours * 3600.0,
            "window": window, "source": source}


def effective_quota(snapshot, now):
    """The snapshot if it still describes the current windows, else None (unknown)."""
    if not snapshot or snapshot.get("unknown") or not snapshot.get("windows"):
        return None
    if any(w.get("resets_at") is not None and now >= w["resets_at"] for w in snapshot["windows"]):
        return None  # a reading from before its own reset says nothing about now
    return snapshot


def _remaining(w):
    return 100.0 - float(w.get("used_pct", 0))


def _cap(caps, plan):
    return caps.get(plan)


def _capped(caps, plan, now):
    c = _cap(caps, plan)
    return bool(c) and now < c["resets_at"]


def _low_for_start(quota, plan, now):
    q = effective_quota(quota.get(plan), now)
    if not q:
        return False
    return any(_remaining(w) < LOW_REMAINING_PCT and (w.get("resets_at") or now) - now > LOW_RESET_HORIZON_S
               for w in q["windows"])


def _recovered(quota, caps, plan, now, threshold):
    """A plan that was capped may come back once its cap has passed and quota is healthy or unknown."""
    c = _cap(caps, plan)
    if c and now < c["resets_at"]:
        return False
    q = effective_quota(quota.get(plan), now)
    return q is None or all(_remaining(w) >= threshold for w in q["windows"])


def _earliest_reset(caps, plans, now):
    times = [caps[p]["resets_at"] for p in plans if p in caps and caps[p]["resets_at"] > now]
    return min(times) if times else None


def decide(profile, leads, rotation, current, pinned, quota, caps, now, moment):
    order = list(profile.get("lead_order", []))
    plan = lambda name: leads[name]["plan"]  # noqa: E731
    threshold = rotation.get("return_above_pct", 50)
    notice = ""

    def eligible(name):
        return name in leads and not _capped(caps, plan(name), now)

    # A pin chooses who starts; it never interrupts a running session (only a cap does).
    if pinned and moment != "running":
        if eligible(pinned):
            return Decision(pinned, "pinned", "")
        notice = f"pinned lead {pinned} is capped; using the profile order until it resets"

    if moment == "running":
        if current and _capped(caps, plan(current), now):
            for name in order:
                if name != current and eligible(name):
                    return Decision(name, "capped", notice)
            when = _earliest_reset(caps, [plan(n) for n in order], now)
            return Decision(current, "none-eligible",
                            f"all leads capped; earliest reset at {when:.0f}" if when else "all leads capped")
        return Decision(current, "stay", notice)

    if moment == "boundary" and current in order:
        for name in order[:order.index(current)]:
            if eligible(name) and _recovered(quota, caps, plan(name), now, threshold):
                return Decision(name, "returned", notice)
        if eligible(current):
            return Decision(current, "stay", notice)

    skipped_low = skipped_cap = False
    for name in order:
        if not eligible(name):
            skipped_cap = True
            continue
        if _cap(caps, plan(name)) and not _recovered(quota, caps, plan(name), now, threshold):
            skipped_low = True
            continue
        if _low_for_start(quota, plan(name), now):
            skipped_low = True
            continue
        reason = "quota-low" if skipped_low else ("capped" if skipped_cap else "order")
        return Decision(name, reason, notice)
    for name in order:  # everyone low: take the first lead that is not actually capped
        if eligible(name):
            return Decision(name, "quota-low", notice)
    when = _earliest_reset(caps, [plan(n) for n in order], now)
    # Nobody eligible: keep a running lead (moment boundary), but never start a capped one.
    return Decision(current if moment == "boundary" else None, "none-eligible",
                    f"all leads capped; earliest reset at {when:.0f}" if when else "no leads configured")
