"""run.py — the panel worker: round 1 (every seat, blind and in parallel) and one ask.

`python3 -m panel.run round1 --state S --key K --panel P` and
`python3 -m panel.run ask --state S --key K --panel P --ask A`.

A worker runs only while its panel holds the worktree's one job (Design decision 3), and it clears
the job on its way out. Each seat runs as its own `bw seat` process in its own session, so one
SIGTERM to its group stops it (Design decision 2); the worker records each group in `job.seat_pgids`.
The panel acts on a seat's reason only. It never reads a transcript, and it never shows a seat's
file in the view while that seat's run is live. `bw seat` writes the usage rows; the panel writes none.
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
import traceback
from typing import Optional

from lead import paths
from lead.guard.review import safe
from lead.paths import ConfigError, load_config, read_json
from lead.runs import LEAD_SESSION
from metrics.config import SEAT_NAME
from panel import merge, render, roster
from panel import state as pstate
from seats import brief as seat_brief
from seats import verify

ROOT = paths.ROOT
POLL_S = 0.5
BEAT_S = 15
VERIFY_POLL_S = 5
STOP_WAIT_S = 10
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
REASON_CHARS = 300
ASK_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)

_stop = {"why": None}  # set by the signal handler in main()


class Stopped(Exception):
    """A stop signal reached the worker."""


class NoJob(Exception):
    """The panel holds no job for this worker (it was released): the worker touches nothing."""


class Settled(ValueError):
    """The panel is no longer `queued` or `running`: another writer finished or failed it."""


LIVE = ("queued", "running")


def seat_cmd() -> list:
    """The argv prefix that starts `bw seat`. `BW_PANEL_SEAT_CMD` (a JSON list) replaces it in tests."""
    env = os.environ.get("BW_PANEL_SEAT_CMD")
    if env:
        cmd = json.loads(env)
        if not isinstance(cmd, list) or not cmd or not all(isinstance(a, str) for a in cmd):
            raise ValueError("BW_PANEL_SEAT_CMD must be a JSON list of strings")
        return cmd
    return [sys.executable, os.path.join(ROOT, "bin", "bw"), "seat"]


def seat_argv(seat, prompt, *, worktree, out, result, brief_file=None, session=None, deep=False, panel_id=None,
              lead_session=None, label=None, stand_in_for=None) -> list:
    """One `bw seat` run. The prompt comes last, after `--`, so a prompt that starts with `-` is no option.
    The run-row fields (`--panel`, `--lead-session`, `--label`, `--stand-in-for`) go in only when valid."""
    argv = seat_cmd() + [seat, "--no-fallback", "-d", worktree, "--out", out, "--result", result]
    if brief_file:
        argv += ["--brief-file", brief_file]
    elif session:
        argv += ["--session", session]
    if deep:
        argv.append("--deep")
    for flag, value, valid in (("--panel", panel_id, pstate.PANEL_ID.fullmatch),
                               ("--lead-session", lead_session, LEAD_SESSION.fullmatch),
                               ("--label", label, str.strip), ("--stand-in-for", stand_in_for, SEAT_NAME.fullmatch)):
        if isinstance(value, str) and valid(value):
            argv += [flag, value]
    return argv + ["--", prompt]


def cap_seconds(config, tier) -> float:
    panel = config["panel"]
    return panel["deep_cap_min" if tier == "deep" else "cap_min"] * 60


def classify(returncode: int, result: Optional[dict]) -> tuple:
    """`(status, reason)` of one `bw seat` run. A result file decides when present."""
    if result is not None:
        reason = result.get("reason") if isinstance(result, dict) else None
        if not isinstance(reason, str) or not reason:
            return "absent", "error: bad result"
        if result.get("ok") is True and reason == "ok":
            return "ok", "ok"
        return "absent", reason[:REASON_CHARS]
    if returncode == 143 or returncode < 0:
        return "absent", "interrupted"
    if returncode == 124:
        return "absent", "timeout"
    if returncode == 75:
        return "absent", "error: refused with no result"
    if returncode == 2:
        return "absent", "error: bw seat usage error"
    return "absent", "error: no result"


def _write_text(path, text) -> None:
    """Temp file in the same dir, then rename: a reader never sees a partial file."""
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _read_text(path) -> Optional[str]:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    with os.fdopen(fd, "rb") as f:
        return f.read(MAX_OUTPUT_BYTES).decode("utf-8", errors="replace")


def _unlink(path) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _result(path) -> Optional[dict]:
    return read_json(path) if os.path.lexists(path) else None


def _text(value) -> Optional[str]:
    return value if isinstance(value, str) else None


class _Worker:
    """The state one job shares: its panel, its live seat processes, its beat, and its log."""

    def __init__(self, config, state, key, pid, now):
        self.config, self.state, self.key, self.pid, self.now = config, state, key, pid, now
        self.work = pstate.work_dir(state, key, pid)
        self.view = pstate.view_dir(state, key, pid)
        self.log = os.path.join(self.work, "log.txt")
        self.live = {}  # seat -> (Popen, output file, result file)
        self.last_beat = 0.0

    def update(self, fn) -> dict:
        return pstate.update(self.state, self.key, self.pid, fn)

    def say(self, line) -> None:
        pstate.ensure_dir(self.state, self.work)
        with open(self.log, "a", encoding="utf-8") as f:
            f.write(f"panel.run {time.strftime('%Y-%m-%dT%H:%M:%S')}: {line}\n")

    def set_job(self, p, **fields) -> None:
        if isinstance(p.get("job"), dict):
            p["job"].update(fields)

    def claim(self, p, kind, ask_id=None) -> None:
        """At the start: the panel must hold this kind of job, else NoJob. A job with no pid yet (the
        launcher has not written it) gets this worker's pid, so the watchdog can watch it (g07)."""
        job = p.get("job")
        if not isinstance(job, dict) or job.get("kind") != kind or job.get("ask") != ask_id:
            raise NoJob(self.pid)
        if job.get("pid") is None:
            job["pid"] = os.getpid()

    def sync(self, seats=None) -> None:
        """Write the seats (when given), the live seat groups, and a beat."""
        t = self.now()
        self.last_beat = t
        pgids = [proc.pid for proc, _, _ in self.live.values()]

        def fn(p):
            if seats is not None:
                p["seats"] = json.loads(json.dumps(seats))
            self.set_job(p, beat=t, seat_pgids=pgids)
        self.update(fn)

    def beat(self) -> None:
        if self.now() - self.last_beat >= BEAT_S:
            self.sync()

    def stop_reason(self) -> Optional[str]:
        """`job.stop` when the watchdog set it, else `stopped` on a signal, else None."""
        panel = pstate.load(self.state, self.key, self.pid) or {}
        job = panel.get("job") if isinstance(panel.get("job"), dict) else {}
        why = job.get("stop")
        if isinstance(why, str) and why:
            return why
        return _stop["why"]

    def why(self, exc) -> str:
        try:
            stop = self.stop_reason()
        except BaseException:
            stop = _stop["why"]
        return stop or ("stopped" if isinstance(exc, Stopped) else type(exc).__name__)

    def check_stop(self) -> None:
        if _stop["why"]:
            raise Stopped(_stop["why"])

    def wait(self, seconds) -> None:
        end = time.monotonic() + seconds
        while True:
            self.check_stop()
            self.beat()
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(POLL_S, left))

    def env(self) -> dict:
        env = {**os.environ, "BW_STATE_DIR": self.state}
        if self.config.get("path"):
            env["BALANCEWHEEL_CONFIG"] = self.config["path"]
        return env

    def launch(self, name, argv, out, result) -> None:
        for f in (out, result):
            _unlink(f)
        pstate.ensure_dir(self.state, self.work)
        with open(self.log, "ab") as log:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log,
                                    start_new_session=True, env=self.env())
        self.live[name] = (proc, out, result)

    def terminate(self) -> None:
        """SIGTERM every live seat group, wait up to STOP_WAIT_S, then SIGKILL what is left."""
        for proc, _, _ in self.live.values():
            _killpg(proc.pid, signal.SIGTERM)
        deadline = time.monotonic() + STOP_WAIT_S
        while any(proc.poll() is None for proc, _, _ in self.live.values()) and time.monotonic() < deadline:
            time.sleep(0.05)
        for proc, out, _ in self.live.values():
            if proc.poll() is None:
                _killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            _unlink(out)
        self.live.clear()


