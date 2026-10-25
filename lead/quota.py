"""quota.py — per-plan quota snapshots and caps, and the Claude Code statusline recorder.

One file per plan (`state_dir/quota/<plan>.json`, `state_dir/caps/<plan>.json`,
`state_dir/models/<plan>.json`), each replaced atomically, so two projects' statuslines never
overwrite each other's plans. No credential is read: Claude Code hands its statusline command
`rate_limits` and `model.id` on stdin. A payload without `rate_limits` (before the first
response, or in a release that dropped the field) records the plan's quota as unknown.

    python3 -m lead.quota --statusline <plan>     # reads the statusline JSON on stdin, prints a summary
"""
import json
import os
import sys
import time

from .paths import locked_dir, read_json, state_dir as _state_dir, write_json_atomic
from .rotation import cap_record

WINDOWS = ("five_hour", "seven_day")


def _file(state, kind, plan):
    return os.path.join(state, kind, f"{plan}.json")


def parse_statusline(payload):
    model = (payload.get("model") or {}).get("id") if isinstance(payload, dict) else None
    limits = payload.get("rate_limits") if isinstance(payload, dict) else None
    if not isinstance(limits, dict):
        return None, model
    windows = []
    for name in WINDOWS:
        w = limits.get(name)
        if isinstance(w, dict) and isinstance(w.get("used_percentage"), (int, float)):
            r = w.get("resets_at")
            windows.append({"name": name, "used_pct": float(w["used_percentage"]),
                            "resets_at": float(r) if isinstance(r, (int, float)) and not isinstance(r, bool) else None})
    return ({"windows": windows} if windows else None), model


def record_statusline(state, plan, payload, now, expiry_hours=5):
    snapshot, model = parse_statusline(payload)
    if model:
        write_json_atomic(_file(state, "models", plan), {"model": model, "observed_at": now})
    if snapshot is None:
        write_json_atomic(_file(state, "quota", plan), {"unknown": True, "reason": "no rate_limits in statusline", "observed_at": now})
        return None
    snapshot["observed_at"] = now
    write_json_atomic(_file(state, "quota", plan), snapshot)
    full = [w for w in snapshot["windows"] if w["used_pct"] >= 100]
    if full:
        # Both windows full: the plan is usable again only when the LATER one resets.
        known = [w for w in full if w.get("resets_at") is not None]
        w = max(known, key=lambda w: w["resets_at"]) if known else full[0]
        record_cap(state, plan, now, resets_at=w.get("resets_at"), expiry_hours=expiry_hours,
                   window=w["name"], source="statusline")
    else:
        _clear_expired_cap(state, plan, now)
    return snapshot


def _clear_expired_cap(state, plan, now):
    """Remove a cap that has passed — under a lock, re-reading first, so a cap renewed
    concurrently is never deleted."""
    path = _file(state, "caps", plan)
    with locked_dir(os.path.join(state, "caps")):
        cap = read_json(path)
        if cap and now >= cap.get("resets_at", 0):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass


def record_cap(state, plan, now, resets_at=None, expiry_hours=5, window="unknown", source="manual"):
    cap = cap_record(now, resets_at=resets_at, expiry_hours=expiry_hours, window=window, source=source)
    with locked_dir(os.path.join(state, "caps")):
        write_json_atomic(_file(state, "caps", plan), cap)
    return cap


def _read_dir(state, kind):
    d = os.path.join(state, kind)
    out = {}
    if os.path.isdir(d):
        for name in os.listdir(d):
            if name.endswith(".json"):
                v = read_json(os.path.join(d, name))
                if v is not None:
                    out[name[:-5]] = v
    return out


def read_quota(state):
    return _read_dir(state, "quota")


def read_caps(state, now, expiry_hours=5, include_expired=False):
    caps = _read_dir(state, "caps")
    return caps if include_expired else {p: c for p, c in caps.items() if c.get("resets_at", 0) > now}


def read_model(state, plan):
    v = read_json(_file(state, "models", plan))
    return v.get("model") if v else None


def summary(snapshot):
    if not snapshot:
        return "quota unknown"
    short = {"five_hour": "5h", "seven_day": "7d"}
    return " · ".join(f"{short.get(w['name'], w['name'])} {100 - w['used_pct']:.0f}%" for w in snapshot["windows"])


def main(argv):
    if len(argv) != 2 or argv[0] != "--statusline":
        sys.exit("usage: python3 -m lead.quota --statusline <plan>")
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        payload = {}
    snapshot = record_statusline(_state_dir(), argv[1], payload, time.time())
    print(summary(snapshot))


if __name__ == "__main__":
    main(sys.argv[1:])
