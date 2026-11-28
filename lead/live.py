"""`bw live --json`: what runs now in one worktree, and what ran in a time window (spec §4). Read-only.

    bw live --json [--dir D] [--since EPOCH] [--limit N]

It reads the end of the metrics log and of every extra log (`config.extra_logs`) back to
`min(since, now - max(run_max_s, deep cap))`, the pending run request files of the worktree's checkpoint
dir (never taken), and the worktree's panel files (never through the service or `ensure_dir`). Only rows
whose `dir` is the worktree count, so an end or beat joins its start by (run_id, dir), never by run_id
alone. Pending rows are joined with logged rows first; open, recent, and usage are derived after that.

- open: each start with no end, and each run found only by its newest beat (PD-13: its start is out of
  reach; `start`, `model`, and `label` are null), with its state (`run_state`). A panel seat that is done
  (`ok` or `absent`, no running ask) closes its run. A running panel seat or ask with no open run becomes
  one entry with `run_id: null`. One panel seat shows once.
- recent: end rows in the window, with tokens and cost from the run's `usage` row when the end row has none.
  An end whose start is out of reach still shows (spec §1: it repeats `seat` and `dir`), with `start`,
  `model`, and `label` null. An end with a null `seat` or `dir` (`bw run end` found no start) opens nothing
  and is not listed.
- usage: `usage` rows with no `run_id` in the window.
On a config error `main` prints `empty(now, "config error: ...")` and exits 0 (PD-12).
"""
import json
import math
import os
import socket
import sys
import time
from typing import Optional

from lead import handoff, proc, runs
from lead.paths import ConfigError, load_config, state_dir
from lead.projects import resolve_project, worktree_key
from metrics import config as metrics_config, store
from metrics.config import RUN_MAX_S_DEFAULT
from panel import state as pstate
from seats.live import live_usage, seat_home_path

DEFAULT_SINCE_S = 86400
DEFAULT_LIMIT = 50
LIMIT_MAX = 500
DEEP_CAP_MIN_DEFAULT = 30
LOCAL_SEAT_FIELDS = ("name", "match", "metric", "model", "icon", "color")
USAGE = "usage: bw live --json [--dir D] [--since EPOCH] [--limit N]"


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # it exists under another user
        return True
    except (OSError, OverflowError, ValueError):  # a pid out of range (a foreign row) names no process
        return False
    return True


def run_state(row: dict, now: float, host: str, run_max_s: int, alive=pid_alive, last: Optional[float] = None) -> str:
    """`live`, `unknown` (its pid on this host is gone), or `stale` (past `run_max_s` with no pid rule).
    `last` is the newest time of this run's start and beat rows (PD-13); the row's own time when None."""
    pid, t = row.get("pid"), last if last is not None else store.row_time(row)
    if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 and row.get("host") == host:
        return "live" if alive(pid) else "unknown"
    return "live" if t is not None and now - t <= run_max_s else "stale"


def _local_view(config) -> dict:
    local = config.get("local") or {}
    return {"seats": [{k: s.get(k) for k in LOCAL_SEAT_FIELDS} for s in local.get("seats") or []],
            "logs": [], "families": dict(local.get("families") or {}),
            "run_max_s": local.get("run_max_s", RUN_MAX_S_DEFAULT)}


def empty(now: float, error: Optional[str] = None) -> dict:
    """A v1 document with nothing in it; `error` (and `complete: false`) only for PD-12."""
    doc = {"v": 1, "now": now, "complete": error is None, "open": [], "recent": [], "usage": [],
           "local": _local_view({}), "panels": []}
    if error is not None:
        doc["complete"] = False
        doc["error"] = error
    return doc


def worktree(dir: str, config: dict, run=proc.run) -> str:
    """The worktree root of `dir`, else its real path (outside git)."""
    try:
        return resolve_project(dir, config, run=run).worktree
    except (ValueError, OSError):
        return os.path.realpath(dir)


def _str(v) -> bool:
    return isinstance(v, str) and bool(v)


def _time(row):
    try:
        return store.row_time(row)
    except Exception:  # a foreign row with an odd `ts`
        return None


def _read_logs(config, cover, local):
    """[(row, source)] from the main log and the extra logs, and whether every read was complete."""
    main = store.log_path(config)
    tail = store.read_tail(main, cover)
    complete = tail.complete and tail.error is None
    rows = [(e, e.get("source") if _str(e.get("source")) else "bw") for e in tail.events]
    for path in metrics_config.extra_logs(config, exclude=main):
        if not os.path.exists(path):  # read_tail reads a missing file as an empty one (F9)
            local["logs"].append({"path": path, "ok": False, "error": "missing"})
            continue
        t = store.read_tail(path, cover)
        complete = complete and t.complete and t.error is None
        local["logs"].append({"path": path, "ok": t.error is None, "error": t.error})
        rows += [(e, "local") for e in t.events]
    return rows, complete


