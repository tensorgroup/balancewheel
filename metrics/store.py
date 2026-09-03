"""store.py — the one reader (and the one appender) for the metrics JSONL.

Append-only. Later events amend earlier ones:
  watch-update  {"type":"watch-update","watch_id":…,"status":"open|resolved|dismissed","note":…}
  panel-amend   {"type":"panel-amend","panel_id":… (legacy: "panel_ts"),"findings_detail":[…],…,"note":…}
load() folds these and normalises legacy shapes; nothing else should parse the raw lines.
"""
import fcntl
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

PANEL_AMENDABLE = ("findings_detail", "findings", "findings_raw", "findings_after_triage", "notes")
COUNT_KEYS = ("total", "confirmed", "refuted", "partial", "unique")
WATCH_STATUSES = ("open", "resolved", "dismissed")


def now_ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_path(config=None):
    env = os.environ.get("BALANCEWHEEL_METRICS_LOG")
    if env:
        return env
    if config and config.get("metrics_log"):
        return config["metrics_log"]
    return os.path.expanduser("~/.local/state/balancewheel/metrics.jsonl")


def _sha8(s):
    return hashlib.sha1(s.encode()).hexdigest()[:8]


def watch_id(kind, target, panel_id):
    return _sha8(f"{kind}|{target}|{panel_id}")


def legacy_watch_id(ts, kind, target):
    return _sha8(f"{ts}|{kind}|{target}")


def panel_id_for(event):
    return event.get("id") or _sha8(event.get("ts", ""))


def read_events(path):
    p = Path(path)
    if not p.exists():
        return [], []
    events, warnings = [], []
    # append() writes UTF-8. Decode with replacement so a write cut inside a multibyte character
    # becomes an unparseable last line (skipped with a warning) instead of a UnicodeDecodeError.
    lines = p.read_bytes().decode("utf-8", errors="replace").split("\n")
    last_index = max((i for i, l in enumerate(lines) if l.strip()), default=-1)
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            kind = "truncated final line" if i == last_index else "unparseable line"
            warnings.append(f"{kind} {i + 1} skipped")
            continue
        if not isinstance(obj, dict) or not isinstance(obj.get("type"), str):
            warnings.append(f"line {i + 1} is not an event object; skipped")
            continue
        events.append(obj)
    return events, warnings


def locked(path):
    """Context manager: exclusive lock on <log>.lock for read-modify-append sequences (e.g. watch dedupe)."""
    import contextlib

    @contextlib.contextmanager
    def _cm():
        p = Path(str(path) + ".lock")
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return _cm()


def _is_count(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _normalise_counts(findings):
    """Counts are ints or None ("not recorded"). Bools and floats are corrupt data, not counts."""
    out = {}
    if not isinstance(findings, dict):
        return out
    for seat, c in findings.items():
        c = c if isinstance(c, dict) else {}
        out[seat] = {k: (c[k] if _is_count(c.get(k)) else None) for k in COUNT_KEYS}
    return out


def _normalise_panel(e):
    seats = e.get("seats") or {}
    if isinstance(seats, list):
        seats = {s: None for s in seats}
    return {
        "type": "panel",
        "id": panel_id_for(e),
        "ts": e.get("ts"),
        "target": e.get("target", ""),
        "kind": e.get("kind"),
        "seats": dict(seats),
        "rounds": e.get("rounds"),
        "immediate_agreement": e.get("immediate_agreement"),
        "findings": _normalise_counts(e.get("findings")),
        "findings_raw": e.get("findings_raw"),
        "findings_after_triage": e.get("findings_after_triage"),
        "findings_detail": e.get("findings_detail"),
        "disputes": list(e.get("disputes") or []),
        "notes": e.get("notes", ""),
        "amendments": [],
    }


def _normalise_watch(e):
    wid = e.get("id") or legacy_watch_id(e.get("ts", ""), e.get("kind", ""), e.get("target", ""))
    raw_status = e.get("status")
    if raw_status == "performed" or (raw_status is None and e.get("auto") is True):
        raw_status = "resolved"  # older loggers: "performed", or auto:true with no status, = already done
    status = raw_status if raw_status in WATCH_STATUSES else "open"
    return {
        "type": "watch", "id": wid, "ts": e.get("ts"), "kind": e.get("kind"),
        "target": e.get("target", ""), "panel_id": e.get("panel_id"),
        "status": status, "detail": e.get("detail") or e.get("summary") or "", "updates": [],
    }


def load(path):
    events, warnings = read_events(path)
    usage = [e for e in events if e.get("type") == "usage"]
    panels = [_normalise_panel(e) for e in events if e.get("type") == "panel"]
    by_id, by_ts = {}, {}
    for p in panels:
        if p["id"] in by_id:
            warnings.append(f"duplicate panel id {p['id']}; amendments will attach to the last one")
        by_id[p["id"]] = p
        by_ts[p["ts"]] = p
    for e in events:
        if e.get("type") != "panel-amend":
            continue
        p = by_id.get(e.get("panel_id")) or by_ts.get(e.get("panel_ts"))
        if p is None:
            warnings.append(f"panel-amend at {e.get('ts')} targets an unknown panel")
            continue
        for k in PANEL_AMENDABLE:
            if k in e:
                p[k] = _normalise_counts(e[k]) if k == "findings" else e[k]
        p["amendments"].append({"ts": e.get("ts"), "note": e.get("note", "")})
    watches = [_normalise_watch(e) for e in events if e.get("type") == "watch"]
    w_by_id = {w["id"]: w for w in watches}
    for e in events:
        if e.get("type") != "watch-update":
            continue
        w = w_by_id.get(e.get("watch_id"))
        if w is None:
            warnings.append(f"watch-update at {e.get('ts')} targets unknown watch {e.get('watch_id')}")
            continue
        if e.get("status") in WATCH_STATUSES:
            w["status"] = e["status"]
        w["updates"].append({"ts": e.get("ts"), "status": e.get("status"), "note": e.get("note", "")})
    return {"usage": usage, "panels": panels, "watches": watches, "events": events, "warnings": warnings}


def append(path, event):
    """One line, one write(), under an exclusive lock. Adds ts if missing.
    flush() without fsync(): survives a process crash, not a power loss — acceptable for metrics."""
    event = dict(event)
    event.setdefault("ts", now_ts())
    line = json.dumps(event, ensure_ascii=False) + "\n"
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(line)
            f.flush()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
