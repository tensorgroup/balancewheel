"""cli.py — `bw panel`: the user's own path to a panel (a contained lead uses request files instead).

    bw panel <checkpoint> [target] [--deep] [--focus TEXT] [--stakes normal|high] [--path P]... [--background]
    bw panel ask <panel-id> <seat> <claim> [--dispute ID]
    bw panel log <panel-id> < verdicts.json
    bw panel amend <panel-id> < patch.json
    bw panel status [<panel-id>]

Output goes to the given streams only. Exit 0; 1 when a foreground run failed; 2 on a usage error
or a refused request (the message on stderr, prefixed `bw panel:`).
"""
import argparse
import fcntl
import json
import os
import signal
import sys
import time

from lead.guard.review import safe
from lead.projects import resolve_project
from panel import channel, run, service
from panel import state as pstate

SUBCOMMANDS = ("ask", "log", "amend", "status")
JOB_BUSY = "a panel job is running for this worktree; try again when it ends"
NO_LEAD = "a panel job is running and no bw lead runs here to start the next one; try again when it ends"
MAX_STDIN = 1024 * 1024


class _Usage(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _Usage(message)

    def exit(self, status=0, message=None):
        raise _Usage(message.strip() if message else "usage error")


def _parsers():
    top = _Parser(prog="bw panel", add_help=False)
    top.add_argument("checkpoint")
    top.add_argument("target", nargs="?", default="the current branch")
    top.add_argument("--deep", action="store_true")
    top.add_argument("--focus", default="")
    top.add_argument("--stakes", choices=["normal", "high"], default="normal")
    top.add_argument("--path", action="append", default=[])
    top.add_argument("--background", action="store_true")
    ask = _Parser(prog="bw panel ask", add_help=False)
    ask.add_argument("panel")
    ask.add_argument("seat")
    ask.add_argument("claim")
    ask.add_argument("--dispute", default="d1")
    one = _Parser(prog="bw panel", add_help=False)
    one.add_argument("panel")
    status = _Parser(prog="bw panel status", add_help=False)
    status.add_argument("panel", nargs="?", default=None)
    return top, ask, one, status


def _say(stream, text) -> None:
    stream.write(text + "\n")
    stream.flush()


def _refuse(err, text) -> int:
    _say(err, f"bw panel: {text}")
    return 2


def _lead_runs_here(state, key) -> bool:
    """A `bw lead` holds this worktree's lock now (probed without blocking, never created)."""
    try:
        fd = os.open(os.path.join(state, "locks", f"{pstate.check_key(key)}.lock"), os.O_RDONLY)
    except (OSError, ValueError):
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return False
    finally:
        os.close(fd)


def _read_json(stdin) -> dict:
    text = stdin.read(MAX_STDIN + 1)
    if len(text) > MAX_STDIN:
        raise ValueError("the input is over 1 MiB")
    try:
        obj = json.loads(text)
    except ValueError:
        raise ValueError("the input is not JSON") from None
    if not isinstance(obj, dict):
        raise ValueError("the input must be a JSON object")
    return obj


class _Stop:
    """The worker's stop handler for a foreground run: SIGTERM, SIGINT, and SIGHUP all mean "stop and
    fail the panel cleanly". Restored on exit. Does nothing off the main thread."""

    def __enter__(self):
        run._stop["why"] = None
        self.old = {}

        def on_signal(signum, frame):
            run._stop["why"] = "stopped"
        try:
            for sig in run.STOP_SIGNALS:
                self.old[sig] = signal.signal(sig, on_signal)
        except ValueError:
            pass
        return self

    def __exit__(self, *exc):
        for sig, handler in self.old.items():
            signal.signal(sig, handler)
        run._stop["why"] = None
        return False


def _job(config, panel, kind, ask_id, now) -> dict:
    return {"kind": kind, "ask": ask_id, "pid": os.getpid(), "owner": "foreground", "started": now,
            "deadline": now + service._budget(config, panel, kind), "beat": now, "seat_pgids": []}


def _short_text(state, key, panel) -> str:
    if not panel.get("short"):
        return "full"
    text = run._read_text(os.path.join(pstate.work_dir(state, key, panel["id"]), "findings.json"))
    try:
        why = json.loads(text).get("short_reasons") if text else None
    except ValueError:
        why = None
    why = [safe(str(r)) for r in why] if isinstance(why, list) else []
    return "short: " + ("; ".join(why) if why else "see next.md")


def _convene(a, config, state, project, out, err, jobs) -> int:
    key = project.worktree_key
    if not a.background and pstate.has_job(state, key):
        return _refuse(err, JOB_BUSY)
    lead_here = False
    if a.background and pstate.has_job(state, key):
        lead_here = _lead_runs_here(state, key)
        if not lead_here:  # before open_panel: a refusal leaves no panel behind
            return _refuse(err, NO_LEAD)
    req = {"checkpoint": a.checkpoint, "target": a.target, "focus": a.focus, "stakes": a.stakes,
           "paths": a.path, "deep": a.deep}
    panel = service.open_panel(config, state, project, req)
    pid, now = panel["id"], time.time()
    if a.background:
        if lead_here:
            _say(out, f"bw: panel {pid} queued; bw lead starts it next")
            return 0
        started = service.start_job(config, state, key, panel, "round1", None, jobs or service.Jobs(), now,
                                    lambda kind, text, key=None: _say(err, f"bw panel: {text}"), owner="background")
        if started:
            _say(out, f"bw: panel {pid} running in the background")
            return 0
        if _lead_runs_here(state, key):  # lost the slot in the short race
            _say(out, f"bw: panel {pid} queued; bw lead starts it next")
            return 0
        return _refuse(err, f"panel {pid} could not start: {JOB_BUSY}")
    if not pstate.reserve_job(state, key, pid, _job(config, panel, "round1", None, now)):
        return _refuse(err, f"panel {pid} stays queued: {JOB_BUSY}")
    view = pstate.view_dir(state, key, pid)
    with _Stop():
        doc = run.round1(config, state, key, pid)
    if doc.get("failed"):
        _say(err, f"bw panel: panel {pid} failed ({safe(str(doc['failed']))[:200]}) — see {view}/next.md")
        return 1
    _say(out, f"bw: panel {pid} ready ({_short_text(state, key, pstate.load(state, key, pid) or panel)}) "
              f"— next steps in {view}/next.md")
    return 0


def _ask(a, config, state, project, out, err) -> int:
    key = project.worktree_key
    channel.check_ask(a.seat, a.claim, a.dispute)
    service.own(state, project, a.panel)
    if pstate.has_job(state, key):
        return _refuse(err, JOB_BUSY)
    ask = service.queue_ask(config, state, project, {"panel": a.panel, "seat": a.seat, "claim": a.claim,
                                                     "dispute": a.dispute})
    now = time.time()
    panel = pstate.load(state, key, a.panel)
    if not pstate.reserve_job(state, key, a.panel, _job(config, panel, "ask", ask["id"], now)):
        return _refuse(err, f"ask {ask['id']} stays queued: {JOB_BUSY}")
    with _Stop():
        entry = run.ask(config, state, key, a.panel, ask["id"])
    answer = os.path.join(pstate.view_dir(state, key, a.panel), "asks", f"{ask['id']}.md")
    if (entry or {}).get("state") != "done":
        _say(err, f"bw panel: ask {ask['id']} failed ({safe(str((entry or {}).get('reason')))[:200]})")
        return 1
    _say(out, f"bw: ask {ask['id']} answered — read {answer}")
    return 0


def _response(r, out) -> None:
    _say(out, r["summary"])
    for line in r.get("watches") or []:
        _say(out, line)


def _status(a, state, project, out, err) -> int:
    key = project.worktree_key
    if a.panel is not None:
        service.own(state, project, a.panel)
        text = run._read_text(os.path.join(pstate.view_dir(state, key, a.panel), "status.json"))
        out.write((text or "{}").rstrip("\n") + "\n")
        return 0
    for p in pstate.panels(state, key):
        short = " (short)" if p.get("short") else ""
        _say(out, f"{p['id']}  {safe(str(p.get('checkpoint')))[:40]}  {p.get('state')}{short}  "
                  f"{pstate.view_dir(state, key, p['id'])}")
    return 0


def main(argv, config, state, out=None, err=None, jobs=None, stdin=None) -> int:
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    argv = list(argv)
    top, ask, one, status = _parsers()
    try:
        sub = argv[0] if argv and argv[0] in SUBCOMMANDS else None
        a = {"ask": ask, "log": one, "amend": one, "status": status}.get(sub, top).parse_args(argv[1:] if sub else argv)
    except _Usage as e:
        return _refuse(err, f"usage error: {e}")
    try:
        project = resolve_project(os.getcwd(), config)
        if sub == "ask":
            return _ask(a, config, state, project, out, err)
        if sub == "status":
            return _status(a, state, project, out, err)
        if sub in ("log", "amend"):
            body = _read_json(sys.stdin if stdin is None else stdin)
            if sub == "log":
                _response(service.log_panel(config, state, project, a.panel, body), out)
            else:
                _response(service.amend(config, state, project, a.panel, body), out)
            return 0
        return _convene(a, config, state, project, out, err, jobs)
    except ValueError as e:
        return _refuse(err, safe(str(e)))
