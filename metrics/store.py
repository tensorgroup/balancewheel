"""store.py — the one reader (and the one appender) for the metrics JSONL.

Append-only. Later events amend earlier ones:
  watch-update  {"type":"watch-update","watch_id":…,"status":"open|resolved|dismissed","note":…}
  panel-amend   {"type":"panel-amend","panel_id":… (legacy: "panel_ts"),"findings_detail":[…],…,"note":…}
load() folds these and normalises legacy shapes; nothing else should parse the raw lines.
Other rows load() keeps as written, by type, for example:
  run           {"type":"run","phase":"start|beat|end","run_id":"r<12 hex>","t":<float epoch>,"seat":…,"dir":…,…}
                one seat or local run; load() keeps them in file order under "runs".
read_tail() reads only the end of the log, backward in chunks, for the live view.
"""
import fcntl
import hashlib
import json
import math
import os
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path

PANEL_AMENDABLE = ("findings_detail", "findings", "findings_raw", "findings_after_triage", "notes")
COUNT_KEYS = ("total", "confirmed", "refuted", "partial", "unique")
WATCH_STATUSES = ("open", "resolved", "dismissed")
TAIL_CHUNK = 256 * 1024
TAIL_CAP = 64 * 1024 * 1024
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
Tail = namedtuple("Tail", "events complete error")


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
    raw_id = event.get("id")
    if raw_id is None or raw_id == "":
        return _sha8(event.get("ts", ""))
    return raw_id if isinstance(raw_id, str) else str(raw_id)


def _has_explicit_id(event):
    raw_id = event.get("id")
    return not (raw_id is None or raw_id == "")


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


def _parse_ts(ts):
    try:
        return datetime.strptime(ts, TS_FORMAT).replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def row_time(event):
    """The row's own time: `t` when it is a finite number (not a bool), else `ts`, else None."""
    t = event.get("t")
    if isinstance(t, (int, float)) and not isinstance(t, bool):
        try:
            if math.isfinite(t):
                return float(t)
        except OverflowError:
            pass
    return _parse_ts(event.get("ts"))


def written_time(event):
    """The append time (`ts`), else row_time. The log is in this order; a row ingested late keeps an old `t`."""
    w = _parse_ts(event.get("ts"))
    return w if w is not None else row_time(event)


def _parse_line(raw):
    """One raw line to an event dict with a string `type`, else None (blank, cut, or not an event)."""
    line = raw.decode("utf-8", errors="replace")
    if not line.strip():
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or not isinstance(obj.get("type"), str):
        return None
    return obj


def read_tail(path, since, cap=TAIL_CAP, chunk=TAIL_CHUNK):
    """Events with row_time >= since, in file order, read backward from the end in chunks.
    It stops at the first chunk that holds a row written before `since` (the cover point), or after
    `cap` bytes; complete is False only when the cap stopped it before the cover point and the file start.
    A missing file is Tail([], True, None); any other OSError is Tail([], False, "<strerror>")."""
    try:
        f = open(path, "rb")
    except FileNotFoundError:
        return Tail([], True, None)
    except OSError as e:
        return Tail([], False, e.strerror or type(e).__name__)
    out, carry, read, complete = [], b"", 0, True
    try:
        with f:
            pos = os.fstat(f.fileno()).st_size
            while pos > 0:
                if read >= cap:
                    complete = False  # the cap stopped us before the cover point and the file start
                    break
                n = min(chunk, pos, cap - read)
                pos -= n
                f.seek(pos)
                buf = f.read(n) + carry
                read += n
                parts = buf.split(b"\n")
                # the first piece may be cut by the chunk edge: carry it into the next read
                carry, body = (parts[0], parts[1:]) if pos > 0 else (b"", parts)
                oldest = None
                for raw in reversed(body):
                    e = _parse_line(raw)
                    if e is None:
                        continue
                    out.append(e)
                    w = written_time(e)  # append time: the file is in this order, `t` is not
                    if w is not None:
                        oldest = w if oldest is None else min(oldest, w)
                if oldest is not None and oldest < since - 1:  # `ts` has 1 s precision
                    break
    except OSError as e:
        return Tail([], False, e.strerror or type(e).__name__)
    out.reverse()
    kept = []
    for e in out:
        t = row_time(e)
        if t is not None and t >= since:  # 0.0 is a time
            kept.append(e)
    return Tail(kept, complete, None)


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


def _normalise_target(target):
    if target is None:
        return ""
    return target if isinstance(target, str) else str(target)


