"""The one shell-out helper: every subprocess bw runs goes through `run`.

stdout and stderr go to temp files, not pipes, so a grandchild that keeps a pipe open
after the child exits (or after the kill) cannot hang the read. The child leads its own
session, so a timeout, or any exception while waiting (Ctrl-C included), kills its whole
process group. Never a shell.
"""
import os
import signal
import subprocess
import tempfile
from collections import namedtuple

Result = namedtuple("Result", "rc out err timed_out")


def _slurp(f):
    f.seek(0)
    return f.read().decode("utf-8", "replace")


def run(argv, timeout, cwd=None, env=None, stdin=subprocess.DEVNULL):
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            p = subprocess.Popen(argv, stdin=stdin, stdout=out, stderr=err, cwd=cwd, env=env,
                                 start_new_session=True)
        except (FileNotFoundError, PermissionError):
            return Result(127, "", f"{argv[0]}: not found", False)
        try:
            rc = p.wait(timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            _kill_group(p)
            rc, timed_out = 124, True
        except BaseException:
            # Interrupted while waiting (Ctrl-C in bw, a signal): the child leads its own session,
            # so nothing else would stop it — kill its group before the error surfaces.
            _kill_group(p)
            raise
        return Result(rc, _slurp(out), _slurp(err), timed_out)


def _kill_group(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        p.kill()
    p.wait()
