"""service.py — the panel service: requests, the scheduler, the watchdog, `log`, and `amend`.

`bw lead` calls `drain` at each task boundary. `drain` answers the lead's requests (Task 6's files),
then calls `schedule`, which runs one job per worktree at a time (Design decision 3): a round 1 or
one `ask`, each as a `python3 -m panel.run` worker in its own session. `log` and `amend` run in the
drain itself (Design decision 4). Nothing here prints: a problem goes to `notice("panel", …, key=…)`.

The watchdog never trusts a stored pid alone (Design decision 5a). A job that this bw did not start
is judged by its beat first. Every stop sends SIGTERM to the worker's group and to every recorded
seat group, sets `job.kill_at`, and waits KILL_GRACE_S. Only a later call sends SIGKILL and then calls
`fail`, so one writer writes `findings.json`.
"""
import io
import json
import math
import os
import re
import signal
import subprocess
import sys
import time
from typing import Optional

from lead import paths, proc, quota
from lead.guard.review import safe
from panel import channel, record, render, roster, run
from panel import state as pstate
from seats import brief as seat_brief
import log as metrics_log  # metrics/, on sys.path through lead.paths
import store

ROOT = paths.ROOT
BEAT_STALE_S = 120
KILL_GRACE_S = 15
SPAWN_GRACE_S = 30
KILL_TRUST_S = 4 * KILL_GRACE_S  # how long the ids confirmed at SIGTERM stay trusted for SIGKILL
STOP_MARGIN_S = 300
MAX_REQUESTS = 20
MAX_OPEN_LINES = 10
MAX_SPAWNS = 3
NOT_OURS = "stale pid not ours"
PANEL_ARG = re.compile(r"(?<![0-9A-Za-z])p[0-9a-f]{7}(?![0-9A-Za-z])")
FOLLOW_UP_SAMPLE = 3
AMEND_KEYS = ("verdicts", "groups", "severity", "added", "notes")
WORKER = [sys.executable, "-m", "panel.run"]  # the worker argv prefix; tests replace it


# --- processes ---

class Jobs:
    """The worker processes this `bw lead` started. Made once per `bw lead` run."""

    def __init__(self):
        self._procs = {}

    def spawn(self, argv: list, log_path: str) -> int:
        with open(log_path, "ab") as log:  # the parent's copy closes at once
            proc = subprocess.Popen(argv, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True, close_fds=True)
        self._procs[proc.pid] = proc
        return proc.pid

    def alive(self, pid) -> Optional[bool]:
        """True or False for a worker this bw started; None for any other pid."""
        proc = self._procs.get(pid)
        return None if proc is None else proc.poll() is None

    def reap(self) -> None:
        for pid, proc in list(self._procs.items()):
            if proc.poll() is not None:
                del self._procs[pid]


def _ok_id(g) -> bool:
    return isinstance(g, int) and not isinstance(g, bool) and g > 1


def _groups(job: dict) -> list:
    """The process groups of a job: a scheduler worker's (its pid; it leads its session) and each
    recorded seat group. Never group 0 or 1, a negative id, or bw's own group. A foreground worker
    (`bw panel` in the user's shell) leads no group of its own: `_lone` gives its pid."""
    cands = [] if job.get("worker_exited") or job.get("owner") == "foreground" else [job.get("pid")]
    cands += list(job.get("seat_pgids") or [])
    own = os.getpgrp()
    out = []
    for g in cands:
        if _ok_id(g) and g != own and g not in out:
            out.append(g)
    return out


def _lone(job: dict) -> list:
    """The pid of a foreground worker, signalled alone with os.kill (never killpg). Never bw itself."""
    pid = job.get("pid")
    if job.get("owner") != "foreground" or job.get("worker_exited") or not _ok_id(pid) or pid == os.getpid():
        return []
    return [pid]


def stop_tree(job: dict, sig) -> None:
    for kill, ids in ((os.killpg, _groups(job)), (os.kill, _lone(job))):
        for g in ids:
            try:
                kill(g, sig)
            except (ProcessLookupError, PermissionError):
                pass


def tree_alive(job: dict) -> bool:
    """Any of the job's groups still exists. A vendor CLI in its own session is not seen here."""
    for kill, ids in ((os.killpg, _groups(job)), (os.kill, _lone(job))):
        for g in ids:
            try:
                kill(g, 0)
                return True
            except ProcessLookupError:
                continue
            except PermissionError:
                return True
    return False


