"""offer.py — `bw start`'s short guard review: a few unmarked decisions, asked for at most once a day.

The offer runs on a terminal only, before the lead opens, and Enter skips it. It builds the evidence
that the gate counts: a quarter sampled allows before promotion, and at least two stops after it.
Only bw writes `state_dir/guard-offer.json` (`{profile: {"ts"}}`), under an flock.
"""
import fcntl
import os
import random

from ..paths import guarded, read_json, write_json_atomic
from . import gate, record, review

FILE = "guard-offer.json"
DAY_S, WEEK_S = 86400, 7 * 86400
BEFORE, AFTER = 5, 3  # decisions offered: under the gate's counts, and while acting


def path(state):
    return os.path.join(state, FILE)


def last_offer(state, profile):
    data = read_json(path(state), {})
    entry = data.get(profile) if isinstance(data, dict) else None
    ts = entry.get("ts") if isinstance(entry, dict) else None
    return ts if isinstance(ts, (int, float)) and not isinstance(ts, bool) else None


def record_offer(state, profile, now):
    os.makedirs(state, exist_ok=True)
    fd = os.open(path(state) + ".lock", os.O_RDONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        data = read_json(path(state), {})
        data = data if isinstance(data, dict) else {}
        data[profile] = {"ts": now}
        write_json_atomic(path(state), data)
    finally:
        os.close(fd)


def due(config, state, profile, runtime, summary, mode, pending, now):
    """How many decisions to offer now: 0 for none."""
    if pending <= 0 or gate.pin(config, profile) == "shadow":
        return 0
    last = last_offer(state, profile)
    if mode == "acting":
        n, gap = AFTER, WEEK_S
    elif summary["reviewed"] < review.MIN_REVIEWED or summary["sampled_allows"] < review.MIN_SAMPLED:
        n, gap = BEFORE, DAY_S
    else:
        return 0
    return n if last is None or now - last >= gap else 0


def first_runtime(config, profile):
    """The runtime of the first guarded lead in the profile's lead_order, else None."""
    for lname in ((config.get("profiles") or {}).get(profile) or {}).get("lead_order", []):
        lead = (config.get("leads") or {}).get(lname)
        if lead and guarded(lead):
            return lead["runtime"]
    return None


def maybe_offer(config, state, profile, stdin, out, now, rng=None):
    if not (stdin.isatty() and out.isatty()):
        return
    runtime = first_runtime(config, profile)
    if runtime is None:
        return
    rng = rng or random.SystemRandom()
    key = gate.current_key(config, profile, runtime)
    decisions = record.load(state)["decisions"]
    entry = gate.read(state, profile, runtime)
    mode = entry.get("mode") if entry else "shadow"
    acting = mode == "acting"
    pending = len(review.offer_pick(decisions, key, BEFORE, acting, rng))
    n = due(config, state, profile, runtime, review.summary(decisions, key), mode, pending, now)
    if not n:
        return
    items = review.offer_pick(decisions, key, n, acting, rng)
    out.write(f"review {len(items)} guard decisions now? [y/N] ")
    out.flush()
    answer = stdin.readline().strip().lower()
    record_offer(state, profile, now)
    if answer in ("y", "yes"):
        review.run(state, stdin=stdin, out=out, rng=rng, items=items, key=key)
