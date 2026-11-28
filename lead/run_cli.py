"""`bw run`: track a local command as a run (start, beat, and end rows), so the live pane shows it.

    bw run --seat NAME [--model M] [--label TEXT] [--dir D] -- COMMAND [ARGS...]
    bw run start --seat NAME [--model M] [--label TEXT] [--dir D]     stdout: the run id only
    bw run end RUN_ID (--ok | --fail [--reason R])

The wrap form runs COMMAND with stdio passed through, forwards SIGTERM and SIGINT to it (SIGINT only when
`bw run` is not its terminal's foreground group, which already signalled the child), writes a beat row
every `local.run_max_s / 2` while it runs (PD-13), and exits with its status (128 + n after signal n; 127 or
126 when it cannot start). The sink is chosen once, on the first row (PD-14): the log through `store.append`;
when that fails and `BW_CHECKPOINT` is set, request files in `<checkpoint dir>/runs/`; else one
`bw run: not tracked (...)` line, and the command still runs. `start`/`end` exit 0 when tracking fails.
"""
import os
import signal
import socket
import subprocess
import sys
import time

from lead import runs
from lead.guard.review import safe
from lead.paths import ConfigError, load_config
from lead.projects import resolve_project
from metrics import store
from metrics.config import RUN_MAX_S_DEFAULT, SEAT_NAME

USAGE = ("usage: bw run --seat NAME [--model M] [--label TEXT] [--dir D] -- COMMAND [ARGS...]\n"
         "       bw run start --seat NAME [--model M] [--label TEXT] [--dir D]\n"
         "       bw run end RUN_ID (--ok | --fail [--reason R])")
FORWARDED = (signal.SIGINT, signal.SIGTERM)


class Usage(Exception):
    pass


def _one_line(text):
    """A flag value as one safe line (the direct append skips the panel service's validator)."""
    lines = str(text).splitlines()
    return safe(lines[0].strip() if lines else "")


def _flags(argv, names, bools=()):
    """Parse `--name VALUE` (names) and `--flag` (bools) with no positionals; raises Usage."""
    got, i = {}, 0
    while i < len(argv):
        a = argv[i]
        if a in bools and a not in got:
            got[a] = True
            i += 1
        elif a in names and a not in got and i + 1 < len(argv):
            got[a] = argv[i + 1]
            i += 2
        else:
            raise Usage(f"unexpected argument {safe(a)!r}")
    return got


class Sink:
    """Where this call's rows go, chosen on the first row and kept: "log", "request", or None (untracked)."""

    def __init__(self, err, environ):
        self.err, self.environ = err, environ
        self.config, self.why, self.mode = None, None, ""  # "" = not chosen yet
        try:
            self.config = load_config()
            self.log = store.log_path(self.config)
        except ConfigError as e:
            self.why = f"config error: {e}"

    def local(self):
        return (self.config or {}).get("local") or {}

    def _request_dir(self):
        cp = self.environ.get("BW_CHECKPOINT")
        return runs.requests_dir(os.path.dirname(cp)) if cp else None

    def write(self, row):
        if self.mode == "":
            self._choose(row)
        elif self.mode:
            try:
                self._write(self.mode, row)
            except OSError:
                pass  # said once already, or the first row went through: never stop the command

    def _write(self, mode, row):
        if mode == "log":
            store.append(self.log, row)
        else:
            body = row if row.get("phase") != "end" else {k: v for k, v in row.items()
                                                           if not (k in ("seat", "dir") and v is None)}
            runs.write_request(self._request_dir(), body)

    def _choose(self, row):
        if self.config is not None:
            try:
                self._write("log", row)
                self.mode = "log"
                return
            except OSError as e:
                self.why = f"log: {e.strerror or type(e).__name__}"
        if self._request_dir():
            try:
                self._write("request", row)
                self.mode = "request"
                return
            except OSError as e:
                self.why += f"; request file: {e.strerror or type(e).__name__}"
        self.mode = None
        print(f"bw run: not tracked ({safe(self.why)})", file=self.err)


def in_foreground() -> bool:
    """True when `bw run` is its terminal's foreground process group. Then a Ctrl-C at that terminal
    signals the whole group, the child included, so `bw run` must not send it a second SIGINT.
    No terminal, or an error, means False (forward)."""
    for fd in (0, 1, 2):
        try:
            if os.isatty(fd):
                return os.tcgetpgrp(fd) == os.getpgrp()
        except OSError:
            return False
    return False


def _where(d, config):
    """The worktree root of `d`, else its real path."""
    try:
        return resolve_project(d, config or {}).worktree
    except (ValueError, OSError):
        return os.path.realpath(d)


def _start_fields(got, sink):
    """seat, model, dir, and label of a start row; raises Usage for a bad --seat."""
    seat = got.get("--seat")
    if seat is None or not SEAT_NAME.fullmatch(seat):
        raise Usage(f"--seat must match {SEAT_NAME.pattern}")
    model = got.get("--model")
    if model is None:
        model = next((s.get("model") for s in sink.local().get("seats", []) if s.get("name") == seat), None)
    else:
        model = _one_line(model) or None
    lab = runs.label(got["--label"]) if got.get("--label") else None
    return seat, model, _where(got.get("--dir") or os.getcwd(), sink.config), lab