def _trace() -> str:
    """`:\n<traceback>` inside an exception handler; "" outside one (`service.fail` calls in from none)."""
    return ":\n" + traceback.format_exc() if sys.exc_info()[0] is not None else ""


def _killpg(pgid, sig) -> None:
    try:
        os.killpg(pgid, sig)
    except OSError:
        pass


# --- round 1 ---

def _bare_doc(panel, seats, absent, short, why, failed) -> dict:
    """findings.json when merge itself failed: no findings, only the seats and the reason."""
    return {"panel_id": panel["id"], "checkpoint": panel.get("checkpoint"), "target": panel.get("target"),
            "tier": panel.get("tier"), "short": short, "short_reasons": why, "failed": failed, "seats": seats,
            "absent": absent, "groups": [], "candidate_disputes": [], "findings_raw": 0, "triage_required": False}


def _spec(config, panel) -> dict:
    profile = (config.get("profiles") or {}).get(panel.get("profile")) or {}
    name = roster.ALIASES.get(panel.get("checkpoint"), panel.get("checkpoint"))
    return (profile.get("checkpoints") or {}).get(name) or {}


def _finish(w, seats, failed=None) -> dict:
    """Write findings.json and next.md, then the state (`ready`, or `failed` with `failed` set), then
    the panel_run event. Every seat without a final status is absent `panel failed: <failed>`."""
    panel = pstate.load(w.state, w.key, w.pid)
    if panel is None:
        raise ValueError(f"unknown panel: {w.pid}")
    if panel.get("state") not in LIVE:  # one writer: never write over a ready, failed, or logged panel
        raise Settled(f"panel {w.pid} is {panel.get('state')}")
    if failed:
        absent_all(seats, panel.get("roster"), failed)
    panel["seats"] = seats
    short, why = roster.shortness(_spec(w.config, panel), seats)
    absent = {s: e.get("reason") for s, e in seats.items() if e.get("status") == "absent"}
    outputs = {}
    for s, e in seats.items():
        if e.get("status") == "ok":
            text = _read_text(os.path.join(w.view, "seats", f"{s}.md"))
            if text is not None:
                outputs[s] = text
    meta = {"panel_id": w.pid, "checkpoint": panel.get("checkpoint"), "target": panel.get("target"),
            "tier": panel.get("tier")}
    try:
        doc = merge.build(meta, outputs, seats, absent, short, why, w.config["watches"], failed=failed)
    except Exception:
        if not failed:
            raise
        w.say("merge failed:\n" + traceback.format_exc())
        doc = _bare_doc(panel, seats, absent, short, why, failed)
    panel.update(state="failed" if failed else "ready", short=short)
    try:
        text = render.next_md(doc, panel, {"request_dir": pstate.request_dir(w.state, w.key), "view_dir": w.view},
                              w.config)
    except Exception:
        if not failed:
            raise
        w.say("next.md failed:\n" + traceback.format_exc())
        text = render.failed_next(w.pid, failed, pstate.request_dir(w.state, w.key), w.view)
    pstate.ensure_dir(w.state, w.work)
    _write_text(os.path.join(w.work, "findings.json"), json.dumps(doc))
    _write_text(os.path.join(w.view, "next.md"), text)

    def fn(p):
        if p.get("state") not in LIVE:
            raise Settled(f"panel {w.pid} is {p.get('state')}")
        p.update(state="failed" if failed else "ready", short=short, error=failed,
                 seats=json.loads(json.dumps(seats)))
    w.update(fn)
    pstate.record_run(w.config, w.state, w.key, w.pid, doc["findings_raw"], w.now())
    return doc


