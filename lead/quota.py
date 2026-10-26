"""quota.py — per-plan quota snapshots and caps, and the Claude Code statusline recorder.

One file per plan (`state_dir/quota/<plan>.json`, `state_dir/caps/<plan>.json`,
`state_dir/models/<plan>.json`), each replaced atomically, so two projects' statuslines never
overwrite each other's plans. No credential is read: Claude Code hands its statusline command
`rate_limits` and `model.id` on stdin. A payload without `rate_limits` (before the first
response, or in a release that dropped the field) records the plan's quota as unknown.

Each quota source writes its own snapshot: the statusline `quota/<plan>.json`, the others
`quota/<plan>.<source>.json`. `arbitrate` picks a plan's account-wide windows from the first
known, fresh source in the plan's precedence order, and takes pools (windows that cover only
some leads) from a fresh Orca snapshot whichever source won. An unknown snapshot carries
`last_known_at`, copied forward, so "no reading since <t>" survives repeated failures.

    python3 -m lead.quota --statusline <plan>     # reads the statusline JSON on stdin, prints a summary
"""
import json
import os
import sys
import time

from .paths import locked_dir, read_json, state_dir as _state_dir, write_json_atomic
from . import rotation
from .rotation import cap_record

WINDOWS = ("five_hour", "seven_day")
SOURCES = ("statusline", "probe", "orca")
ORDER = {"statusline": ["statusline"], "probe": ["probe", "orca"], "orca": ["orca"], "none": []}
FUTURE_SLACK_S = 300


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
        write_snapshot(state, plan, "statusline",
                       {"unknown": True, "reason": "no rate_limits in statusline", "observed_at": now}, now)
        return None
    snapshot["observed_at"] = now
    write_snapshot(state, plan, "statusline", snapshot, now)
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


def _snapshot_file(state, plan, source):
    if source not in SOURCES:
        raise ValueError(f"unknown quota source {source!r}")
    return _file(state, "quota", plan if source == "statusline" else f"{plan}.{source}")


def _last_known(snap, now):
    """When a snapshot (known, or unknown with a carried time) last saw a real reading; a time
    in the future is no reading at all."""
    if not isinstance(snap, dict):
        return None
    t = snap.get("last_known_at") if snap.get("unknown") else snap.get("observed_at")
    return t if _num(t) and t <= now + FUTURE_SLACK_S else None


def write_snapshot(state, plan, source, snap, now):
    """Replace one source's snapshot for a plan. An unknown snapshot keeps the time of the last
    known reading of the same source (`last_known_at`), so a run of failures still says since when."""
    path = _snapshot_file(state, plan, source)
    snap = dict(snap)
    if snap.get("unknown"):
        snap["last_known_at"] = _last_known(snap, now) or _last_known(read_json(path), now)
    write_json_atomic(path, snap)
    return snap


def read_sources(state):
    """{plan: {source: snapshot}} from every snapshot file; unknown sources and non-objects skipped."""
    out = {}
    for name, snap in _read_dir(state, "quota", dotted=True).items():
        plan, dot, source = name.partition(".")
        source = source if dot else "statusline"
        if source in SOURCES and not (dot and source == "statusline") and isinstance(snap, dict):
            out.setdefault(plan, {})[source] = snap
    return out


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _windows_ok(windows):
    return isinstance(windows, list) and bool(windows) and all(
        isinstance(w, dict) and isinstance(w.get("name"), str) and _num(w.get("used_pct"))
        and (w.get("resets_at") is None or _num(w.get("resets_at"))) for w in windows)


def _observation_stale(src, snap, now, probe_every_min):
    """None when the snapshot is a known, dated, current observation (its windows not judged);
    else the reason it is not."""
    if not snap or not isinstance(snap, dict):
        return "no reading"
    if snap.get("unknown"):
        return snap.get("reason", "unknown")
    t = snap.get("observed_at")
    if not _num(t):
        return "no observed_at"
    if t > now + FUTURE_SLACK_S:
        return "observed in the future"
    if src != "statusline" and now - t > 2 * probe_every_min * 60:
        return f"stale: observed {(now - t) / 60:.0f} min ago"
    return None


def _fresh(src, snap, now, probe_every_min):
    """None when the snapshot is a usable, current reading; else the reason it is not."""
    why = _observation_stale(src, snap, now, probe_every_min)
    if why:
        return why
    if not _windows_ok(snap.get("windows")):
        return "no usable windows"
    if rotation.effective_quota(snap, now) is None:
        return "past its reset"
    return None


