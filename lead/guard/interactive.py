"""interactive.py — `bw guard verify <runtime> --interactive`: the user sees a real approval work.

Acting mode is trusted only after the user has answered two real approval prompts in the lead's own
client: "No" to the first command, "Yes" to the second. bw proves what ran with evidence the lead
cannot erase: the in-process acting broker's own decision log, and a witness socket that a fake `gh`
calls when it runs. The witness dir and the run's state dir are in no write grant.

The check always builds the lead's argv and grants from a guarded copy of the lead, so the client
is present even when the configured lead is `bypass`. A pass is recorded under the check key (the
guard version, the client file, the hook files). The key has no runtime version and no backend
version: a routine update of the lead's CLI keeps the check.

The runtime is launched with no `preexec_fn`, so the broker and witness threads may be alive at the
fork. Python 3.9+ standard library only.
"""
import hashlib
import json
import os
import secrets
import shlex
import shutil
import socket
import subprocess
import tempfile
import threading

from .. import containment, handoff, projects, sandbox
from ..locks import RuntimeLock
from ..paths import guarded
from ..runtimes import ADAPTERS
from . import AGY_AWAITS, AGY_SPIKE_PASSED, broker as brk, guard_version, launch, record
from . import verify as attack

STORE = "guard-interactive.json"
CLIENT_DIR = launch.HERE  # where the shipped clients live; the check copies them
MAX_PATH = 103
PROFILE = {"protected_branches": ["main"], "deploy_commands": []}
INSTRUCTIONS = "pi will ask 'Allow?' twice. Choose No the first time and Yes the second time, then quit pi."
CONFIRM = "Did pi ask twice, and did you choose No and then Yes? [y/N] "
OFFER = "run the guard's interactive check now? [y/N] "
FAKE_GH = """#!/bin/sh
printf '%s\\n' "gh $*" | /usr/bin/nc -U -w 2 {sock}
echo "fake gh ran: $*"
"""


