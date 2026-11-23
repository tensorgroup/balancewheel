"""One seat run: the brief, the session, the OS sandbox, the cap, one stand-in hop, and the usage row.

`run_seat` is the core of `bw seat` and of the panel, which calls it in-process. Every return gives a
`SeatResult`. A seat that did not run gets one `ran: false` usage row; a run that happened gets one
`ran: true` row. Exit statuses: the CLI's own status, 124 on the wall-clock cap, 75 when bw refuses
or cannot start the seat.
"""
import argparse
import os
import shutil
import stat
import sys
import tempfile
import time
from collections import namedtuple

from lead import locks, proc, sandbox
from lead.guard.redact import redact
from lead.guard.review import safe
from lead.paths import load_config, state_dir
from lead.projects import resolve_project
from metrics import store
from seats import adapters, caps, sessions, verify
from seats import brief as seat_brief

SeatResult = namedtuple("SeatResult",
    "seat requested stand_in_for runtime model ran ok reason exit_code session_id mode dir duration_s "
    "tokens_in tokens_out cost_usd rate_limits answer")
REFUSED = 75
TIMEOUT = 124
INTERRUPTED = 143  # 128 + SIGTERM
REASON_MAX = 200
READING_RULE = ("## Reading rule\n\nOpen a file only to verify a claim, follow a caller, or read what the task "
                "names. Do not survey the repository.")
TASK_HEADING = "## Task"
SANDBOX_ERROR_PREFIXES = ("sandbox-exec:", "nono")
ROW_OPTIONAL = ("session_id", "stand_in_for", "tokens_in", "tokens_out", "cost_usd")


class Tampered(Exception):
    """A seat home component is a symlink or not a dir, or a credential name is not bw's link."""


def _stderr(line):
    print(line, file=sys.stderr)


def _real_dir(path, name):
    """Make `path` 0700 when absent. Refuse it when it is a symlink or not a dir (lstat, no follow)."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        os.mkdir(path, 0o700)
        st = os.lstat(path)
    if not stat.S_ISDIR(st.st_mode):
        raise Tampered(name)


def safe_home(state, seat) -> str:
    """`state_dir/seats/homes/<seat>`. Every component from `seats/` down must be a real dir, never
    a symlink. Raises Tampered with the name of the bad component."""
    path = state
    os.makedirs(state, exist_ok=True)
    for part in ("seats", "homes", seat):
        path = os.path.join(path, part)
        _real_dir(path, part)
    os.chmod(path, 0o700)
    return path


def _link_credentials(home, credentials):
    """A link in the seat home to each login file. A name is linked only when lstat shows it absent;
    an existing name that is not the expected link is refused."""
    for real, name, _ in credentials:
        d = home
        for part in name.split("/")[:-1]:
            d = os.path.join(d, part)
            _real_dir(d, name)
        link = os.path.join(home, name)
        try:
            st = os.lstat(link)
        except FileNotFoundError:
            os.symlink(real, link)
            continue
        if not stat.S_ISLNK(st.st_mode) or os.readlink(link) != real:
            raise Tampered(name)


def _write_private(path, text):
    """Write `text` to `path` atomically, mode 0600."""
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".bw-seat-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _row(r) -> dict:
    row = {"type": "usage", "seat": r.seat, "model": r.model, "mode": r.mode, "dir": r.dir,
           "duration_s": r.duration_s, "ok": r.ok, "reason": r.reason, "ran": r.ran}
    for k in ROW_OPTIONAL:
        v = getattr(r, k)
        if v is not None:
            row[k] = v
    return row


def result_json(result) -> dict:
    """The result as JSON: `{"v": 1, ...}`, every field except `answer`."""
    d = {"v": 1, **result._asdict()}
    del d["answer"]
    return d


def _error_reason(line):
    return ("error: " + safe(redact(line)))[:REASON_MAX]


def _input_text(prompt, brief_text):
    if brief_text is None:
        return prompt
    return f"{brief_text.rstrip()}\n\n{READING_RULE}\n\n{TASK_HEADING}\n\n{prompt}"


def _contained(config, state, seat, project, home, scratch, argv):
    """Step 7: the seat's grants, env, and sandbox wrap for `argv`. Returns (full argv, cleanup, env).
    Raises ValueError when a grant is refused. `run_seat` and `exec_sandboxed` share it, so a plain
    attack probe runs under exactly the seat's own containment."""
    s = config["seats"][seat]
    adapter = adapters.SEAT_ADAPTERS[s["runtime"]]
    rg = adapter.grants(s, home)
    backend = "sandbox-exec" if adapter.backend == "sandbox-exec" else sandbox.backend()
    home_dir = home if rg.private_home else os.path.expanduser("~")
    env = sandbox.seat_env(os.environ, keep=adapter.env_keys)
    env.update({"TMPDIR": scratch, "NODE_COMPILE_CACHE": scratch})
    env.update(adapter.home_env(home))
    grants = sandbox.seat_grants(project, home, rg, state, scratch,
                                 credentials=[p for p, _, refreshes in adapter.credentials if refreshes],
                                 state_dirs=adapter.state_dirs)
    full, cleanup = sandbox.wrap(argv, grants, backend, home_dir, scratch, {}, sandbox.denied_ports(config),
                                 allow_cwd=False)
    return full, cleanup, env