def _first_verification(w, pending, tier) -> None:
    """Design decision 18: wait, bounded by cap_s, for seats whose first verification still runs."""
    started = verify.spawn_verify(w.config, w.state, pending, env=_config_env(w.config))
    w.say(f"spawn_verify {pending} before launch: {started}")
    start, cap = w.now(), cap_seconds(w.config, tier)
    seats_json = os.path.join(w.state, "seats.json")
    while True:
        w.wait(VERIFY_POLL_S)
        entries = read_json(seats_json, {})
        entries = entries if isinstance(entries, dict) else {}
        if all(isinstance(entries.get(s), dict) and entries[s].get("verified") is True for s in pending):
            return
        if not roster.verify_running(w.state):
            return
        if w.now() - start >= cap:
            return


def _config_env(config) -> dict:
    return {"BALANCEWHEEL_CONFIG": config["path"]} if config.get("path") else {}


def _end_seat(w, name, rc, seats, queue, unverified, tier) -> None:
    proc, out, result = w.live[name]
    status, reason = classify(rc, _result(result))
    entry = seats[name]
    if status == "ok":
        res = _result(result) or {}
        try:
            os.replace(out, os.path.join(os.path.dirname(out), f"{name}.md"))
        except OSError:
            status, reason = "absent", "error: no output"
        else:
            entry.pop("reason", None)
            duration = res.get("duration_s")
            entry.update(status="ok", model=_text(res.get("model")), session_id=_text(res.get("session_id")),
                         duration_s=duration if isinstance(duration, (int, float)) and not isinstance(duration, bool)
                         else None)
            return
    _unlink(out)
    if reason == "interrupted":
        why = w.stop_reason()
        if why:
            reason = f"panel failed: {why}"
    entry.update(status="absent", reason=reason)
    if reason == "usage cap":
        fb = roster.standin(w.config, w.state, name, seats, tier, w.now())
        if fb:
            queue.append(fb)
    elif reason.startswith("unverified"):
        unverified.append(name)