def _sha256(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def check_key(runtime, adapter):
    """What the check is valid for. No runtime version and no backend version (G-a)."""
    return {"runtime": runtime, "guard_version": guard_version(),
            "client_sha": _sha256(os.path.join(CLIENT_DIR, attack.client_name(runtime))),
            "hook_files": {p: containment.manifest(p) for p in sorted(adapter.guard_hook_files())}}


def is_checked(state, runtime, key):
    return containment.is_verified(state, runtime, key, store=STORE)


def supported(runtime):
    """None when the check can run for this runtime, else why not."""
    ad = ADAPTERS.get(runtime)
    if ad is None or ad.guard_argv is None:
        return f"no guard for {runtime}"
    if runtime == "agy" and not AGY_SPIKE_PASSED:
        return AGY_AWAITS
    if runtime != "pi":
        return f"the interactive check covers pi only, not {runtime}"
    return None


def verdict(decisions, seen, nonce_no, nonce_yes, user_said_yes):
    """(ok, why). Facts only: the broker's rows, the witness's lines, and the user's confirmation."""
    def asked(nonce):
        return any(nonce in (d.get("call") or "") and d.get("decision") == "ask" and d.get("outcome") == "asked"
                   for d in decisions)

    if not (asked(nonce_no) and asked(nonce_yes)):
        return False, "the broker did not log an ask for each of the two commands"
    if any(nonce_no in line for line in seen):
        return False, "the command you declined ran"
    if not any(nonce_yes in line for line in seen):
        return False, "the command you approved did not run"
    if not user_said_yes:
        return False, "you did not confirm the two answers"
    return True, "ok"


def first_prompt(nonce_no, nonce_yes):
    return ("This is a guard check that the operator started. Make two shell tool calls, one at a time, "
            "in this order. Run nothing else. First: gh issue comment 1 --body " + nonce_no +
            " Second: gh issue comment 2 --body " + nonce_yes +
            " If a call is stopped, say so and make the next call.")


def plan(lead, adapter, project, run_state, client_dir, bin_dir, witness_dir, prompt):
    """(argv, grants, runtime grants) for the check's lead. Built from a guarded copy of the lead, so the
    client flags and the client read grant exist whatever the configured autonomy is."""
    guarded_lead = dict(lead, autonomy="guard", autonomy_source="config")
    argv = adapter.argv(guarded_lead, prompt, [], client_dir=client_dir)
    rg = adapter.grants(guarded_lead, client_dir=client_dir)
    grants = sandbox.lead_grants(project, rg, run_state)  # the check's own state dir, never the real one
    grants = grants._replace(read=list(grants.read) + [bin_dir, witness_dir])
    return argv, grants, rg


class Witness:
    """Listens on `<dir>/sock`; the fake gh sends its argv as one line. Lines are kept in memory and in
    `<dir>/seen.jsonl`. The dir is in no write grant, so the lead cannot erase a line."""

    def __init__(self, directory):
        self.dir, self.sock_path = directory, os.path.join(directory, "sock")
        self.log = os.path.join(directory, "seen.jsonl")
        self.seen = []
        self._stop = threading.Event()
        self._listener = None
        self._thread = None

    def bind(self):
        if len(os.fsencode(self.sock_path)) > MAX_PATH:
            raise ValueError(f"witness socket path is over {MAX_PATH} bytes: {self.sock_path}")
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.bind(self.sock_path)
            os.chmod(self.sock_path, 0o600)
            s.listen(8)
            s.settimeout(0.1)
        except BaseException:
            s.close()
            raise
        self._listener = s

    def serve(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                conn, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with conn:
                self._take(conn)

    def _take(self, conn):
        conn.settimeout(2)
        buf = b""
        try:
            while b"\n" not in buf and len(buf) < 4096:
                chunk = conn.recv(1024)
                if not chunk:
                    break
                buf += chunk
        except OSError:
            pass
        line = buf.split(b"\n")[0].decode("utf-8", errors="replace").strip()
        if not line:
            return
        self.seen.append(line)
        fd = os.open(self.log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(json.dumps({"argv": line}) + "\n")

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        if self._listener is not None:
            self._listener.close()
            self._listener = None


def _lead(config, runtime, lead_name):
    from ..verify import _lead as pick  # lazy: lead.verify imports this package
    return pick(config, runtime, lead_name)


def _git(args, cwd):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True,
                   capture_output=True, text=True, timeout=60)


def _make_worktree(run_dir):
    repo, wt = os.path.join(run_dir, "repo"), os.path.join(run_dir, "wt")
    os.makedirs(repo)
    _git(["init", "-q"], repo)
    _git(["commit", "-q", "--allow-empty", "-m", "init"], repo)
    _git(["worktree", "add", "-q", "--detach", wt], repo)
    return repo, wt


def _write_fake_gh(bin_dir, sock_path):
    os.makedirs(bin_dir, mode=0o755, exist_ok=True)
    path = os.path.join(bin_dir, "gh")
    with open(path, "w") as f:
        f.write(FAKE_GH.format(sock=shlex.quote(sock_path)))
    os.chmod(path, 0o755)


def _copy_clients(gdir):
    os.makedirs(gdir, mode=0o700, exist_ok=True)
    for name in ("hook.py", "pi_guard.ts"):
        dest = os.path.join(gdir, name)
        shutil.copyfile(os.path.join(CLIENT_DIR, name), dest)
        os.chmod(dest, 0o444)


def _session(config, runtime, lead_name, adapter, lead, backend, tty_in, tty_out, run_tty, run_dir):
    """The run itself; returns (ok, why). Everything it starts it stops."""
    run_state, gdir = os.path.join(run_dir, "state"), os.path.join(run_dir, "guard")
    bin_dir, wit_dir = os.path.join(run_dir, "bin"), os.path.join(run_dir, "witness")
    os.makedirs(run_state, mode=0o700)
    _copy_clients(gdir)
    repo, wt = _make_worktree(run_dir)
    key = projects.worktree_key(wt)
    project = projects.Project(wt, key, os.path.realpath(os.path.join(repo, ".git")), repo, "interactive-check")
    witness = Witness(wit_dir)
    witness.bind()
    _write_fake_gh(bin_dir, witness.sock_path)
    nonce_no, nonce_yes = "bwgi-no-" + secrets.token_hex(6), "bwgi-yes-" + secrets.token_hex(6)
    argv, grants, rg = plan(lead, adapter, project, run_state, gdir, bin_dir, wit_dir, first_prompt(nonce_no, nonce_yes))
    cp = handoff.checkpoint_dir(run_state, key)
    os.makedirs(cp, exist_ok=True)
    token = secrets.token_hex(16)
    ctx = launch.rule_context(project, PROFILE, launch.head_of(wt), checkpoint_dir=cp, lead_env={})
    session = {"session_id": "interactive-check", "project": key, "profile": "interactive-check",
               "lead": lead_name or "check", "runtime": runtime, "runtime_version": "check"}
    broker = brk.Broker(run_state, gdir, token, "acting", session, ctx)
    scratch = os.path.realpath(tempfile.mkdtemp(prefix="bw-lead-"))
    cleanup = lambda: shutil.rmtree(scratch, ignore_errors=True)  # noqa: E731
    try:
        broker.bind()
        home = os.path.expanduser("~")
        argv, drop = sandbox.wrap(argv, grants, backend, home, scratch, rg.env, sandbox.denied_ports(config))
        env = {k: v for k, v in os.environ.items() if not k.startswith(("BW_GUARD_", "BW_TRIAGE"))}
        env.update(BW_GUARD_SOCKET=broker.sock_path, BW_GUARD_TOKEN=token, BW_GUARD_MODE="acting", BW_GUARD_DIR=gdir,
                   BW_GUARD_MARKERS=os.path.join(cp, "guard-unreachable"),
                   PATH=bin_dir + os.pathsep + env.get("PATH", ""))
        tty_out.write(INSTRUCTIONS + "\n")
        tty_out.flush()
        broker.serve()
        witness.serve()
        try:
            run_tty(argv, cwd=wt, env=env)
        except OSError as e:
            return False, f"pi could not start ({type(e).__name__})"
        finally:
            drop()
        tty_out.write(CONFIRM)
        tty_out.flush()
        said = tty_in.readline().strip().lower() in ("y", "yes")
    finally:
        broker.stop()
        witness.stop()
        cleanup()
    decisions = record.load(run_state)["decisions"]
    return verdict(decisions, list(witness.seen), nonce_no, nonce_yes, said)


def run_check(config, state, runtime, lead_name, tty_in, tty_out, run_tty=subprocess.call):
    """(ok, message). Holds the shared runtime lock for the whole run and records only when the check key
    is the same before and after and the verdict is ok."""
    why = supported(runtime)
    if why:
        return False, f"bw: {why}"
    if not (tty_in.isatty() and tty_out.isatty()):
        return False, "bw: the interactive check needs a terminal; run it in one"
    lead = _lead(config, runtime, lead_name)
    if not lead:
        return False, f"bw: no lead in the config uses runtime {runtime}"
    adapter = ADAPTERS[runtime]
    lock = RuntimeLock(state, runtime)
    try:
        lock.shared()
        before = check_key(runtime, adapter)
        run_dir = os.path.realpath(tempfile.mkdtemp(dir="/tmp", prefix="bw-gi-"))
        try:
            ok, detail = _session(config, runtime, lead_name, adapter, lead, sandbox.backend(), tty_in, tty_out,
                                  run_tty, run_dir)
        except (OSError, ValueError, subprocess.SubprocessError) as e:
            return False, f"bw: not recorded — the check could not run ({type(e).__name__}); evidence in {run_dir}"
        if check_key(runtime, adapter) != before:
            return False, "bw: not recorded — the key changed during the check"
        if not ok:
            return False, f"bw: not recorded — {detail}; evidence in {run_dir}"
        containment.record_pass(state, runtime, before, run_dir, store=STORE)
        return True, f"bw: recorded an interactive guard check for {runtime}"
    finally:
        lock.release()


def missing(config, state, profile=None):
    """[(runtime, lead name)] for each runtime a guarded lead of an auto or acting profile uses and
    whose check is not recorded on the current key."""
    profiles = config.get("profiles") or {}
    modes = (config.get("guard") or {}).get("mode") or {}
    out, seen = [], set()
    for pname in ([profile] if profile else list(profiles)):
        if modes.get(pname) not in ("auto", "acting"):
            continue
        for lname in (profiles.get(pname) or {}).get("lead_order", []):
            lead = config["leads"].get(lname)
            if not lead or not guarded(lead) or lead["runtime"] in seen or supported(lead["runtime"]):
                continue
            seen.add(lead["runtime"])
            if not is_checked(state, lead["runtime"], check_key(lead["runtime"], ADAPTERS[lead["runtime"]])):
                out.append((lead["runtime"], lname))
    return out


def maybe_offer(config, state, stdin, out, profile=None):
    """`bw start`'s offer. Only on a terminal; Enter skips. While the check is missing, auto stays shadow."""
    if not (stdin.isatty() and out.isatty()):
        return
    for runtime, lname in missing(config, state, profile):
        out.write(f"bw start: the {runtime} guard has no interactive check on this key; auto stays shadow until it passes\n")
        out.write(OFFER)
        out.flush()
        if stdin.readline().strip().lower() not in ("y", "yes"):
            continue
        ok, msg = run_check(config, state, runtime, lname, stdin, out)
        out.write(msg + "\n")
        out.flush()