class _Run:
    """One attempt at one seat: steps 3 to 9. `refuse` and `finish` append the usage row."""

    def __init__(self, config, state, seat, prompt, where, opts, run, now, say, standin_for):
        self.config, self.state, self.seat, self.prompt, self.where = config, state, seat, prompt, where
        self.opts, self.run, self.now, self.say = opts, run, now, say
        self.s = config["seats"][seat]
        self.runtime = self.s["runtime"]
        self.adapter = adapters.SEAT_ADAPTERS[self.runtime]
        self.base = {"seat": seat, "requested": standin_for or seat, "stand_in_for": standin_for,
                     "runtime": self.runtime, "model": self.s["model"]}

    def _append(self, r):
        store.append(store.log_path(self.config), _row(r))
        return r

    def refuse(self, reason, d=None):
        """The seat did not run: exit 75 and one `ran: false` row."""
        return self._append(SeatResult(**self.base, ran=False, ok=False, reason=reason, exit_code=REFUSED,
                                       session_id=None, mode=None, dir=d or self.where, duration_s=0,
                                       tokens_in=None, tokens_out=None, cost_usd=None, rate_limits=None,
                                       answer=""))

    def finish(self, ok, reason, exit_code, sid, mode, d, duration, usage, answer):
        return self._append(SeatResult(**self.base, ran=True, ok=ok, reason=reason, exit_code=exit_code,
                                       session_id=sid, mode=mode, dir=d, duration_s=duration,
                                       tokens_in=usage.get("tokens_in"), tokens_out=usage.get("tokens_out"),
                                       cost_usd=usage.get("cost_usd"), rate_limits=usage.get("rate_limits"),
                                       answer=answer))

    def locked(self):
        """Step 3: the shared runtime lock before the verification check, held to the end of the run."""
        lock = locks.RuntimeLock(self.state, self.runtime)
        try:
            lock.shared()
            try:
                # The home and its code dirs exist before any seat key reads the hook manifests
                # (verify.seat_home makes them; it raises OSError on a tampered home).
                safe_home(self.state, self.seat)
                why = self.unverified()
            except (Tampered, OSError) as e:
                return self.refuse(f"sandbox: seat home tampered: {safe(e)}")
            return self.refuse(why) if why else self.target()
        finally:
            lock.release()

    def unverified(self):
        """The refusal reason when the seat has no pass on its current key, else None."""
        if self.opts["attack"] and verify.check_token(self.state, self.seat, self.now()):
            return None
        if verify.is_verified(self.config, self.state, self.seat, self.run):
            return None
        key = verify.seat_key(self.config, self.state, self.seat, self.run)
        if key.get("runtime_version") == "unreadable":
            return f"unverified: {self.runtime} version unreadable"
        return "unverified"

    def target(self):
        """Step 4: the worktree and the per-seat, per-directory lock. The hostile-repo check needs the
        run's argv, so it runs in `execute`, before the CLI starts."""
        try:
            project = resolve_project(self.where, self.config, self.run)
        except ValueError:
            return self.refuse("refused: not a git worktree")
        wt = project.worktree
        try:
            with sessions.run_lock(self.state, self.seat, wt):
                return self.home(project)
        except sessions.Busy:
            return self.refuse("busy", wt)

    def home(self, project):
        """Step 5: the seat home and the scratch dir, removed at the end."""
        wt = project.worktree
        try:
            home = safe_home(self.state, self.seat)
            _link_credentials(home, self.adapter.credentials)
            adapters.make_code_dirs(self.runtime, home)  # again: an attack run skips the seat key
        except (Tampered, OSError) as e:
            return self.refuse(f"sandbox: seat home tampered: {safe(e)}", wt)
        try:
            scratch = tempfile.mkdtemp(prefix=f"{self.seat}-", dir=sandbox.seat_scratch_root())
        except ValueError as e:
            return self.refuse(f"sandbox: {safe(e)}", wt)
        try:
            return self.execute(project, home, scratch)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def session(self, wt):
        """Step 6: the session to resume, or None for a new one."""
        sid = self.opts["session_id"]
        if sid is None and self.opts["resume"]:
            sid = sessions.get(self.state, self.seat, wt, self.s["model"])
            if sid is None:
                self.say(f"bw seat: no session for {self.seat} in {safe(wt)}; starting a new one")
        return sid if adapters.valid_session(sid) else None  # run_seat refuses an invalid explicit id

    def brief_text(self, wt):
        if self.opts["brief_file"]:
            with open(self.opts["brief_file"], encoding="utf-8", errors="replace") as f:
                return f.read()
        if self.opts["brief"]:
            with open(seat_brief.build(self.state, wt), encoding="utf-8", errors="replace") as f:
                return f.read()
        return None

    def execute(self, project, home, scratch):
        """Steps 6 to 9: the input file, the grants and wrap, the run, the parse, and the row."""
        wt = project.worktree
        sid = self.session(wt)
        mode = "resume" if sid else "new"
        text = _input_text(self.prompt, None if sid else self.brief_text(wt))
        input_path = os.path.join(scratch, "input.md")
        _write_private(input_path, text)
        deep = self.opts["deep"]
        argv = (self.adapter.resume_argv(self.s, sid, input_path, deep) if sid
                else self.adapter.argv(self.s, input_path, deep))
        hostile = self.adapter.project_code(wt, argv)  # only files this argv's ignore flags do not cover
        if hostile:
            name = safe(os.path.relpath(hostile[0], wt))
            return self.refuse(f"hostile: {name} loads code for {self.runtime}", wt)

        try:
            full, cleanup, env = _contained(self.config, self.state, self.seat, project, home, scratch, argv)
        except ValueError as e:
            return self.refuse(f"sandbox: {safe(e)}", wt)

        cap_s = self.config["panel"]["deep_cap_min" if deep else "cap_min"] * 60
        start = self.now()
        try:
            with open(input_path, encoding="utf-8") as stdin:
                r = self.run(full, cap_s, cwd=wt, env=env, stdin=stdin, out_dir=scratch)
        except (KeyboardInterrupt, SystemExit):
            self.finish(False, "interrupted", INTERRUPTED, sid, mode, wt, round(self.now() - start, 1), {}, "")
            raise
        finally:
            cleanup()
        duration = round(self.now() - start, 1)
        return self.parse(r, sid, mode, wt, duration)

    def parse(self, r, sid, mode, wt, duration):
        """Step 8 and 9: read the run with the adapter and append its row."""
        out_l, err_l = r.out.splitlines(), r.err.splitlines()
        if self.opts["transcript"]:
            _write_private(self.opts["transcript"], r.out + r.err)
        if r.rc != 0 and not r.timed_out:
            first = next((line.strip() for line in err_l if line.strip()), "")
            if first.startswith(SANDBOX_ERROR_PREFIXES):
                return self.refuse(f"sandbox: {safe(redact(first))}"[:REASON_MAX], wt)
        usage = self.adapter.usage(out_l) or {}
        answer = self.adapter.answer(out_l)
        new_sid = self.adapter.session_id(out_l, err_l) or sid
        if self.opts["out"]:
            _write_private(self.opts["out"], answer)
        if r.timed_out:
            return self.finish(False, "timeout", TIMEOUT, new_sid, mode, wt, duration, usage, answer)
        if r.rc == 0:
            if new_sid:
                sessions.put(self.state, self.seat, wt, new_sid, self.s["model"], self.now())
            return self.finish(True, "ok", 0, new_sid, mode, wt, duration, usage, answer)
        # The vendor's echo of the prompt is not an error. Only the task and the reading rule count as
        # echo: a brief's diff that holds a usage-limit line must not hide a real cap.
        echo = {line.strip() for line in "\n".join([self.prompt, READING_RULE, TASK_HEADING]).splitlines()
                if line.strip()}
        errors = [e for e in self.adapter.errors(out_l, err_l) if e.strip() not in echo]
        if self.adapter.usage_limit(errors):
            caps.arm(self.state, self.seat, self.now(), self.config["rotation"]["cap_expiry_hours"])
            return self.finish(False, "usage cap", r.rc, new_sid, mode, wt, duration, usage, answer)
        first = next((e.strip() for e in errors if e.strip()), f"{self.runtime} exited {r.rc}")
        return self.finish(False, _error_reason(first), r.rc, new_sid, mode, wt, duration, usage, answer)


