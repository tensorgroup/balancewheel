"""runner.py — `bw lead`: one locked, job-controlled lead session per worktree, switching on cap.

Job control: the lead runs in its own process group and gets the terminal's foreground; bw
ignores the terminal's job-control signals while it waits, and takes the terminal back when the
lead's group is gone. Stopping a lead: SIGTERM to the group, a grace period, SIGKILL, then
confirm the group is gone before anything else starts in the worktree.

Lock: an flock on state_dir/locks/<worktree-key>.lock, opened without truncation, whose
descriptor the lead inherits — the kernel holds the lock until bw AND the lead are gone, so a
crashed bw cannot let a second writable lead into the same worktree.
"""
import fcntl
import json
import os
import signal
import subprocess
import sys
import termios
import tempfile
import time
import uuid

from . import containment, quota as quotamod, rotation, sandbox
from .handoff import build_handoff, checkpoint_dir, first_prompt, protocol_text
from .paths import locked_dir, read_json, write_json_atomic
from .projects import resolve_project
from .runtimes import ADAPTERS

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "metrics"))
import store  # noqa: E402

LOCKED, UNVERIFIED, NO_LEADS, ALL_CAPPED = 3, 4, 5, 6
V0_SCOPE = ("v0: automatic switching for leads that report usage through the Claude Code statusline; "
            "for other leads record a cap with `bw cap <plan>`")


class Lock:
    def __init__(self, state, key):
        os.makedirs(os.path.join(state, "locks"), exist_ok=True)
        self.path = os.path.join(state, "locks", f"{key}.lock")
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)

    def acquire(self):
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    def read(self):
        os.lseek(self.fd, 0, os.SEEK_SET)
        try:
            return json.loads(os.read(self.fd, 65536).decode() or "{}")
        except ValueError:
            return {}

    def write(self, meta):
        data = json.dumps(meta).encode()
        os.ftruncate(self.fd, 0)
        os.lseek(self.fd, 0, os.SEEK_SET)
        os.write(self.fd, data)