def _round(w, runs, seats, brief_path, worktree, tier, panel) -> list:
    """Launch up to max_parallel seats at once and collect each end. Returns the unverified seats."""
    queue, unverified = list(runs), []
    max_parallel = w.config["panel"]["max_parallel"]
    seat_dir, results = os.path.join(w.view, "seats"), os.path.join(w.work, "results")
    while queue or w.live:
        w.check_stop()
        launched = False
        while queue and len(w.live) < max_parallel:
            name = queue.pop(0)
            out, result = os.path.join(seat_dir, f"{name}.md.part"), os.path.join(results, f"{name}-1.json")
            w.launch(name, seat_argv(name, render.ROUND1_PROMPT, worktree=worktree, out=out, result=result,
                                     brief_file=brief_path, deep=tier == "deep", panel_id=w.pid,
                                     lead_session=panel.get("session_id"), label=f"{panel['checkpoint']} round 1",
                                     stand_in_for=seats[name].get("stand_in_for")), out, result)
            seats[name]["status"] = "running"
            launched = True
        if launched:
            w.sync(seats)
        ended = [n for n, (proc, _, _) in w.live.items() if proc.poll() is not None]
        for name in ended:
            _end_seat(w, name, w.live[name][0].returncode, seats, queue, unverified, tier)
            del w.live[name]
        if ended:
            w.sync(seats)
        elif w.live:
            w.wait(POLL_S)
    return unverified


def _verify_after(w, unverified, seats) -> None:
    """Step 5a: start one background verify for seats with no finished verification on their key."""
    entries = read_json(os.path.join(w.state, "seats.json"), {})
    entries = entries if isinstance(entries, dict) else {}
    names = [s for s in seats if s in unverified and seats[s].get("reason") == "unverified"
             and isinstance(entries.get(s), dict) and entries[s].get("failed_probe") in roster.PENDING_PROBES]
    if names:
        started = verify.spawn_verify(w.config, w.state, names, env=_config_env(w.config))
        w.say(f"spawn_verify {names} after round 1: {started}")


def _fail_round(w, seats, why) -> dict:
    """The failure path: stop the live seats, then `_finish(failed=why)`. When that raises too, a
    minimal write marks the panel failed. Best effort: this function never raises."""
    try:
        w.say(f"round 1 failed ({why})" + _trace())
    except BaseException:
        pass
    try:
        w.terminate()
    except BaseException:
        pass
    try:
        current = pstate.load(w.state, w.key, w.pid) or {}
        if current.get("state") not in LIVE:  # finished or failed already: only a later step failed
            return _settled_doc(w, current, why)
        try:
            return _finish(w, seats, failed=why)
        except Settled:
            return _settled_doc(w, pstate.load(w.state, w.key, w.pid) or {}, why)
    except BaseException:
        return _mark_failed(w, seats, why)


def _settled_doc(w, panel, why) -> dict:
    """The doc of a panel another writer settled: its findings.json, else a bare failed doc."""
    try:
        doc = json.loads(_read_text(os.path.join(w.work, "findings.json")) or "{}")
    except ValueError:
        doc = {}
    if isinstance(doc, dict) and doc:
        return doc
    return _bare_doc({"id": w.pid}, {}, {}, True, [], panel.get("error") or why)


