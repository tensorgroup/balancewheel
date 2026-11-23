"""Seat cap flags. A seat cap lives under `seats/caps/`; it never writes the plan caps."""
import fcntl
import os
import time

from lead import quota, rotation
from lead.paths import read_json, write_json_atomic


def _file(state, seat):
    return os.path.join(state, "seats", "caps", f"{seat}.json")


def arm(state, seat, now, expiry_hours, resets_at=None):
    rec = rotation.cap_record(now, resets_at=resets_at, expiry_hours=expiry_hours, source="seat")
    path = _file(state, seat)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path + ".lock", os.O_RDONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        write_json_atomic(path, rec)
    finally:
        os.close(fd)
    return rec


def read(state, seat, now):
    rec = read_json(_file(state, seat))
    if isinstance(rec, dict) and isinstance(rec.get("resets_at"), (int, float)) and rec["resets_at"] > now:
        return rec
    return None


def clear(state, seat):
    try:
        os.remove(_file(state, seat))
    except OSError:
        pass


def _hhmm(epoch):
    return time.strftime("%H:%M", time.localtime(epoch))


def capped(config, state, seat, now):
    rec = read(state, seat, now)
    if rec:
        return f"seat {seat} capped until {_hhmm(rec['resets_at'])}"
    plan = ((config.get("seats") or {}).get(seat) or {}).get("plan")
    plan_cap = quota.read_caps(state, now).get(plan) if plan else None
    if plan_cap:
        return f"plan {plan} capped until {_hhmm(plan_cap['resets_at'])}"
    return None