def _exists(pid) -> bool:
    """A process with this pid exists (any owner). False only when the kernel says there is none."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OverflowError, ValueError):
        return True
    return True


# --- entry points ---

def own(state, project, pid) -> dict:
    """The panel when it belongs to this worktree; ValueError otherwise (P6-1)."""
    panel = pstate.load(state, project.worktree_key, pid) if isinstance(pid, str) else None
    if panel is None:
        raise ValueError(f"unknown panel {safe(str(pid))[:40]}")
    return panel


def _open_refusal(open_now) -> str:
    listed = ", ".join(f"{p['id']} {p.get('state')}" for p in open_now)
    return (f"{len(open_now)} panels are open: {listed}. Log a ready panel (follow its next.md), or wait for a "
            "running one, then send this request again with a new id.")


def open_panel(config, state, project, req, lead=None, session_id=None, now=None) -> dict:
    now = time.time() if now is None else now
    key = project.worktree_key
    profile = (config.get("profiles") or {}).get(project.profile)
    if not isinstance(profile, dict):
        raise ValueError(f"profile {safe(str(project.profile))[:40]} is not in the config")
    checkpoint = roster.checkpoint(profile, req.get("checkpoint"))
    try:
        rel = seat_brief.safe_paths(project.worktree, list(req.get("paths") or []))
    except ValueError as e:
        raise ValueError(f"paths must be repo-relative: {e}") from None
    stakes = req.get("stakes", "normal")
    tier = roster.tier(profile, stakes, bool(req.get("deep")))
    names = roster.roster(config, profile, checkpoint, tier)
    originals, fallbacks = roster.run_counts(config, names, tier)
    def check(open_now):
        if len(open_now) >= pstate.MAX_OPEN:
            raise ValueError(_open_refusal(open_now))
    # The metrics log is shared by every worktree: a new id must not name a record there (panel g08).
    taken = {p.get("id") for p in store.load(store.log_path(config))["panels"]}
    return pstate.create(state, key, {
        "worktree": project.worktree, "profile": project.profile, "checkpoint": checkpoint,
        "target": req.get("target"), "focus": req.get("focus", ""), "stakes": stakes, "paths": rel, "tier": tier,
        "roster": names, "originals": originals, "fallbacks": fallbacks, "created": now, "session_id": session_id,
        "lead": (lead or {}).get("name"), "seats": {}}, check=check, taken=taken)


def disputes(doc) -> list:
    """The dispute ids a panel accepts: its candidate disputes, and d1 to d<n> for its n finding groups
    (a lead may dispute a finding that no seat contested). So the rounds of a panel stay bounded."""
    ids = [c.get("id") for c in doc.get("candidate_disputes") or [] if isinstance(c, dict)]
    ids += [f"d{n}" for n in range(1, max(1, len(doc.get("groups") or [])) + 1)]
    return [i for n, i in enumerate(ids) if isinstance(i, str) and i not in ids[:n]]


def _new_ask(p, seat, claim, dispute, session_id, max_rounds, now, allowed=None) -> dict:
    pid = p["id"]
    if p.get("state") != "ready":
        raise ValueError(f"panel {pid} is not ready (it is {p.get('state')}); send an ask to a ready panel")
    if allowed is not None and dispute not in allowed:
        raise ValueError(f"dispute {safe(str(dispute))[:16]} is not a dispute of this panel; use one of: "
                         + ", ".join(safe(a)[:16] for a in allowed[:20]))
    shown = safe(seat)[:40]
    entry = (p.get("seats") or {}).get(seat)
    if not isinstance(entry, dict):
        raise ValueError(f"seat {shown} is not a seat of this panel")
    if entry.get("status") != "ok":
        raise ValueError(f"seat {shown} did not return in round 1")
    if not entry.get("session_id"):
        raise ValueError(f"seat {shown} has no round-1 session to resume; rule on it yourself")
    asks = [a for a in p.get("asks") or [] if isinstance(a, dict)]
    used = pstate.rounds_used(asks, dispute)
    if used >= max_rounds:
        raise ValueError(f"dispute {dispute} has had {used} rounds; rule on it yourself")
    if sum(1 for a in asks if a.get("lead_session") == session_id) >= pstate.MAX_ASKS:
        raise ValueError(f"this session has sent {pstate.MAX_ASKS} asks on panel {pid}; rule on the rest yourself")
    rnd = used + 1
    attempt = 1 + sum(1 for a in asks if (a.get("dispute"), a.get("round"), a.get("seat")) == (dispute, rnd, seat))
    ask_id = f"{dispute}-{rnd}-{seat}-{attempt}"
    if not run.ASK_ID.fullmatch(ask_id):
        raise ValueError(f"seat {shown} cannot be asked")
    return {"id": ask_id, "seat": seat, "dispute": dispute, "claim": claim, "round": rnd, "attempt": attempt,
            "state": "queued", "reason": None, "lead_session": session_id, "created": now}


def queue_ask(config, state, project, req, session_id=None, now=None) -> dict:
    seat, claim, dispute = req.get("seat"), req.get("claim"), req.get("dispute")
    channel.check_ask(seat, claim, dispute)
    now = time.time() if now is None else now
    pid = req.get("panel")
    panel = own(state, project, pid)
    allowed = disputes(_doc(state, project.worktree_key, panel)) if panel.get("state") == "ready" else None
    made = {}

    def fn(p):
        made.update(_new_ask(p, seat, claim, dispute, session_id, config["panel"]["max_rounds"], now, allowed))
        p["asks"] = list(p.get("asks") or []) + [dict(made)]
    pstate.update(state, project.worktree_key, pid, fn)
    return made


def moderator_for(config, state, lead) -> Optional[dict]:
    """The record's moderator: the lead, its runtime, and the model it runs as. None with no lead."""
    if not lead:
        return None
    model, resolved = quota.lead_model(state, lead)
    if not all(isinstance(v, str) and v for v in (model, lead.get("name"), lead.get("runtime"))):
        return None
    return {"lead": lead["name"], "runtime": lead["runtime"], "model": model, "resolved": resolved}


