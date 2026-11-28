"""selfupdate.py — bw updates its own checkout at `bw start`: `git pull --ff-only`, nothing else.

It pulls only commits already pushed to the branch's upstream, and never touches a checkout with
local changes (tracked files; untracked ones do not count), a detached HEAD, or one with no upstream:
it says why and goes on. When HEAD moved, `bw start` starts again once on the new code
(RESTARTED_ENV). `updates.self_update: false` or `--no-self-update` turns it off.
"""
import os
from collections import namedtuple

from . import proc

PULL_TIMEOUT_S = 60
GIT_TIMEOUT_S = 10
RESTARTED_ENV = "BW_SELF_UPDATED"  # "<before> <after>" in the restarted bw start; never passed on to a lead

Result = namedtuple("Result", "state before after detail")  # state: updated, current, skipped, failed


def pull_argv(root):
    return ["git", "-C", root, "pull", "--ff-only", "--quiet"]


def git_env(environ=None):
    """The user's environment without GIT_* (a GIT_DIR or GIT_WORK_TREE from a hook would point git at
    another repository), and with no prompt for a password: git fails at once instead."""
    env = {k: v for k, v in (os.environ if environ is None else environ).items() if not k.startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _reason(r):
    """git's own error line: the `fatal:` or `error:` line, not a hint before it."""
    lines = [ln.strip() for ln in ((r.err or "") + "\n" + (r.out or "")).splitlines() if ln.strip()]
    for ln in lines:
        if ln.startswith(("fatal:", "error:")):
            text = ln
            break
    else:
        text = lines[0] if lines else f"git pull exit {r.rc}"
    if "index.lock" in text:
        text += " (when no git runs there, delete that file and run bw start again)"
    return text


def update(root, run=proc.run, environ=None) -> Result:
    env = git_env(environ)

    def git(*args, timeout=GIT_TIMEOUT_S):
        return run(["git", "-C", root, *args], timeout, env=env)

    head = git("rev-parse", "--short", "HEAD")
    if head.rc != 0 or head.timed_out:
        return Result("skipped", None, None, f"{root} is not a git checkout")
    before = head.out.strip()
    if git("symbolic-ref", "-q", "HEAD").rc != 0:
        return Result("skipped", before, before, f"{root} is not on a branch (detached HEAD)")
    if git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}").rc != 0:
        return Result("skipped", before, before, "its branch has no upstream to pull from")
    dirty = git("status", "--porcelain", "--untracked-files=no")
    if dirty.rc != 0 or dirty.out.strip():
        return Result("skipped", before, before, f"{root} has local changes")
    r = run(pull_argv(root), PULL_TIMEOUT_S, env=env)
    if r.timed_out:
        return Result("failed", before, before, f"git pull gave no result after {PULL_TIMEOUT_S} s")
    if r.rc != 0:
        return Result("failed", before, before, _reason(r))
    after = git("rev-parse", "--short", "HEAD")
    after = after.out.strip() if after.rc == 0 else before
    return Result("updated" if after != before else "current", before, after, "")