class _Run:
    """One run of the worktree: its start (or None), newest beat, and end, by run_id."""
    __slots__ = ("start", "src", "beat", "beat_t", "end", "end_src")

    def __init__(self):
        self.start = self.src = self.beat = self.beat_t = self.end = self.end_src = None


def _index(rows, pending, wt):
    """{run_id: _Run} of `wt`'s run rows, logged then pending (a pending row that ingest would refuse is
    left out), and the worktree's usage rows as [(row, source)]."""
    by_id, usage = {}, []
    for row, src in rows:
        if row.get("dir") != wt:
            continue
        if row.get("type") == "usage":
            usage.append((row, src))
        elif row.get("type") == "run":
            _add(by_id, row, src, pending=False)
    for row in sorted(pending, key=lambda r: runs.PHASES.index(r["phase"])):
        _add(by_id, row, "local", pending=True)
    return by_id, usage


def _add(by_id, row, src, pending):
    rid, phase, t = row.get("run_id"), row.get("phase"), _time(row)
    if not (isinstance(rid, str) and runs.RUN_ID.fullmatch(rid)) or phase not in runs.PHASES:
        return
    r = by_id.get(rid)
    if pending:
        if phase == "start" and r is not None and r.start is not None:
            return  # a duplicate start (ingest refuses it)
        if phase != "start" and r is not None and r.src == "bw":
            return  # a bw seat run; bw seat closes it
    if r is None:
        r = by_id[rid] = _Run()
    if phase == "start":
        if r.start is None:
            r.start, r.src = row, src
    elif phase == "beat":
        if t is not None and (r.beat_t is None or t >= r.beat_t):
            r.beat, r.beat_t = row, t
    elif r.end is None or (t is not None and t >= (_time(r.end) or 0.0)):
        r.end, r.end_src = row, src


def _pending(state, wt, now):
    """The valid pending run requests of `wt` as rows; nothing on disk changes."""
    items, _ = runs.read_requests(runs.requests_dir(handoff.checkpoint_dir(state, worktree_key(wt))), unlink=False)
    out = []
    for _, obj in items:
        if isinstance(obj, ValueError):
            continue
        try:
            out.append(runs.validate_request(obj, wt, now))
        except Exception:  # ValueError, or a fault the checks missed (as runs._validated)
            continue
    return out


def _panel_map(state, wt, wanted):
    """{panel id: panel} of the worktree's unlogged panels, plus each logged one a run names in `wanted`."""
    key = worktree_key(wt)
    out = {p.get("id"): p for p in pstate.panels(state, key) if _str(p.get("id"))}
    for pid in wanted:
        if pid not in out and isinstance(pid, str) and pstate.PANEL_ID.fullmatch(pid):
            p = pstate.load(state, key, pid)
            if p is not None:
                out[pid] = p
    return out


def _seats(panel) -> dict:
    s = panel.get("seats")
    return {k: v if isinstance(v, dict) else {} for k, v in s.items() if isinstance(k, str)} if isinstance(s, dict) else {}


def _asks(panel) -> list:
    a = panel.get("asks")
    return [x for x in a if isinstance(x, dict)] if isinstance(a, list) else []


def _running_ask(panel, seat):
    return next((a.get("id") for a in _asks(panel) if a.get("seat") == seat and a.get("state") == "running"), None)


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def _open_entry(run_id, seat, model, label, wt, start, source, panel, ask, state, local_seats):
    ls = local_seats.get(seat) or {}
    return {"run_id": run_id, "seat": seat, "model": model, "label": label, "dir": wt, "start": start,
            "source": source, "panel": panel, "ask": ask, "state": state, "icon": ls.get("icon"),
            "color": ls.get("color"), "ctx": None, "window": None, "cost_usd": None}


def _live_fill(entry, config, state, wt, local_by_name):
    """ctx, window, and cost from the seat CLI's session file, for a live run with a known start."""
    if entry["state"] != "live" or entry["start"] is None or not _str(entry["seat"]):
        return
    seat = entry["seat"]
    if entry["source"] == "bw":
        cfg = (config.get("seats") or {}).get(seat)
        if not isinstance(cfg, dict) or not cfg.get("runtime"):
            return
        u = live_usage(cfg["runtime"], seat_home_path(state, seat), None, wt, entry["start"], cfg)
    else:
        rt = (local_by_name.get(seat) or {}).get("runtime")
        if not rt:
            return
        u = live_usage(rt, None, None, wt, entry["start"])
    entry.update(ctx=u.get("ctx"), window=u.get("window"), cost_usd=u.get("cost_usd"))