def _rounds(panel) -> int:
    done = [a.get("round") for a in panel.get("asks") or [] if isinstance(a, dict) and a.get("state") == "done"]
    return 1 + max([r for r in done if isinstance(r, int) and not isinstance(r, bool)], default=0)


def _doc(state, key, panel) -> dict:
    """findings.json. A failed panel may have none (its write failed too): then the seats alone."""
    path = os.path.join(pstate.work_dir(state, key, panel["id"]), "findings.json")
    text = run._read_text(path)
    if text:
        try:
            doc = json.loads(text)
        except ValueError:
            doc = None
        if isinstance(doc, dict) and (doc.get("seats") or not doc.get("failed")):
            return doc  # a failed doc with no seats (written before its seats started) is rebuilt below
    if not (panel.get("state") == "failed" or (panel.get("state") == "logged" and panel.get("error"))):
        raise ValueError(f"panel {panel['id']} has no findings; bw cannot log it")
    why = panel.get("error") or "unknown"
    seats = run.absent_all(json.loads(json.dumps(panel.get("seats") or {})), panel.get("roster"), why)
    absent = {s: e.get("reason") for s, e in seats.items() if e.get("status") == "absent"}
    doc = run._bare_doc(panel, seats, absent, True, [], why)
    try:  # written once, so a later amend reads the same doc the log was built from (panel g14)
        run._write_text(path, json.dumps(doc))
    except OSError:
        pass
    return doc


def _sample(detail) -> list:
    """The first confirmed finding of each seat, at most FOLLOW_UP_SAMPLE."""
    out, seen = [], set()
    for f in detail:
        if f["verdict"] == "confirmed" and f["seat"] not in seen:
            seen.add(f["seat"])
            out.append(f["id"])
    return out[:FOLLOW_UP_SAMPLE]


def _response(state, key, pid, rec, doc, new, sink, ignored, out) -> dict:
    text = sink.getvalue()
    if out is not None:
        out.write(text)
    follow = any(w.get("kind") == "verification-skim" for w in new)
    if follow:
        path = os.path.join(pstate.view_dir(state, key, pid), "next.md")
        body = (run._read_text(path) or "").rstrip("\n")
        section = render.follow_up(_sample(rec["findings_detail"]), pstate.request_dir(state, key), pid,
                                   render.responses_dir(pstate.view_dir(state, key, pid)))
        run._write_text(path, (body + "\n\n" if body else "") + section)
    return {"ok": True, "panel": pid, "summary": record.summary_line(rec, doc),
            "watches": [line for line in text.splitlines() if line], "ignored": ignored, "follow_up": follow}