def absent_all(seats, roster_names, why) -> dict:
    """Every seat without a final status becomes absent `panel failed: <why>`. A panel that failed
    before its seats started has none: then each roster seat is absent, so the record still logs."""
    if not seats:
        for name in roster_names or []:
            if isinstance(name, str):
                seats[name] = {"status": "pending", "on_roster": True}
    for entry in seats.values():
        if entry.get("status") not in ("ok", "absent"):
            entry.update(status="absent", reason=f"panel failed: {why}")
    return seats


def _mark_failed(w, seats, why) -> dict:
    """Mark the panel failed with no findings.json write; each step is best effort."""
    try:
        absent_all(seats, (pstate.load(w.state, w.key, w.pid) or {}).get("roster"), why)
    except BaseException:
        absent_all(seats, [], why)
    try:
        w.say("the failed panel could not be written in full:" + _trace())
    except BaseException:
        pass
    try:  # every lead surface points at next.md: it must exist, and it must say how to log
        pstate.ensure_dir(w.state, w.view)
        _write_text(os.path.join(w.view, "next.md"),
                    render.failed_next(w.pid, why, pstate.request_dir(w.state, w.key), w.view))
    except BaseException:
        pass
    def fn(p):
        if p.get("state") in LIVE:  # never over a ready, failed, or logged panel
            p.update(state="failed", error=why, short=True, seats=json.loads(json.dumps(seats)))
    try:
        w.update(fn)
    except BaseException:
        pass
    try:
        pstate.record_run(w.config, w.state, w.key, w.pid, 0, w.now())
    except BaseException:
        pass
    absent = {s: e.get("reason") for s, e in seats.items() if e.get("status") == "absent"}
    return _bare_doc({"id": w.pid}, seats, absent, True, [], why)


def round1(config, state, key, pid, now=time.time) -> dict:
    """Run round 1 of this panel and return its findings.json document (Task 8, steps 1-6)."""
    w = _Worker(config, state, key, pid, now)
    seats = {}

    def start(p):
        w.claim(p, "round1")
        if p.get("state") not in LIVE:
            raise NoJob(pid)
        p["state"] = "running"
        w.set_job(p, beat=now())
    try:
        panel = w.update(start)
    except NoJob:  # not this worker's job: no write, and no release of another worker's job
        return {"panel_id": pid, "failed": "no job"}
    try:
        try:
            tier, worktree = panel.get("tier"), panel["worktree"]
            pstate.ensure_dir(state, w.work)
            with open(seat_brief.build(state, worktree, panel.get("paths") or []), encoding="utf-8") as f:
                seat_text = f.read()
            brief_path = os.path.join(w.work, "brief.md")
            _write_text(brief_path, render.brief(panel["checkpoint"], panel["target"], panel.get("focus"), seat_text))
            runs, seats = roster.prelaunch(config, state, panel["roster"], tier, now())
            w.sync(seats)
            # The fallbacks too: a stand-in may be needed mid-round, and it must be verified by then.
            pending = roster.pending_verify(config, state, roster.with_fallbacks(config, runs, panel["roster"], tier))
            if pending:
                _first_verification(w, pending, tier)
            pstate.ensure_dir(state, os.path.join(w.view, "seats"))
            pstate.ensure_dir(state, os.path.join(w.work, "results"))
            unverified = _round(w, runs, seats, brief_path, worktree, tier, panel)
            _verify_after(w, unverified, seats)
            return _finish(w, seats)
        except BaseException as exc:  # KeyboardInterrupt and SystemExit too: never leave the panel running
            why = w.why(exc)
            doc = _fail_round(w, seats, why)
            if not isinstance(exc, Exception):
                raise
            return doc
    finally:
        pstate.release_job(state, key, pid)


# --- one ask ---

def _set_ask(w, ask_id, claim=False, **fields) -> Optional[dict]:
    def fn(p):
        if claim:
            w.claim(p, "ask", ask_id)
        for a in p.get("asks") or []:
            if isinstance(a, dict) and a.get("id") == ask_id:
                a.update(fields)
        w.set_job(p, beat=w.now(), seat_pgids=[proc.pid for proc, _, _ in w.live.values()])
    panel = w.update(fn)
    return next((a for a in panel.get("asks") or [] if isinstance(a, dict) and a.get("id") == ask_id), None)


