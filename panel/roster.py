"""Which seats run on a panel: checkpoint, tier, roster, stand-ins, and shortness.

Everything here reads config and plan 5's seat state. Nothing here starts a seat."""
from typing import Optional

from lead import proc
from seats import caps, verify

ALIASES = {"code-review": "review"}
RESERVED_REASON = "reserved: deep panels only"
ONE_AGY_REASON = "one agy seat per panel"
PENDING_PROBES = ("never verified", "key changed")


def checkpoint(profile: dict, name: str) -> str:
    """The checkpoint name with its alias resolved; ValueError when the profile lacks it."""
    name = ALIASES.get(name, name)
    defined = sorted(profile.get("checkpoints") or {})
    if name in defined:
        return name
    if not defined:
        raise ValueError("this profile defines no checkpoints")
    raise ValueError(f"checkpoint '{name}' is not in this profile; it defines: {', '.join(defined)}")


def tier(profile: dict, stakes: str, deep: bool) -> str:
    depth = profile.get("depth")
    if deep or depth == "deep" or (depth == "deep-on-stakes" and stakes == "high"):
        return "deep"
    return "standard"


def _seat(config, name) -> dict:
    return (config.get("seats") or {}).get(name) or {}


def _enabled(config, name) -> bool:
    return name in (config.get("seats") or {}) and _seat(config, name).get("enabled", True)


def _may_run(config, name, tier_) -> bool:
    """Enabled, and not a reserved seat on the standard tier."""
    return _enabled(config, name) and (tier_ == "deep" or _seat(config, name).get("tier") != "reserved")


def roster(config, profile, checkpoint, tier) -> list:
    names = list(profile["checkpoints"][checkpoint]["seats"])
    if tier == "deep":
        for name, seat in config["seats"].items():
            if (seat.get("tier") == "reserved" and _enabled(config, name)
                    and checkpoint in (seat.get("roles") or []) and name not in names):
                names.append(name)
    return names


def run_counts(config, names, tier) -> tuple:
    """`(originals, fallbacks)` for the watchdog deadline."""
    originals = [n for n in names if _may_run(config, n, tier)]
    fallbacks = set()
    for n in originals:
        fb = _seat(config, n).get("fallback")
        if fb and fb not in names and _may_run(config, fb, tier):
            fallbacks.add(fb)
    return len(originals), len(fallbacks)


def standin(config, state, seat, seats, tier, now, capped=caps.capped) -> Optional[str]:
    """Mark the fallback of `seat` as its stand-in. Return its id when it must be launched."""
    if (seats.get(seat) or {}).get("stand_in_for"):
        return None  # one hop only
    fb = _seat(config, seat).get("fallback")
    if not fb or not _may_run(config, fb, tier) or capped(config, state, fb, now):
        return None
    if (seats.get(fb) or {}).get("status") == "absent":
        return None
    entry = seats.get(fb)
    if entry is None:
        seats[fb] = {"status": "pending", "stand_in_for": seat, "on_roster": False}
        return fb
    entry["stand_in_for"] = seat
    return None


def prelaunch(config, state, names, tier, now, capped=caps.capped) -> tuple:
    runs, seats, capped_names = [], {}, []
    agy_taken = False
    for name in names:
        if name in seats:
            continue
        if not _enabled(config, name):
            seats[name] = {"status": "absent", "reason": "disabled", "on_roster": True}
        elif not _may_run(config, name, tier):
            seats[name] = {"status": "absent", "reason": RESERVED_REASON, "on_roster": True}
        elif capped(config, state, name, now):
            seats[name] = {"status": "absent", "reason": "usage cap", "on_roster": True}
            capped_names.append(name)
        elif _seat(config, name).get("runtime") == "agy" and agy_taken:
            seats[name] = {"status": "absent", "reason": ONE_AGY_REASON, "on_roster": True}
        else:
            agy_taken = agy_taken or _seat(config, name).get("runtime") == "agy"
            seats[name] = {"status": "pending", "on_roster": True}
            runs.append(name)
    for name in capped_names:
        fb = _seat(config, name).get("fallback")
        if fb and fb not in seats and _seat(config, fb).get("runtime") == "agy" and agy_taken:
            continue  # a new agy stand-in would be a second agy seat
        launched = standin(config, state, name, seats, tier, now, capped=capped)
        if launched:
            agy_taken = agy_taken or _seat(config, launched).get("runtime") == "agy"
            runs.append(launched)
    return runs, seats


def pending_verify(config, state, names, run=proc.run) -> list:
    """Names whose first verification on the current key has not finished."""
    entries = verify.write_seats_json(config, state, run=run)
    return [n for n in names
            if (entries.get(n) or {}).get("verified") is False
            and entries[n].get("failed_probe") in PENDING_PROBES]


def verify_running(state) -> bool:
    with verify.hold_lock(state) as free:
        return not free


def shortness(spec: dict, seats: dict) -> tuple:
    why = []
    for name in spec.get("required") or []:
        entry = seats.get(name) or {}
        if entry.get("status") != "ok":
            why.append(f"required seat {name} absent: {entry.get('reason') or 'not run'}")
    minimum = spec.get("min_seats") or 0
    ok = sum(1 for e in seats.values() if e.get("status") == "ok")
    if ok < minimum:
        why.append(f"{ok} of {len(seats)} seats returned (at least {minimum})")
    return bool(why), why