def log_panel(config, state, project, pid, req, lead=None, out=None) -> dict:
    key = project.worktree_key
    panel = own(state, project, pid)
    st = panel.get("state")
    if st in ("queued", "running"):
        raise ValueError(f"panel {pid} is running; log it when it is ready")
    if st == "logged":
        raise ValueError(f"panel {pid} is already logged; send an amend")
    asks = pstate.open_asks(panel)
    if asks:
        ids = ", ".join(str(a.get("id")) for a in asks)
        raise ValueError(f"panel {pid} has open asks: {ids}; wait for asks, then log")
    doc = _doc(state, key, panel)
    body = {k: v for k, v in req.items() if k not in channel.ENVELOPE}
    rec, ignored = record.build(doc, body, config, moderator_for(config, state, lead), rounds=_rounds(panel))
    full = metrics_log.validate_panel(rec, config, False, panel_id=pid)
    path = store.log_path(config)
    judged = record.judged(body)

    def logged(p):
        p.update(state="logged", judged=p.get("judged") or judged, record_id=pid)
    with store.locked(path):  # the log has no duplicate-id check of its own
        if any(p["id"] == pid for p in store.load(path)["panels"]):
            # the record is in the log, but its state write was lost: mark the panel logged, so amend works
            pstate.update(state, key, pid, logged)
            raise ValueError(f"panel {pid} is already logged; send an amend")
        store.append(path, full)
    pstate.update(state, key, pid, logged)  # before the watches: a failure there leaves the panel amendable
    sink = io.StringIO()
    new, _ = metrics_log.run_check(path, config, only_panel=pid, out=sink)
    return _response(state, key, pid, rec, doc, new, sink, ignored, out)


def amend(config, state, project, pid, patch, out=None) -> dict:
    """Merge `patch` over the judged request, rebuild the detail, and amend the record (P6-16)."""
    key = project.worktree_key
    panel = own(state, project, pid)
    if panel.get("state") != "logged":
        raise ValueError(f"panel {pid} is not logged; send a log request first")
    if not isinstance(patch, dict):
        raise ValueError("patch must be an object")
    ignored = [k for k in patch if k not in AMEND_KEYS]
    new = record.merge_patch(panel.get("judged") or {}, {k: patch[k] for k in AMEND_KEYS if k in patch})
    doc = _doc(state, key, panel)
    rec, _ = record.build(doc, new, config, rounds=_rounds(panel))
    change = {"findings_detail": rec["findings_detail"], "notes": rec["notes"]}
    if "findings_after_triage" in rec:
        change["findings_after_triage"] = rec["findings_after_triage"]
    sink = io.StringIO()
    watches, _ = metrics_log.amend_panel(store.log_path(config), config, pid, change, out=sink)
    pstate.update(state, key, pid, lambda p: p.update(judged=new))
    return _response(state, key, pid, rec, doc, watches, sink, ignored, out)


# --- failure, the watchdog, and the scheduler ---

def fail(config, state, key, pid, why, now=None) -> None:
    """Fail the panel's job. A round 1 fails the panel (Design decision 13); an ask fails only that
    ask. A no-op on a round 1 whose panel is `ready`, `logged`, or `failed` already (one writer)."""
    now = time.time() if now is None else now
    panel = pstate.load(state, key, pid)
    if panel is None:
        return
    job = panel.get("job") if isinstance(panel.get("job"), dict) else {}
    if job.get("kind") == "ask":
        _fail_ask(state, key, pid, job.get("ask"), why)
        return
    if panel.get("state") not in ("queued", "running"):
        return
    w = run._Worker(config, state, key, pid, lambda: now)
    run._fail_round(w, json.loads(json.dumps(panel.get("seats") or {})), why)


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _command(pid) -> str:
    """The command line of a live process, or "" (gone, a zombie's is "<defunct>", or ps failed)."""
    r = proc.run(["ps", "-ww", "-o", "command=", "-p", str(pid)], timeout=5)
    return r.out.strip() if r.rc == 0 else ""