def _normalise_seats(raw_seats):
    raw_seats = raw_seats or {}
    if isinstance(raw_seats, list):
        return {str(s): None for s in raw_seats}
    if isinstance(raw_seats, dict):
        return {k: (None if v is None else str(v)) for k, v in raw_seats.items() if isinstance(k, str)}
    return {}


def _normalise_panel(e):
    return {
        "type": "panel",
        "absent": e.get("absent") if isinstance(e.get("absent"), dict) else {},
        "id": panel_id_for(e),
        "ts": e.get("ts"),
        "target": _normalise_target(e.get("target", "")),
        "kind": e.get("kind"),
        "seats": _normalise_seats(e.get("seats")),
        "tier": e.get("tier") if isinstance(e.get("tier"), str) else None,
        "rounds": e.get("rounds"),
        "immediate_agreement": e.get("immediate_agreement"),
        "findings": _normalise_counts(e.get("findings")),
        "findings_raw": e.get("findings_raw") if _is_count(e.get("findings_raw")) else None,
        "findings_after_triage": e.get("findings_after_triage") if _is_count(e.get("findings_after_triage")) else None,
        "findings_detail": e.get("findings_detail"),
        "disputes": list(e.get("disputes") or []),
        "notes": e.get("notes", ""),
        "moderator": e.get("moderator") if isinstance(e.get("moderator"), dict) else None,
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
    panel_events = [e for e in events if e.get("type") == "panel"]
    by_id, by_ts, order = {}, {}, []
    for e in panel_events:
        p = _normalise_panel(e)
        if p["id"] in by_id:
            warnings.append(f"duplicate panel id {p['id']}; amendments will attach to the last one")
        else:
            order.append(p["id"])
        by_id[p["id"]] = p
        if not _has_explicit_id(e) and p["ts"] in by_ts:
            warnings.append(f"duplicate panel ts {p['ts']}; a panel_ts amend will attach to the last one")
        by_ts[p["ts"]] = p
    panels = [by_id[i] for i in order]  # first-seen order; last occurrence's data wins, matching amend behaviour
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
    sessions, s_by_id = [], {}
    for e in events:
        if e.get("type") != "session":
            continue
        sid = e.get("session_id")
        if e.get("phase") == "start":
            s = {k: e.get(k) for k in ("session_id", "project", "worktree", "lead", "runtime", "model", "resolved", "plan",
                                   "autonomy", "guard_version")}
            s.update({"start": e.get("ts"), "end": None, "end_reason": None})
            sessions.append(s); s_by_id[sid] = s
        elif e.get("phase") == "end":
            s = s_by_id.get(sid)
            if s is None:  # an end with no start (a session closed as lost after a crash)
                s = {"session_id": sid, "project": e.get("project"), "start": None}
                sessions.append(s); s_by_id[sid] = s
            s.update({"end": e.get("ts"), "end_reason": e.get("reason")})
    leads = [e for e in events if e.get("type") == "lead"]
    ratings = [e for e in events if e.get("type") == "rating"]
    guards = [e for e in events if e.get("type") == "guard"]
    guard_modes = [e for e in events if e.get("type") == "guard_mode"]
    asks = [e for e in events if e.get("type") == "ask"]
    answers = [e for e in events if e.get("type") == "answer"]
    runs = [e for e in events if e.get("type") == "run"]
    panel_runs, run_ids = [], set()
    for e in events:  # a crash between append and mark can repeat a panel's event: keep the first
        if e.get("type") != "panel_run" or (e.get("panel_id") is not None and e.get("panel_id") in run_ids):
            continue
        panel_runs.append(e)
        run_ids.add(e.get("panel_id"))
    return {"usage": usage, "panels": panels, "watches": watches, "events": events, "warnings": warnings,
            "sessions": sessions, "leads": leads, "ratings": ratings, "guards": guards, "guard_modes": guard_modes, "asks": asks,
            "answers": answers, "panel_runs": panel_runs, "runs": runs}


def append(path, event):
    """One record appended per call, under an exclusive lock. Adds ts if missing.
    If a prior write was cut short (no trailing newline), repair it first — still inside the
    same lock, two writes under one lock, never two locks. Bytes go through errors="replace" so
    a lone surrogate in a record never tracebacks.
    flush() without fsync(): survives a process crash, not a power loss — acceptable for metrics."""
    event = dict(event)
    event.setdefault("ts", now_ts())
    line = json.dumps(event, ensure_ascii=False) + "\n"
    data = line.encode("utf-8", errors="replace")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a+b") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            size = f.seek(0, os.SEEK_END)
            if size > 0:
                f.seek(size - 1)
                if f.read(1) != b"\n":
                    f.write(b"\n")
            f.write(data)
            f.flush()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