def ask(config, state, key, pid, ask_id, now=time.time) -> dict:
    """Run one ask: the asked seat resumes its round-1 session with the claim in the fixed frame.
    Returns the ask entry, `done` or `failed` with its reason. ValueError for an unknown ask."""
    w = _Worker(config, state, key, pid, now)
    entry, released = None, False
    try:
        try:
            if not isinstance(ask_id, str) or not ASK_ID.fullmatch(ask_id):
                raise ValueError(f"bad ask id: {ask_id!r}")
            panel = pstate.load(state, key, pid)
            if panel is None:
                raise ValueError(f"unknown panel: {pid}")
            entry = next((a for a in panel.get("asks") or [] if isinstance(a, dict) and a.get("id") == ask_id), None)
            if entry is None:
                raise ValueError(f"unknown ask: {ask_id}")
            try:
                entry = _set_ask(w, ask_id, claim=True, state="running", ts=now())
            except NoJob:  # not this worker's job: no write, and no release of another worker's job
                entry, released = None, True
                return {"id": ask_id, "state": "failed", "reason": "no job"}
            seat, claim = entry.get("seat"), entry.get("claim")
            session = _text(((panel.get("seats") or {}).get(seat) or {}).get("session_id"))
            if not isinstance(claim, str) or not claim or len(claim) > merge.CLAIM_CHARS:
                return _set_ask(w, ask_id, state="failed", reason="error: bad claim")
            if not session or not isinstance(seat, str):
                return _set_ask(w, ask_id, state="failed", reason="error: no round-1 session")
            # The answer streams to a .part file, published only when the ask is done (g18).
            answer = os.path.join(pstate.ensure_dir(state, os.path.join(w.view, "asks")), f"{ask_id}.md")
            out = answer + ".part"
            result = os.path.join(pstate.ensure_dir(state, os.path.join(w.work, "results")), f"ask-{ask_id}.json")
            w.launch(seat, seat_argv(seat, render.ASK_FRAME.format(claim=claim), worktree=panel["worktree"], out=out,
                                     result=result, session=session, deep=panel.get("tier") == "deep", panel_id=pid,
                                     lead_session=panel.get("session_id"), label=f"ask {ask_id}"), out, result)
            w.sync()
            proc = w.live[seat][0]
            while proc.poll() is None:
                w.wait(POLL_S)
            status, reason = classify(proc.returncode, _result(result))
            if status == "ok":
                try:
                    os.replace(out, answer)
                except OSError:
                    status, reason = "absent", "error: no output"
            if status != "ok":
                _unlink(out)
                if reason == "interrupted":
                    reason = w.stop_reason() or reason
            res = _result(result) or {}
            w.live.clear()
            return _set_ask(w, ask_id, state="done" if status == "ok" else "failed", reason=reason,
                            session_id=_text(res.get("session_id")))
        except BaseException as exc:
            if entry is None:
                raise
            why = w.why(exc)
            w.say(f"ask {ask_id} failed ({why})" + _trace())
            w.terminate()
            done = _set_ask(w, ask_id, state="failed", reason=why)
            if not isinstance(exc, Exception):
                raise
            return done
    finally:
        if not released:
            pstate.release_job(state, key, pid)


# --- the command ---

def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python3 -m panel.run", description="The panel worker.")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("round1", "ask"):
        s = sub.add_parser(name)
        s.add_argument("--state", required=True)
        s.add_argument("--key", required=True)
        s.add_argument("--panel", required=True)
        if name == "ask":
            s.add_argument("--ask", required=True)
    args = p.parse_args(argv)
    try:
        config = load_config()
    except ConfigError as e:
        print(f"panel.run: config error: {e}", file=sys.stderr)
        return 2
    _stop["why"] = None

    def on_signal(signum, frame):
        _stop["why"] = "stopped"

    old = {sig: signal.signal(sig, on_signal) for sig in STOP_SIGNALS}
    try:
        if args.cmd == "round1":
            return 1 if round1(config, args.state, args.key, args.panel).get("failed") else 0
        return 0 if ask(config, args.state, args.key, args.panel, args.ask).get("state") == "done" else 1
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)
        _stop["why"] = None


if __name__ == "__main__":
    sys.exit(main())