def _holder(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


JOB_SIGNALS = (signal.SIGTTOU, signal.SIGTTIN, signal.SIGINT, signal.SIGQUIT, signal.SIGTSTP)


def _child_setup():
    """In the lead, before exec: its own process group, and default job-control signals.
    bw ignores these while it waits, and an ignored disposition survives exec — without this
    reset the lead could not be interrupted or suspended from the keyboard."""
    os.setpgid(0, 0)
    for s in JOB_SIGNALS:
        signal.signal(s, signal.SIG_DFL)


class Session:
    """One running lead: the child process and its group."""

    def __init__(self, ctx, lead_name, handoff):
        self.ctx, self.lead_name = ctx, lead_name
        self.lead = ctx.config["leads"][lead_name]
        self.adapter = ctx.adapters[self.lead["runtime"]]
        self.id = uuid.uuid4().hex[:12]
        self.head = ctx.git_head()
        cp_dir = checkpoint_dir(ctx.state, ctx.project.worktree_key)
        os.makedirs(cp_dir, exist_ok=True)
        prompt = first_prompt(handoff, protocol_text())
        argv_prompt = prompt
        if self.adapter.prompt_mode == "file":
            path = os.path.join(cp_dir, "first-prompt.md")
            with open(path, "w") as f:
                f.write(prompt)
            ctx.say(f"bw: paste the first prompt from {path} into the lead")
            argv_prompt = None
        # pi/Kimi get the checkpoint dir through bw sandbox grants; self-contained runtimes whose
        # own sandbox stops at the worktree (Codex, agy) get it as an extra writable dir.
        argv = self.adapter.argv(self.lead, argv_prompt, [cp_dir] if self.adapter.contained_by == "self" else [])
        env = dict(os.environ, BW_CHECKPOINT=os.path.join(cp_dir, "checkpoint.md"), BW_SESSION=self.id)
        self.cleanup = lambda: None
        if self.adapter.contained_by == "bw":
            rg = self.adapter.grants(self.lead)
            home = os.path.expanduser("~")
            if rg.private_home:
                home = os.path.join(ctx.state, "homes", ctx.project.worktree_key)
                os.makedirs(home, exist_ok=True)
                _link_git_identity(home)
                rg = rg._replace(write=list(rg.write) + [home])
            scratch = os.path.realpath(tempfile.mkdtemp(prefix="bw-lead-"))
            grants = sandbox.lead_grants(ctx.project, rg, ctx.state)
            argv, self.cleanup = sandbox.wrap(argv, grants, ctx.backend, home, scratch, rg.env)
        self.argv = argv
        self.proc = subprocess.Popen(argv, cwd=ctx.project.worktree, env=env, pass_fds=(ctx.lock.fd,),
                                     preexec_fn=_child_setup)
        self.pgid = self.proc.pid
        ctx.give_terminal(self.pgid)
        model = self.lead["model"]
        resolved = False
        if self.lead["runtime"] == "claude-code":
            seen = quotamod.read_model(ctx.state, self.lead["plan"])
            if seen:
                model, resolved = seen, True
        ctx.log({"type": "session", "phase": "start", "project": ctx.project.worktree_key, "session_id": self.id,
                 "worktree": ctx.project.worktree, "lead": lead_name, "runtime": self.lead["runtime"],
                 "model": model, "resolved": resolved, "plan": self.lead["plan"]})
        ctx.lock.write({"pid": os.getpid(), "child": self.proc.pid, "lead": lead_name, "session_id": self.id,
                        "worktree": ctx.project.worktree, "started": time.time()})

    def group_alive(self):
        try:
            os.killpg(self.pgid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def stop(self, grace):
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(self.pgid, sig)
            except ProcessLookupError:
                break
            deadline = time.time() + (grace if sig == signal.SIGTERM else 5)
            while time.time() < deadline:
                self.proc.poll()
                if not self.group_alive():
                    break
                time.sleep(0.05)
            if not self.group_alive():
                break
        self.proc.wait()
        if self.group_alive():
            raise RuntimeError(f"lead process group {self.pgid} survived SIGKILL; refusing to start another lead")
        self.cleanup()


class Context:
    def __init__(self, config, state, project, lock, adapters, backend, out):
        self.config, self.state, self.project, self.lock = config, state, project, lock
        self.adapters, self.backend, self.out = adapters, backend, out
        self.tty = sys.stdin.fileno() if sys.stdin.isatty() else None

    def say(self, msg):
        print(msg, file=self.out, flush=True)

    def log(self, ev):
        store.append(store.log_path(self.config), ev)

    def git_head(self):
        r = subprocess.run(["git", "-C", self.project.worktree, "rev-parse", "HEAD"], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None

    def give_terminal(self, pgid):
        if self.tty is not None:
            try:
                os.tcsetpgrp(self.tty, pgid)
            except OSError:
                pass

    def take_terminal(self):
        if self.tty is not None:
            try:
                os.tcsetpgrp(self.tty, os.getpgrp())
            except OSError:
                pass


def _link_git_identity(home):
    """A private HOME still commits as the user: link the user's git config into it (read-only
    to the lead by the sandbox's read grant)."""
    for rel in (".gitconfig", os.path.join(".config", "git")):
        src, dst = os.path.expanduser(os.path.join("~", rel)), os.path.join(home, rel)
        if os.path.exists(src) and not os.path.lexists(dst):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.symlink(src, dst)


def _pins_path(state):
    return os.path.join(state, "leads.json")


def read_pin(state, key):
    return (read_json(_pins_path(state), {}) or {}).get(key)


def write_pin(state, key, lead):
    with locked_dir(os.path.join(state, "pins.lock.d")):  # other worktrees update the same file
        pins = read_json(_pins_path(state), {}) or {}
        if lead:
            pins[key] = lead
        else:
            pins.pop(key, None)
        write_json_atomic(_pins_path(state), pins)


def _verified(ctx, lead_name):
    lead = ctx.config["leads"][lead_name]
    ad = ctx.adapters[lead["runtime"]]
    if ad.contained_by != "bw":
        return True
    key = containment.verification_key(lead["runtime"], ad.version_argv, ctx.backend)
    return containment.is_verified(ctx.state, lead["runtime"], key)


def _rating(ctx, session_id):
    if ctx.tty is None:
        return
    ctx.say("bw: how did that session go? [g]ood / [b]ad / any other key to skip")
    old = termios.tcgetattr(ctx.tty)
    try:
        new = termios.tcgetattr(ctx.tty)
        new[3] &= ~(termios.ICANON | termios.ECHO)
        termios.tcsetattr(ctx.tty, termios.TCSANOW, new)
        ch = os.read(ctx.tty, 1).decode(errors="ignore").lower()
    finally:
        termios.tcsetattr(ctx.tty, termios.TCSANOW, old)
    if ch in ("g", "b"):
        ctx.log({"type": "rating", "session_id": session_id, "value": "good" if ch == "g" else "bad", "note": ""})


def run(config, state, cwd, use=None, auto=False, rating=True, adapters=None, backend=None, out=None):
    out = out or sys.stdout
    adapters = adapters or ADAPTERS
    project = resolve_project(cwd, config)
    profile = config["profiles"][project.profile]
    if not profile["lead_order"] and not use:
        print(f"bw: no leads configured for profile {project.profile}", file=out)
        return NO_LEADS
    lock = Lock(state, project.worktree_key)
    if not lock.acquire():
        h = _holder(lock.path)
        print(f"bw: {project.worktree} already has a lead: {h.get('lead')} (bw pid {h.get('pid')}, "
              f"lead pid {h.get('child')}, since {time.ctime(h.get('started', 0))})", file=out)
        return LOCKED
    ctx = Context(config, state, project, lock, adapters, backend or sandbox.backend(), out)
    stale = lock.read()
    if stale.get("session_id"):
        ctx.log({"type": "session", "phase": "end", "project": project.worktree_key,
                 "session_id": stale["session_id"], "reason": "lost"})
        lock.write({})  # recorded once; an early exit below must not leave it to be logged again
    if use:
        write_pin(state, project.worktree_key, use)
    elif auto:
        write_pin(state, project.worktree_key, None)
    pinned = read_pin(state, project.worktree_key)
    rot = config["rotation"]
    poll_s = float(os.environ.get("BW_PROBE_SECONDS", rot["probe_every_min"] * 60))
    grace = float(os.environ.get("BW_STOP_GRACE", 10))
    for s in JOB_SIGNALS:
        signal.signal(s, signal.SIG_IGN)

    def now_state():
        # expired caps included: a plan that was capped must clear the return threshold first
        return quotamod.read_quota(state), quotamod.read_caps(state, time.time(), rot["cap_expiry_hours"],
                                                              include_expired=True)

    q, caps = now_state()
    d = rotation.decide(profile, config["leads"], rot, None, pinned, q, caps, time.time(), "start")
    if d.notice:
        ctx.say(f"bw: {d.notice}")
    if not d.lead:
        ctx.say(f"bw: no lead started — {d.notice or 'no lead available'}")
        lock.write({})
        return ALL_CAPPED if d.reason == "none-eligible" and profile["lead_order"] else NO_LEADS
    if not _verified(ctx, d.lead):
        rt = config["leads"][d.lead]["runtime"]
        ctx.say(f"bw: {rt} has no verified containment for this version; run `bw sandbox verify {rt}`")
        return UNVERIFIED
    session, handoff, warned = None, None, False
    current = d.lead
    while True:
        session = Session(ctx, current, handoff)
        ctx.say(f"bw: {current} ({session.lead['runtime']}) leading {project.worktree} — {d.reason}")
        switched = False
        next_poll = time.time() + poll_s
        while session.proc.poll() is None:
            time.sleep(0.1)
            if time.time() < next_poll:
                continue
            next_poll = time.time() + poll_s
            # A contained lead cannot write bw's caps; its hook drops cap-signal in its own
            # checkpoint dir instead, and bw records the cap for the current plan.
            sig = os.path.join(checkpoint_dir(state, project.worktree_key), "cap-signal")
            if os.path.lexists(sig):
                os.remove(sig)
                quotamod.record_cap(state, session.lead["plan"], time.time(),
                                    expiry_hours=rot["cap_expiry_hours"], source="lead-hook")
            q, caps = now_state()
            d = rotation.decide(profile, config["leads"], rot, current, pinned, q, caps, time.time(), "running")
            if d.reason == "none-eligible":
                if not warned:
                    ctx.say(f"bw: {d.notice}")
                    warned = True
                continue
            if d.lead != current and not _verified(ctx, d.lead):
                if not warned:
                    rt = config["leads"][d.lead]["runtime"]
                    ctx.say(f"bw: {current} is capped but {d.lead} has no verified containment; "
                            f"run `bw sandbox verify {rt}` (staying on {current})")
                    warned = True
                continue
            if d.lead != current:
                # The terminal goes back to bw only after the lead's group is gone: a TUI that
                # restores the terminal on SIGTERM must still own it, or it stops on SIGTTOU.
                session.stop(grace)
                ctx.take_terminal()
                ctx.log({"type": "session", "phase": "end", "project": project.worktree_key,
                         "session_id": session.id, "reason": "switch"})
                ctx.log({"type": "lead", "project": project.worktree_key,
                         "from": {"lead": current, "runtime": session.lead["runtime"], "model": session.lead["model"]},
                         "to": {"lead": d.lead, "runtime": config["leads"][d.lead]["runtime"],
                                "model": config["leads"][d.lead]["model"]},
                         "reason": d.reason, "quota": q})
                handoff = build_handoff(project.worktree, session.head, state, project.worktree_key, [])
                ctx.say(f"bw: {current} is capped; handing off to {d.lead}")
                current, switched = d.lead, True
                break
        if switched:
            continue
        ctx.take_terminal()
        if session.group_alive():  # leftovers in the lead's group (servers, watchers) stop with it
            session.stop(grace)
        else:
            session.cleanup()
        ctx.log({"type": "session", "phase": "end", "project": project.worktree_key,
                 "session_id": session.id, "reason": "exit"})
        lock.write({})
        if rating:
            _rating(ctx, session.id)
        q, caps = now_state()
        d = rotation.decide(profile, config["leads"], rot, current, pinned, q, caps, time.time(), "boundary")
        if d.reason == "returned" and d.lead != current:
            ctx.log({"type": "lead", "project": project.worktree_key,
                     "from": {"lead": current, "runtime": config["leads"][current]["runtime"],
                              "model": config["leads"][current]["model"]},
                     "to": {"lead": d.lead, "runtime": config["leads"][d.lead]["runtime"],
                            "model": config["leads"][d.lead]["model"]},
                     "reason": "returned", "quota": q})
            ctx.say(f"bw: {d.lead} is available again — the next `bw lead` starts it")
        return 0