def run_seat(config, state, seat, prompt, *, target=None, brief=False, brief_file=None, resume=False,
             session_id=None, deep=False, fallback=True, out=None, transcript=None, attack=False,
             run=proc.run, now=time.time, say=None, _standin_for=None) -> SeatResult:
    """One run of `seat` on `target` (default: the current dir). An unknown seat raises KeyError.
    `_standin_for` is internal: only this function's own one-hop stand-in call sets it."""
    say = say or _stderr
    s = config["seats"][seat]
    where = os.path.realpath(target or os.getcwd())
    opts = {"brief": brief, "brief_file": brief_file, "resume": resume, "session_id": session_id, "deep": deep,
            "out": out, "transcript": transcript, "attack": attack}
    attempt = _Run(config, state, seat, prompt, where, opts, run, now, say, _standin_for)
    if not s.get("enabled", True):
        return attempt.refuse("disabled")
    if session_id is not None and not adapters.valid_session(session_id):
        return attempt.refuse("refused: invalid session id")
    can_stand_in = (fallback and _standin_for is None and not os.environ.get("BW_SEAT_STANDIN")
                    and s.get("fallback") in config["seats"])

    def stand_in(why):
        fb = s["fallback"]
        say(f"bw seat: {seat} capped ({why}); {fb} stands in")
        # A stand-in starts its own session: the original's session id is not the stand-in's.
        return run_seat(config, state, fb, prompt, target=target, brief=brief, brief_file=brief_file, resume=resume,
                        session_id=None, deep=deep, fallback=fallback, out=out, transcript=transcript,
                        attack=attack, run=run, now=now, say=say, _standin_for=seat)

    why = caps.capped(config, state, seat, now())
    if why:
        refused = attempt.refuse("usage cap")
        return stand_in(why) if can_stand_in else refused
    missing = adapters.login_missing(s["runtime"], os.environ)
    if missing:
        return attempt.refuse(missing)
    result = attempt.locked()
    if result.ran and result.reason == "usage cap" and can_stand_in:
        # The original ran and has its own row: no extra `ran: false` row for it.
        return stand_in(caps.capped(config, state, seat, now()) or f"seat {seat} capped")
    return result


