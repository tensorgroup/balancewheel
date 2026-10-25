"""launch.py — what bw does around a guarded lead's launch: the rules' view of the repo, the
lead-writable marker file read the safe way, and the self-test of the copied client.

bw reads git here outside the sandbox, so every call goes through handoff's safe runner.
"""
import json
import os
import secrets
import stat
import subprocess
import tempfile

from .. import sandbox
from ..handoff import _git_rc
from .rules import RuleContext

MARKER_LIMIT = 64 * 1024
HERE = os.path.dirname(os.path.realpath(__file__))
HARNESS = os.path.join(HERE, "pi_harness.mjs")
NODE_ARGV = ["node", "--no-warnings", "--experimental-strip-types"]
SELF_TEST_TIMEOUT = 30


class GuardUnavailable(Exception):
    """A guarded lead cannot start; the message is what bw prints."""


def rule_context(project, profile):
    wt = project.worktree

    def push_dest(remote, refspec):
        rc, out, _ = _git_rc(wt, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{push}")
        if rc == 0 and out:
            return out
        rc, out, _ = _git_rc(wt, "branch", "--show-current")  # a bare `push`/`HEAD` lands on the same name
        return out if rc == 0 and out else None

    def is_pristine(path):
        rel = os.path.relpath(path, wt)
        if rel.startswith(".."):
            return False
        if _git_rc(wt, "ls-files", "--error-unmatch", "--", rel)[0] != 0:
            return False
        return _git_rc(wt, "diff", "--quiet", "--no-ext-diff", "--no-textconv", "HEAD", "--", rel)[0] == 0

    return RuleContext(worktree=wt, git_dir=sandbox._git_dir(wt), common_dir=project.common_dir,
                       home=os.path.expanduser("~"), protected_branches=list(profile["protected_branches"]),
                       deploy_commands=list(profile["deploy_commands"]), push_dest=push_dest, is_pristine=is_pristine)


def read_markers(path):
    """Marker lines from the lead-writable file: no symlink followed, never blocking on a FIFO,
    regular files only, at most MARKER_LIMIT bytes. Returns one dict per non-empty line."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return []
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return []
        data = os.read(fd, MARKER_LIMIT)
    except OSError:
        return []
    finally:
        os.close(fd)
    out = []
    for line in data.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            obj = {}
        out.append(obj if isinstance(obj, dict) else {})
    return out


def clear_markers(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def marker_notice(lines, when):
    modes = {}
    for m in lines:
        k = m.get("mode") if m.get("mode") in ("shadow", "acting") else "unknown"
        modes[k] = modes.get(k, 0) + 1
    detail = ", ".join(f"{n} in {k} ({'allowed' if k == 'shadow' else 'blocked' if k == 'acting' else 'unknown'})"
                       for k, n in sorted(modes.items()))
    return f"bw: guard was unreachable {len(lines)} time(s) {when}: {detail}"


def _node_ok():
    try:
        v = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
        major, minor = (int(x) for x in v.lstrip("v").split(".")[:2])
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    return (major, minor) >= (22, 6)


def self_test(broker, runtime, guard_dir, token, mode, worktree, state_dir):
    """Send one block-class call through the copied client. Returns (error, note): error None
    when the client answered block and the broker logged the call as a self-test."""
    from . import record
    marker = "bw-self-test-" + secrets.token_hex(8)
    command = "herdr pane send " + marker
    broker.arm_self_test(marker)
    env = {k: v for k, v in os.environ.items() if not k.startswith("BW_GUARD_")}
    env.update(BW_GUARD_SOCKET=os.path.join(guard_dir, "sock"), BW_GUARD_TOKEN=token, BW_GUARD_MODE=mode,
               BW_GUARD_DIR=guard_dir)
    note = None
    try:
        if runtime == "pi":
            if not _node_ok():
                return None, "bw: node >= 22.6 not found; the pi guard client was not self-tested"
            with tempfile.TemporaryDirectory(prefix="bw-selftest-") as d:
                sc = os.path.join(d, "scenario.json")
                with open(sc, "w") as f:
                    json.dump({"event": {"type": "tool_call", "toolName": "bash", "toolCallId": "bw-self-test",
                                         "input": {"command": command}},
                               "ctx": {"hasUI": False, "cwd": worktree}}, f)
                r = subprocess.run(NODE_ARGV + [HARNESS, os.path.join(guard_dir, "pi_guard.ts"), sc],
                                   capture_output=True, text=True, env=env, timeout=SELF_TEST_TIMEOUT)
            blocked = bool((json.loads(r.stdout.strip().splitlines()[-1]).get("result") or {}).get("block"))
        elif runtime == "agy":
            payload = {"toolName": "run_shell_command", "toolInput": {"command": command}, "cwd": worktree}
            r = subprocess.run(["python3", "-I", os.path.join(guard_dir, "hook.py"), "agy"], input=json.dumps(payload),
                               capture_output=True, text=True, env=env, timeout=SELF_TEST_TIMEOUT)
            blocked = json.loads(r.stdout.strip().splitlines()[-1]).get("decision") in ("deny", "block")
        else:
            return "no guard client for runtime %s" % runtime, note
    except (OSError, ValueError, IndexError, subprocess.SubprocessError) as e:
        return "guard client self-test did not run (%s: %s)" % (type(e).__name__, e), note
    logged = any(d.get("self_test") and marker in (d.get("call") or "")
                 for d in record.load(state_dir)["decisions"])
    if not blocked:
        return "guard client self-test: the client did not block a block-class call", note
    if not logged:
        return "guard client self-test: the broker did not log the client's call", note
    return None, note