def arbitrate(sources, plan_cfg, now, probe_every_min):
    """The plan's windows from the first fresh known source in its precedence order (plus
    `source`, `age_s`), or unknown with every source's reason; `pools` from a fresh Orca
    snapshot whichever source won; Orca's reading on a statusline plan only as `cross_check`."""
    orca_ok = bool(plan_cfg.get("orca_provider"))
    orca = sources.get("orca") if orca_ok else None
    orca = orca if isinstance(orca, dict) else None
    # Pools need a current observation, not current windows (an account window that reset since
    # says nothing about a weekly pool), and each stands only until its own reset.
    pools = orca.get("pools") if orca and not _observation_stale("orca", orca, now, probe_every_min) else None
    pools = [p for p in pools if isinstance(p, dict) and not (_num(p.get("resets_at")) and p["resets_at"] <= now)] \
        if isinstance(pools, list) else []
    cross = orca if plan_cfg["quota"] == "statusline" and orca else None
    reasons, last_known = [], None
    for src in [s for s in ORDER.get(plan_cfg["quota"], []) if s != "orca" or orca_ok]:
        snap = sources.get(src)
        snap = snap if isinstance(snap, dict) else {}
        lk = _last_known(snap, now)
        last_known = max(filter(None, [last_known, lk]), default=None)
        why = _fresh(src, snap, now, probe_every_min)
        if why:
            reasons.append(f"{src}: {why}")
            continue
        return dict(snap, source=src, age_s=now - snap["observed_at"], pools=pools, cross_check=cross)
    return {"unknown": True, "source": None, "last_known_at": last_known, "pools": pools, "cross_check": cross,
            "reason": "; ".join(reasons) or f"quota: {plan_cfg['quota']}"}


def notice(arbitrated, plan_cfg, now, probe_every_min):
    """A line for a plan that has had no usable reading for a whole probe interval, else None."""
    if plan_cfg.get("quota", "none") == "none" or not arbitrated or not arbitrated.get("unknown"):
        return None
    last = arbitrated.get("last_known_at")
    last = last if _num(last) else None
    if now - (last or 0) <= probe_every_min * 60:
        return None
    since = f"since {time.ctime(last)}" if last else "yet"
    return f"no quota reading {since}; rotation is using observed caps only"


def clear_expired_caps(state, arbitrated, now, expiry_hours):
    """Remove cap records past their reset once a fresh known reading taken after that reset
    exists for the plan, else once `expiry_hours` more have passed. An unexpired cap is never
    touched. Under the caps lock, re-reading each record first. Returns the plans cleared."""
    def expired(caps):
        return {p: c for p, c in caps.items() if isinstance(c, dict) and _num(c.get("resets_at")) and now >= c["resets_at"]}

    cleared = []
    if not expired(_read_dir(state, "caps")):
        return cleared  # nothing to clear: no lock taken, nothing written
    with locked_dir(os.path.join(state, "caps")):
        for plan, cap in sorted(expired(_read_dir(state, "caps")).items()):
            reset = cap["resets_at"]
            a = arbitrated.get(plan) if isinstance(arbitrated, dict) else None
            read_since = (isinstance(a, dict) and not a.get("unknown") and a.get("source")
                          and _num(a.get("observed_at")) and a["observed_at"] >= reset)
            if read_since or now >= reset + expiry_hours * 3600:
                try:
                    os.remove(_file(state, "caps", plan))
                    cleared.append(plan)
                except FileNotFoundError:
                    pass
    return cleared


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


def _read_dir(state, kind, dotted=False):
    d = os.path.join(state, kind)
    out = {}
    if os.path.isdir(d):
        for name in os.listdir(d):
            if name.endswith(".json") and (dotted or "." not in name[:-5]):
                v = read_json(os.path.join(d, name))
                if v is not None:
                    out[name[:-5]] = v
    return out


def read_quota(state, config=None, now=None):
    """Without config: the statusline snapshots as stored. With config: `arbitrate` per plan."""
    if config is None:
        return _read_dir(state, "quota")
    now = time.time() if now is None else now
    every = (config.get("rotation") or {}).get("probe_every_min", 15)
    sources = read_sources(state)
    return {name: arbitrate(sources.get(name, {}), pcfg, now, every) for name, pcfg in config["plans"].items()}


def read_caps(state, now, expiry_hours=5, include_expired=False):
    caps = _read_dir(state, "caps")
    return caps if include_expired else {p: c for p, c in caps.items() if c.get("resets_at", 0) > now}


def read_model(state, plan):
    v = read_json(_file(state, "models", plan))
    return v.get("model") if v else None


def summary(snapshot):
    if not snapshot:
        return "quota unknown"
    if snapshot.get("unknown") or not _windows_ok(snapshot.get("windows")):
        return f"quota unknown — {snapshot['reason']}" if snapshot.get("reason") else "quota unknown"
    short = {"five_hour": "5h", "seven_day": "7d"}
    parts = [f"{short.get(w['name'], w['name'])} {100 - w['used_pct']:.0f}%" for w in snapshot["windows"]]
    if snapshot.get("source") and _num(snapshot.get("age_s")):
        parts.append(f"{snapshot['source']} {max(snapshot['age_s'], 0) / 60:.0f}m")
    return " — ".join([" · ".join(parts)] + _notes(snapshot))


NOTE_LIMIT = 120


def _notes(snapshot):
    """A snapshot's `note` and `notes`, escaped (they can carry text from a tool's output) and short."""
    notes = snapshot.get("notes")
    raw = [n for n in [snapshot.get("note")] + (notes if isinstance(notes, list) else []) if isinstance(n, str) and n]
    if not raw:
        return []
    from lead.guard.review import safe  # only when there is a note: the statusline renders summary() often
    return [n if len(n) <= NOTE_LIMIT else n[:NOTE_LIMIT - 1] + "…" for n in map(safe, raw)]


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