def _wrap(argv, out, err, environ, now):
    if "--" not in argv:
        raise Usage("missing -- COMMAND")
    cut = argv.index("--")
    got, command = _flags(argv[:cut], ("--seat", "--model", "--label", "--dir")), argv[cut + 1:]
    if not command:
        raise Usage("missing COMMAND after --")
    sink = Sink(err, environ)
    seat, model, wt, lab = _start_fields(got, sink)
    t0 = now()
    row = runs.start_row(seat, model, wt, lab, "local", pid=os.getpid(), host=socket.gethostname(), t=t0)
    run_id = row["run_id"]
    interval = sink.local().get("run_max_s", RUN_MAX_S_DEFAULT) / 2

    child, pending = None, []

    def send(signum):
        try:
            child.send_signal(signum)
        except (ProcessLookupError, OSError):
            pass

    def forward(signum, frame):
        if child is None:
            pending.append(signum)  # the child never saw it: sent once it starts
        elif signum != signal.SIGINT or not in_foreground():  # a terminal's Ctrl-C reached the child already
            send(signum)

    old = {}
    try:
        for s in FORWARDED:
            try:
                old[s] = signal.signal(s, forward)
            except ValueError:  # not the main thread: the signals keep their handlers
                break
        sink.write(row)
        rc = None
        try:
            child = subprocess.Popen(command)
        except FileNotFoundError:
            rc, what = 127, "not found"
        except OSError:
            rc, what = 126, "not executable"
        if rc is not None:
            print(f"bw run: {safe(command[0])}: {what}", file=err)
        else:
            for s in pending:
                send(s)
            while True:
                try:
                    rc = child.wait(timeout=interval)
                    break
                except subprocess.TimeoutExpired:
                    sink.write(runs.beat_row(run_id, seat, wt, t=now()))
    finally:
        for s, h in old.items():
            signal.signal(s, h)
    reason = None if rc == 0 else f"signal {-rc}" if rc < 0 else f"exit {rc}"
    t1 = now()
    sink.write(runs.end_row(run_id, seat, wt, rc == 0, reason, round(t1 - t0, 1), t=t1))
    return 128 - rc if rc < 0 else rc


def _start(argv, out, err, environ, now):
    got = _flags(argv, ("--seat", "--model", "--label", "--dir"))
    sink = Sink(err, environ)
    seat, model, wt, lab = _start_fields(got, sink)
    row = runs.start_row(seat, model, wt, lab, "local", host=socket.gethostname(), t=now())  # no pid (PD-3)
    sink.write(row)
    print(row["run_id"], file=out)
    return 0


def _pending_start(dir_path, run_id, now, err):
    """The start of `run_id` from a request file not yet appended (a contained lead), checked as the panel
    service checks it, with `dir` kept as written when it is one safe line; None when there is none. A
    refused start is one stderr line and None. Never raises. (The service forces `dir` to the worktree
    when it appends either row.)"""
    items, _ = runs.read_requests(dir_path, unlink=False)
    for _, obj in items:
        if isinstance(obj, dict) and obj["phase"] == "start" and obj["run_id"] == run_id:
            wt = obj.get("dir")
            try:
                return runs.validate_request(obj, wt if isinstance(wt, str) and safe(wt) == wt else None, now)
            except Exception as e:  # ValueError, or a fault the checks missed (OverflowError, ...)
                why = safe(str(e))[:runs.REASON_CHARS] if isinstance(e, ValueError) else type(e).__name__
                print(f"bw run end: the pending start of {run_id} was refused ({why})", file=err)
                return None
    return None


def _end(argv, out, err, environ, now):
    if not argv or not runs.RUN_ID.fullmatch(argv[0]):
        raise Usage("RUN_ID must be r and 12 hex digits")
    run_id = argv[0]
    got = _flags(argv[1:], ("--reason",), bools=("--ok", "--fail"))
    if ("--ok" in got) == ("--fail" in got):
        raise Usage("give one of --ok and --fail")
    if "--reason" in got and "--ok" in got:
        raise Usage("--reason goes with --fail")
    sink = Sink(err, environ)
    t = now()
    start = None
    if sink.config is not None:
        tail = store.read_tail(sink.log, t - runs.END_LOOKBACK_S)
        start = next((e for e in reversed(tail.events) if e.get("type") == "run" and e.get("phase") == "start"
                      and e.get("run_id") == run_id), None)
    request_dir = sink._request_dir()
    if start is None and request_dir:
        start = _pending_start(request_dir, run_id, t, err)
    if start is not None and start.get("source") == "bw":
        print(f"bw run end: {run_id} is a bw seat run; bw seat closes it", file=err)
        return 2
    seat = start.get("seat") if start else None
    wt = start.get("dir") if start else None
    t0 = start.get("t") if start else None
    duration = round(t - t0, 1) if isinstance(t0, (int, float)) and not isinstance(t0, bool) else None
    reason = (runs.label(got["--reason"]) or None) if "--reason" in got else None
    sink.write(runs.end_row(run_id, seat, wt, "--ok" in got, reason, duration, t=t))
    return 0


def main(argv: list, out=sys.stdout, err=sys.stderr, environ=None, now=time.time) -> int:
    environ = os.environ if environ is None else environ
    form = {"start": _start, "end": _end}.get(argv[0] if argv else None)
    try:
        if form:
            return form(argv[1:], out, err, environ, now)
        return _wrap(list(argv), out, err, environ, now)
    except Usage as e:
        print(f"bw run: {e}\n{USAGE}", file=err)
        return 2