def _open(by_id, panels, config, state, wt, now, host, run_max_s, alive, local_by_name):
    entries = []
    for rid, r in by_id.items():
        if r.end is not None or (r.start is None and r.beat is None):
            continue
        if r.start is not None:
            st = r.start
            t0 = _time(st)
            last = max(x for x in (t0, r.beat_t) if x is not None) if (t0 is not None or r.beat_t is not None) else None
            state_ = run_state(st, now, host, run_max_s, alive, last=last)
            seat = st.get("seat") if _str(st.get("seat")) else None
            panel = st.get("panel") if _str(st.get("panel")) else None
            ask = None
            if panel in panels:
                p = panels[panel]
                entry_seat = _seats(p).get(seat) if seat else None
                ask = _running_ask(p, seat) if seat else None
                if entry_seat is not None and entry_seat.get("status") in ("ok", "absent") and ask is None:
                    continue  # panel state shows the seat done
            e = _open_entry(rid, seat, st.get("model") if _str(st.get("model")) else None,
                            st.get("label") if _str(st.get("label")) else None, wt, t0, r.src, panel, ask, state_,
                            local_by_name)
        else:  # found by its newest beat only: its start is out of reach (F2); only `bw run` writes beats
            b = r.beat
            seat = b.get("seat") if _str(b.get("seat")) else None
            e = _open_entry(rid, seat, None, None, wt, None, "local", None, None,
                            run_state(b, now, host, run_max_s, alive, last=r.beat_t), local_by_name)
            e["_last"] = r.beat_t
        entries.append(e)
    # one panel seat shows once: the newest open run of it
    newest = {}
    for e in entries:
        if e["panel"] is not None:
            k = (e["panel"], e["seat"])
            if k not in newest or (e["start"] or 0.0) >= (newest[k]["start"] or 0.0):
                newest[k] = e
    entries = [e for e in entries if e["panel"] is None or newest[(e["panel"], e["seat"])] is e]
    shown = {(e["panel"], e["seat"]) for e in entries if e["panel"] is not None}
    for pid, p in panels.items():
        if p.get("state") not in pstate.OPEN:
            continue
        seats = _seats(p)
        names = [n for n, s in seats.items() if s.get("status") == "running"]
        names += [a.get("seat") for a in _asks(p) if a.get("state") == "running" and _str(a.get("seat"))
                  and a.get("seat") not in names]
        for name in names:
            if (pid, name) in shown:
                continue
            shown.add((pid, name))
            ask = next((a for a in _asks(p) if a.get("seat") == name and a.get("state") == "running"), None)
            job = p.get("job") if isinstance(p.get("job"), dict) else {}
            start = _num(job.get("started"))
            if start is None and ask is not None:
                start = _num(ask.get("ts"))
            if start is None:
                start = _num(p.get("created"))
            cfg = (config.get("seats") or {}).get(name)
            model = cfg.get("model") if isinstance(cfg, dict) and _str(cfg.get("model")) else None
            entries.append(_open_entry(None, name, model, None, wt, start, "bw", pid,
                                       ask.get("id") if ask else None, "live", local_by_name))
    for e in entries:
        if e["run_id"] is not None:
            _live_fill(e, config, state, wt, local_by_name)
    entries.sort(key=lambda e: (e["start"] if e["start"] is not None else e.get("_last") or 0.0))
    for e in entries:
        e.pop("_last", None)
    return entries


def _recent(by_id, usage, since, limit):
    by_run = {}
    for row, _ in usage:
        rid = row.get("run_id")
        if isinstance(rid, str) and rid not in by_run:
            by_run[rid] = row
    out = []
    for rid, r in by_id.items():
        end = r.end
        if end is None:
            continue
        st = r.start if r.start is not None else {}
        if r.start is None and not _str(end.get("seat")):
            continue  # `bw run end` found no start: null seat, so not listed (a null dir is already out)
        te = _time(end)
        if te is None or te < since:
            continue
        u = by_run.get(rid) or {}
        seat = end.get("seat") if _str(end.get("seat")) else st.get("seat") if _str(st.get("seat")) else None
        item = {"run_id": rid, "seat": seat, "model": st.get("model") if _str(st.get("model")) else None,
                "label": st.get("label") if _str(st.get("label")) else None, "start": _time(st) if st else None, "end": te,
                "ok": end.get("ok") if isinstance(end.get("ok"), bool) else None,
                "reason": end.get("reason") if _str(end.get("reason")) else None,
                "source": r.src if r.start is not None else "local" if r.beat is not None else r.end_src,
                "stand_in_for": st.get("stand_in_for") if _str(st.get("stand_in_for")) else None}
        for k in ("tokens_in", "tokens_out", "cost_usd"):
            v = _num(end.get(k))
            item[k] = v if v is not None else _num(u.get(k))
        item["ctx_peak"] = _num(end.get("ctx_peak"))
        for k in ("tokens_in", "tokens_out", "ctx_peak"):
            if item[k] is not None and item[k].is_integer():
                item[k] = int(item[k])
        out.append(item)
    out.sort(key=lambda x: x["end"], reverse=True)
    return out[:limit]