# ---- for the attack only (tests/attack/seat_readonly.sh) ----

INPUT_PLACEHOLDER = "{input}"  # in an exec argv: the path of the input file in the scratch dir


def _without(argv, flags):
    """`argv` without the block `flags` (as the adapter adds it), else without each flag."""
    n = len(flags)
    for i in range(len(argv) - n + 1):
        if n and argv[i:i + n] == flags:
            return argv[:i] + argv[i + n:]
    return [a for a in argv if a not in flags]


def control_argv(seat_cfg) -> list:
    """The seat's own new-session argv without its ignore flags: the hostile-repo positive control.
    INPUT_PLACEHOLDER stands for the input file."""
    adapter = adapters.SEAT_ADAPTERS[seat_cfg["runtime"]]
    return _without(adapter.argv(seat_cfg, INPUT_PLACEHOLDER, False), list(adapter.ignore_flags))


def exec_sandboxed(config, state, seat, target, argv, run=proc.run, input_text=None, now=time.time) -> proc.Result:
    """Run `argv` as a plain process (no model, no usage row) under exactly the seat's sandbox, env,
    and home, with the target's worktree as the cwd. Only an attack may do this: it needs the seat's
    attack token in `state` (verify.check_token), else rc 75. `input_text` goes to the input file in
    the scratch dir, which is stdin, and INPUT_PLACEHOLDER in `argv` becomes its path."""
    if not verify.check_token(state, seat, now()):
        return proc.Result(REFUSED, "", "no attack token", False)
    runtime = config["seats"][seat]["runtime"]
    adapter = adapters.SEAT_ADAPTERS[runtime]
    try:
        project = resolve_project(os.path.realpath(target), config, run)
    except ValueError:
        return proc.Result(REFUSED, "", "refused: not a git worktree", False)
    try:
        home = safe_home(state, seat)
        _link_credentials(home, adapter.credentials)
        adapters.make_code_dirs(runtime, home)
        scratch = tempfile.mkdtemp(prefix=f"{seat}-", dir=sandbox.seat_scratch_root())
    except (Tampered, OSError, ValueError) as e:
        return proc.Result(REFUSED, "", f"sandbox: {safe(e)}", False)
    try:
        input_path = os.path.join(scratch, "input.md")
        _write_private(input_path, input_text or "")
        argv = [a.replace(INPUT_PLACEHOLDER, input_path) for a in argv]
        try:
            full, cleanup, env = _contained(config, state, seat, project, home, scratch, argv)
        except ValueError as e:
            return proc.Result(REFUSED, "", f"sandbox: {safe(e)}", False)
        try:
            with open(input_path, encoding="utf-8") as stdin:
                return run(full, config["panel"]["cap_min"] * 60, cwd=project.worktree, env=env, stdin=stdin,
                           out_dir=scratch)
        finally:
            cleanup()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