def started_at(pid) -> str:
    """When a live process started, as `ps` prints it, or "". With the pid it names one process."""
    r = proc.run(["ps", "-o", "lstart=", "-p", str(pid)], timeout=5)
    return r.out.strip() if r.rc == 0 else ""


def _is_worker(cmd, panel_id, owner) -> bool:
    if owner == "foreground":  # `bw panel …`; when its argv names a panel, it must be this one
        ids = PANEL_ARG.findall(cmd)
        return "bw panel" in cmd and (not ids or panel_id in ids)
    return "panel.run" in cmd and panel_id in cmd


def _is_seat(cmd) -> bool:
    if "bw seat" in cmd:
        return True
    try:
        tail = run.seat_cmd()[1:]
    except ValueError:
        return False
    return bool(tail) and all(part in cmd for part in tail)


def _trusted(job, panel_id, jobs) -> tuple:
    """(the job with only the groups bw may signal, the pids left out). A worker this bw started is
    trusted; any other pid, and every recorded seat group, must still run a panel worker or a seat."""
    j, skipped = dict(job), []
    wpid = j.get("pid")
    if isinstance(wpid, int) and not j.get("worker_exited") and jobs.alive(wpid) is None:
        # A foreground job records when its `bw panel` started: a reused pid fails that check (panel g10).
        born = j.get("pid_start")
        if not _is_worker(_command(wpid), panel_id, j.get("owner")) or (born and started_at(wpid) != born):
            skipped.append(wpid)
            j["pid"] = None
    seats = []
    for g in j.get("seat_pgids") or []:
        if not isinstance(g, int) or isinstance(g, bool):
            continue
        (seats if _is_seat(_command(g)) else skipped).append(g)
    j["seat_pgids"] = seats
    return j, skipped


def _signal(state, key, pid, job, jobs, sig) -> None:
    """SIGTERM the job's tree, never a pid that now runs something else (pid reuse). Each pid is
    confirmed once, here; the groups confirmed are stored as `kill_groups` for the SIGKILL step, which
    trusts them (a leader that ended on SIGTERM can leave members in its group)."""
    trusted, skipped = _trusted(job, pid, jobs)
    stop_tree(trusted, sig)
    groups, lone = _groups(trusted), _lone(trusted)
    notes = [f"{g}: {NOT_OURS}" for g in skipped]
    if notes:
        _say(state, key, pid, notes)

    def fn(p):
        j = p.get("job")
        if isinstance(j, dict):
            j["kill_groups"] = groups
            j["kill_pids"] = lone
            if notes:
                j["skipped"] = sorted(set(j.get("skipped") or []) | set(notes))
    pstate.update(state, key, pid, fn)