def _usage(usage, since, limit):
    out = []
    for row, src in usage:
        if row.get("run_id") is not None:
            continue
        t = _time(row)
        if t is None or t < since:
            continue
        out.append({"seat": row.get("seat") if _str(row.get("seat")) else None, "t": t, "dir": row.get("dir"),
                    "ok": row.get("ok") if isinstance(row.get("ok"), bool) else None, "source": src})
    out.sort(key=lambda x: x["t"], reverse=True)
    return out[:limit]


def _panels_view(panels):
    out = []
    for pid, p in panels.items():
        if p.get("state") not in pstate.OPEN:
            continue
        out.append({"panel": pid, "state": p.get("state"),
                    "seats": {n: s.get("status") if _str(s.get("status")) else "pending" for n, s in _seats(p).items()},
                    "asks": [{"id": a.get("id"), "seat": a.get("seat"), "state": a.get("state")} for a in _asks(p)]})
    return out


def build(config, state, cwd, since=None, limit=DEFAULT_LIMIT, now=None, run=proc.run, alive=pid_alive,
          host=None) -> dict:
    """The v1 document for the worktree of `cwd` (spec §4). Writes nothing."""
    now = time.time() if now is None else now
    since = now - DEFAULT_SINCE_S if since is None else since
    host = socket.gethostname() if host is None else host
    local = _local_view(config)
    run_max_s = local["run_max_s"]
    deep_s = ((config.get("panel") or {}).get("deep_cap_min") or DEEP_CAP_MIN_DEFAULT) * 60
    cover = min(since, now - max(run_max_s, deep_s))
    rows, complete = _read_logs(config, cover, local)
    wt = worktree(cwd, config, run=run)
    by_id, usage = _index(rows, _pending(state, wt, now), wt)
    local_by_name = {s.get("name"): s for s in (config.get("local") or {}).get("seats") or []}
    panels = _panel_map(state, wt, {r.start.get("panel") for r in by_id.values()
                                    if r.start is not None and r.end is None})
    return {"v": 1, "now": now, "complete": complete,
            "open": _open(by_id, panels, config, state, wt, now, host, run_max_s, alive, local_by_name),
            "recent": _recent(by_id, usage, since, limit), "usage": _usage(usage, since, limit),
            "local": local, "panels": _panels_view(panels)}


class _Usage(Exception):
    pass


def _parse(argv):
    got, i = {}, 0
    while i < len(argv):
        a = argv[i]
        if a == "--json" and a not in got:
            got[a] = True
            i += 1
        elif a in ("--dir", "--since", "--limit") and a not in got and i + 1 < len(argv):
            got[a] = argv[i + 1]
            i += 2
        else:
            raise _Usage(f"unexpected argument {a!r}")
    if "--json" not in got:
        raise _Usage("only --json output exists")
    since = None
    if "--since" in got:
        try:
            since = float(got["--since"])
        except ValueError:
            since = None
        if since is None or not math.isfinite(since):
            raise _Usage("--since must be a finite epoch in seconds")
    limit = DEFAULT_LIMIT
    if "--limit" in got:
        try:
            limit = int(got["--limit"])
        except ValueError:
            limit = 0
        if not 1 <= limit <= LIMIT_MAX:
            raise _Usage(f"--limit must be an integer from 1 to {LIMIT_MAX}")
    return got.get("--dir"), since, limit


def main(argv: list, out=sys.stdout, err=sys.stderr) -> int:
    try:
        dir_, since, limit = _parse(list(argv))
    except _Usage as e:
        print(f"bw live: {e}\n{USAGE}", file=err)
        return 2
    now = time.time()
    try:
        config = load_config()
        state = state_dir(config)
    except ConfigError as e:
        doc = empty(now, f"config error: {e}")
    else:
        doc = build(config, state, dir_ or os.getcwd(), since=since, limit=limit, now=now)
    print(json.dumps(doc), file=out)
    return 0