EXEC_USAGE = ("usage: python3 -m seats.run exec --seat ID -d DIR -- argv...\n"
              "       python3 -m seats.run exec --seat ID -d DIR --control PROMPT")


def main(argv, out=sys.stdout, err=sys.stderr) -> int:
    """`exec`: one plain process under the seat's sandbox (see exec_sandboxed). It prints the stdout
    and stderr and exits with the process's status. `--control PROMPT` runs the seat's own argv
    without its ignore flags, with PROMPT as the input."""
    argv = list(argv)
    if not argv or argv[0] != "exec":
        print(EXEC_USAGE, file=err)
        return 2
    rest, cmd = argv[1:], []
    if "--" in rest:
        i = rest.index("--")
        rest, cmd = rest[:i], rest[i + 1:]
    p = argparse.ArgumentParser(prog="python3 -m seats.run exec", allow_abbrev=False)
    p.add_argument("--seat", required=True)
    p.add_argument("-d", dest="target", required=True)
    p.add_argument("--control", metavar="PROMPT")
    try:
        a = p.parse_args(rest)
    except SystemExit:
        return 2
    if bool(cmd) == (a.control is not None):
        print(EXEC_USAGE, file=err)
        return 2
    config = load_config()
    if a.seat not in config["seats"]:
        print(f"unknown seat {a.seat!r}", file=err)
        return 2
    state = state_dir(config)
    if a.control is not None:
        r = exec_sandboxed(config, state, a.seat, a.target, control_argv(config["seats"][a.seat]),
                           input_text=a.control)
    else:
        r = exec_sandboxed(config, state, a.seat, a.target, cmd)
    out.write(r.out)
    err.write(r.err)
    return TIMEOUT if r.timed_out else r.rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