def _kill(state, key, pid, job, jobs, now) -> None:
    """SIGKILL the groups (and the foreground pid) confirmed at SIGTERM time. They are trusted only
    for KILL_TRUST_S after SIGTERM: a job stopped by a bw that died before it stored them, or long
    ago (a bw restart; the ids may be reused by now), is confirmed again instead (panel g09)."""
    fresh = _num(job.get("kill_at")) and now - job["kill_at"] <= KILL_TRUST_S
    if fresh and isinstance(job.get("kill_groups"), list):
        stop_tree({"pid": None, "seat_pgids": job["kill_groups"]}, signal.SIGKILL)
        for p in job.get("kill_pids") or []:
            if _ok_id(p) and p != os.getpid():
                try:
                    os.kill(p, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
    else:
        _signal(state, key, pid, job, jobs, signal.SIGKILL)


def _say(state, key, pid, notes) -> None:
    try:
        with open(os.path.join(pstate.work_dir(state, key, pid), "log.txt"), "a", encoding="utf-8") as f:
            f.write("".join(f"panel.service: not signalled {n}\n" for n in notes))
    except OSError:
        pass


def _stop(state, key, pid, job, why, now, jobs, **extra) -> None:
    """Record why and when, then SIGTERM the tree. The worker reads `job.stop` as its reason."""
    stopped = {}

    def fn(p):
        j = p.get("job")
        if isinstance(j, dict) and j.get("pid") == job.get("pid"):
            j.update(stop=why, kill_at=now, **extra)
            stopped.update(j)
    pstate.update(state, key, pid, fn)
    if stopped:
        _signal(state, key, pid, stopped, jobs, signal.SIGTERM)


def _job_running(panel, job) -> bool:
    """The job's work started: its round 1 set the panel `running`, or its ask is `running`."""
    if job.get("kind") == "ask":
        return any(isinstance(a, dict) and a.get("id") == job.get("ask") and a.get("state") == "running"
                   for a in panel.get("asks") or [])
    return panel.get("state") == "running"


def _watch(config, state, key, panel, jobs, now) -> None:
    pid, job = panel["id"], panel["job"]
    if _num(job.get("kill_at")):
        if now >= job["kill_at"] + KILL_GRACE_S:
            _kill(state, key, pid, job, jobs, now)
            jobs.reap()
            fail(config, state, key, pid, job.get("stop") or "panel worker stopped", now)
            pstate.release_job(state, key, pid)
        return
    wpid = job.get("pid")
    if wpid is None:
        started = job.get("started") if _num(job.get("started")) else 0.0
        if now - started > SPAWN_GRACE_S:  # a failed spawn, or a launcher that died before its pid write
            if _job_running(panel, job):  # the work began, so it is failed, not left `running` (g07)
                fail(config, state, key, pid, "panel worker stopped", now)
            pstate.release_job(state, key, pid)
        return
    alive = jobs.alive(wpid)
    beat = job.get("beat") if _num(job.get("beat")) else 0.0
    if alive is None and not _exists(wpid):  # no such process: it exited (bw did not start it)
        _stop(state, key, pid, job, "panel worker exited", now, jobs, worker_exited=True)
    elif alive is None and now - beat > BEAT_STALE_S:
        _stop(state, key, pid, job, "panel worker stopped", now, jobs)
    elif alive is False:
        jobs.reap()
        _stop(state, key, pid, job, "panel worker exited", now, jobs, worker_exited=True)
    elif _num(job.get("deadline")) and now > job["deadline"]:
        started = job.get("started") if _num(job.get("started")) else job["deadline"]
        _stop(state, key, pid, job, f"stopped after {round((job['deadline'] - started) / 60)} min", now, jobs)


def _count(v) -> int:
    return len(v) if isinstance(v, list) else v if _num(v) else 0


def _budget(config, panel, kind) -> float:
    """Design decision 5: every wave of originals, then of fallbacks, plus the first-verification wait."""
    cap = run.cap_seconds(config, panel.get("tier"))
    if kind == "ask":
        return cap + STOP_MARGIN_S
    mp = config["panel"]["max_parallel"]
    waves = math.ceil(_count(panel.get("originals")) / mp) + math.ceil(_count(panel.get("fallbacks")) / mp) + 1
    return waves * cap + STOP_MARGIN_S


def _when(v) -> float:
    return v if _num(v) else 0.0


def _next_job(state, key):
    """(kind, panel, ask id) of the oldest queued job: a queued round 1 or a ready panel's queued ask,
    by the time it was made, so an ask never waits behind a stream of new panels."""
    panels = pstate.panels(state, key)
    queued = [(_when(p.get("created")), 0, "round1", p, None) for p in panels if p.get("state") == "queued"]
    queued += [(_when(a.get("created")), 1, "ask", p, a["id"]) for p in panels
               if p.get("state") == "ready" for a in p.get("asks") or []
               if isinstance(a, dict) and a.get("state") == "queued" and isinstance(a.get("id"), str)]
    if not queued:
        return None
    _, _, kind, p, ask_id = min(queued, key=lambda t: (t[0], t[1]))
    return kind, p, ask_id


def _start(config, state, key, jobs, now, notice) -> None:
    nxt = _next_job(state, key)
    if nxt is None:
        return
    start_job(config, state, key, nxt[1], nxt[0], nxt[2], jobs, now, notice)


def start_job(config, state, key, panel, kind, ask_id, jobs, now, notice, owner="scheduler") -> bool:
    """Reserve the worktree's job, then spawn the worker for this panel. False when the slot is taken
    or the spawn failed. `bw panel --background` calls it too."""
    pid = panel["id"]
    current = pstate.load(state, key, pid) or {}
    if kind == "round1":
        if current.get("state") == "queued" and not current.get("job") and _count(current.get("spawns")) >= MAX_SPAWNS:
            fail(config, state, key, pid, "worker did not start", now)
            notice("panel", f"panel {pid}: the worker did not start {MAX_SPAWNS} times; the panel failed", key=pid)
            return False
    else:
        entry = _ask_entry(current, ask_id)
        if entry is None or entry.get("state") != "queued":
            return False
        why = None
        if _count(entry.get("spawns")) >= MAX_SPAWNS:  # ask starts are bounded like round 1 (g11)
            why = "worker did not start"
        elif pstate.rounds_used([a for a in current.get("asks") or [] if a is not entry],
                                entry.get("dispute")) >= config["panel"]["max_rounds"]:
            why = f"dispute {entry.get('dispute')} has no round left"  # re-checked before launch (g02)
        if why:
            _fail_ask(state, key, pid, ask_id, why)
            notice("panel", f"panel {pid}: ask {ask_id} failed ({why})", key=pid)
            return False
    job = {"kind": kind, "ask": ask_id, "pid": None, "owner": owner, "started": now,
           "deadline": now + _budget(config, panel, kind), "beat": now, "seat_pgids": []}
    if not pstate.reserve_job(state, key, pid, job):
        return False
    # A worker that reaches `running` is never started again, so this counts failed starts.
    if kind == "round1":
        pstate.update(state, key, pid, lambda p: p.update(spawns=_count(p.get("spawns")) + 1))
    else:
        def count(p):
            a = _ask_entry(p, ask_id)
            if a is not None:
                a["spawns"] = _count(a.get("spawns")) + 1
        pstate.update(state, key, pid, count)
    argv = list(WORKER) + [kind, "--state", state, "--key", key, "--panel", pid] + (["--ask", ask_id] if ask_id else [])
    try:
        work = pstate.ensure_dir(state, pstate.work_dir(state, key, pid))
        wpid = jobs.spawn(argv, os.path.join(work, "log.txt"))
    except Exception as e:
        try:
            pstate.release_job(state, key, pid)
        except Exception:
            pass  # the watchdog releases a job with no pid after SPAWN_GRACE_S
        notice("panel", f"panel {pid}: the worker did not start ({type(e).__name__})", key=pid)
        return False

    def fn(p):  # a worker that already released its job is not given a new one
        j = p.get("job")
        if isinstance(j, dict) and j.get("pid") is None and all(j.get(k) == job[k] for k in ("kind", "ask", "owner", "started")):
            j["pid"] = wpid
    pstate.update(state, key, pid, fn)
    return True


def _ask_entry(panel, ask_id):
    return next((a for a in panel.get("asks") or [] if isinstance(a, dict) and a.get("id") == ask_id), None)


def _fail_ask(state, key, pid, ask_id, why) -> None:
    def fn(p):
        a = _ask_entry(p, ask_id)
        if a is not None and a.get("state") in pstate.ASK_OPEN:
            a.update(state="failed", reason=why)
    pstate.update(state, key, pid, fn)


def recover(config, state, key, now) -> None:
    """Fail the work that no job holds (final review 2): a `running` panel, or a `running` ask, whose
    job is gone (its worker died after a release, or bw crashed in between). Then `log` is always
    possible. A queued one waits for the scheduler; it is not failed here."""
    for p in pstate.panels(state, key):
        if isinstance(p.get("job"), dict):
            continue
        if p.get("state") == "running":
            fail(config, state, key, p["id"], "panel worker stopped", now)
        for a in p.get("asks") or []:
            if isinstance(a, dict) and a.get("state") == "running":
                _fail_ask(state, key, p["id"], a.get("id"), "panel worker stopped")


def watch(config, state, key, jobs, now=None) -> None:
    """The watch step alone: reap, watch the held job, and recover lost work. It starts nothing.
    `bw panel` runs it when no `bw lead` runs here, so a dead job never blocks it for ever."""
    now = time.time() if now is None else now
    jobs.reap()  # a worker that ended on its own is reaped here, not left a zombie (g06)
    held = next((p for p in pstate.panels(state, key) if isinstance(p.get("job"), dict)), None)
    if held is not None:
        _watch(config, state, key, held, jobs, now)
    recover(config, state, key, now)


def _notifier(notice):
    def say(kind, text, key=None):
        if notice is None:
            return
        try:
            notice(kind, text, key=key)
        except Exception:
            pass
    return say


def schedule(config, state, key, jobs, now=None, notice=None) -> None:
    """Watch the one job of this worktree, then start the oldest queued job when none is held."""
    now = time.time() if now is None else now
    notice = _notifier(notice)
    watch(config, state, key, jobs, now)
    if not pstate.has_job(state, key):
        _start(config, state, key, jobs, now, notice)


# --- the drain ---

def _answer(config, state, project, req, lead, session_id, rid, notice) -> dict:
    try:
        kind, pid = req["type"], req.get("panel")
        if kind == "panel":
            return {"ok": True, "panel": open_panel(config, state, project, req, lead=lead, session_id=session_id)["id"]}
        if kind == "ask":
            a = queue_ask(config, state, project, req, session_id=session_id)
            answer = os.path.join(pstate.view_dir(state, project.worktree_key, pid), "asks", f"{a['id']}.md")
            return {"ok": True, "panel": pid, "ask": a["id"], "answer": answer}
        if kind == "log":
            return log_panel(config, state, project, pid, req, lead=lead)
        if kind == "amend":
            if "patch" not in req:
                raise ValueError("amend needs a patch")
            r = amend(config, state, project, pid, req["patch"])
            return dict(r, ignored=[k for k in req if k not in channel.ENVELOPE + ("patch",)] + r["ignored"])
        raise ValueError("type must be one of " + ", ".join(channel.TYPES))
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except (TypeError, KeyError, AttributeError) as e:  # a shape fault the checks missed (Design decision 15)
        return {"ok": False, "error": f"malformed request: {type(e).__name__}"}
    except Exception as e:
        notice("panel", f"bw failed on panel request {rid} ({type(e).__name__})", key=rid)
        return {"ok": False, "error": f"bw failed on this request ({type(e).__name__}); send it again with a new id"}


def _answer_all(config, state, project, lead, session_id, notice) -> None:
    key = project.worktree_key
    items, problems = channel.ingest(state, key, limit=MAX_REQUESTS)
    if problems:
        notice("panel", "; ".join(problems), key="requests")
    for rid, req in items:
        if isinstance(req, ValueError):
            obj, nonce, reused = {"ok": False, "error": str(req)}, getattr(req, "nonce", None), getattr(req, "reused", False)
        else:
            obj, nonce, reused = _answer(config, state, project, req, lead, session_id, rid, notice), req.get("nonce"), False
        try:
            channel.respond(state, key, rid, obj, nonce=nonce, reused=reused)
        except (OSError, ValueError) as e:
            notice("panel", f"bw could not answer panel request {rid} ({type(e).__name__})", key=rid)
            continue
        if reused:
            notice("panel", f"panel request id {rid} was used twice; bw refused the second request", key=rid)


def drain(config, state, project, jobs, lead=None, session_id=None, notice=None) -> None:
    """Answer the lead's requests, then schedule. Never raises; prints nothing. One drain per worktree
    runs at a time, so a reused request id is never taken twice before its response is written."""
    notice = _notifier(notice)
    try:
        key = pstate.check_key(project.worktree_key)
        with paths.locked_dir(pstate.ensure_dir(state, os.path.join(state, "panels", key, "drain"))):
            try:
                _answer_all(config, state, project, lead, session_id, notice)
            except Exception as e:
                notice("panel", f"bw could not read panel requests ({type(e).__name__})", key="requests")
            try:
                schedule(config, state, key, jobs, notice=notice)
            except Exception as e:
                notice("panel", f"the panel scheduler failed ({type(e).__name__})", key="schedule")
    except Exception as e:
        notice("panel", f"the panel drain failed ({type(e).__name__})", key="drain")


def open_lines(state, key) -> list:
    """One line per panel the lead still has to finish, at most MAX_OPEN_LINES (P6-12)."""
    out = []
    for p in pstate.unlogged(state, key)[:MAX_OPEN_LINES]:
        pid, st = p["id"], p.get("state")
        view = pstate.view_dir(state, key, pid)
        head = f"{pid} {safe(str(p.get('checkpoint')))[:40]}"
        if st == "failed":
            why = safe(str(p.get("error") or "unknown"))[:200]
            out.append(f"{head}: failed ({why}) — follow {view}/next.md, then log it")
        elif st == "ready" and not pstate.open_asks(p):
            out.append(f"{head}: ready — follow {view}/next.md, then log it")
        else:
            out.append(f"{head}: running — read {view}/status.json at task boundaries")
    return out
