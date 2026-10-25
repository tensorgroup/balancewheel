"""normalise.py — a runtime's raw hook payload becomes one typed Call the rules can judge.

pi: `payload` is the extension's tool_call event ({toolName, input, ...}); the event carries no
cwd, so the caller passes it. Paths come out absolute and realpath-canonicalised against cwd.
A `~` or `@` prefix keeps both readings (the tool may or may not expand it), and the rules judge
every one. Anything unrecognised, malformed, or oversized is kind "other", which the rules treat
as gray. Never guess a shape: an unknown tool is "other" with its raw name.
"""
import json
import os
from collections import namedtuple

Call = namedtuple("Call", "runtime tool kind command paths server args cwd worktree")

MAX_BYTES = 256 * 1024
_WRITE, _READ = ("write", "edit"), ("read", "grep", "find", "ls")


def _real(p):
    try:
        return os.path.realpath(p)
    except (OSError, ValueError):
        return os.path.normpath(p)


def _cands(raw, cwd):
    forms = [raw]
    if raw.startswith("~"):
        forms.append(os.path.expanduser(raw))
    if raw.startswith("@"):
        forms.append(raw[1:])
    return [_real(os.path.join(cwd, f)) for f in forms]


def normalise(runtime, payload, cwd, worktree):
    cwd, worktree = _real(cwd), _real(worktree)

    def other(tool, args=None):
        return Call(runtime, tool, "other", None, (), None, args, cwd, worktree)

    if not isinstance(payload, dict):
        return other("invalid")
    if payload.get("oversized") is True:
        return other("oversized")
    try:
        if len(json.dumps(payload, default=str)) > MAX_BYTES:
            return other("oversized")
    except (TypeError, ValueError):
        return other("invalid")
    if runtime == "agy":  # shapes arrive with the agy spike; until then every agy call is gray
        tool = payload.get("toolName") or payload.get("tool_name")
        return other(tool if isinstance(tool, str) else "unknown")
    if runtime != "pi":
        return other("unknown")
    tool, inp = payload.get("toolName"), payload.get("input")
    if not isinstance(tool, str):
        return other("unknown")
    if not isinstance(inp, dict):
        return other(tool)
    if tool == "bash":
        cmd = inp.get("command")
        if isinstance(cmd, str):
            return Call(runtime, tool, "shell", cmd, (), None, None, cwd, worktree)
        return other(tool, inp)
    if tool in _WRITE:
        raws = [inp.get(k) for k in ("path", "file_path", "filePath")]
        raws += [e.get("path") for e in inp.get("edits") or [] if isinstance(e, dict)]
        kind = "write"
    elif tool in _READ:
        raws, kind = [inp.get("path")], "read"
        if tool != "read" and raws == [None]:
            raws = [""]
    else:
        return other(tool, inp)
    raws = [r for r in raws if r is not None]
    if not all(isinstance(r, str) for r in raws):
        return other(tool, inp)
    paths = list(dict.fromkeys(p for r in raws for p in _cands(r, cwd)))
    return Call(runtime, tool, kind, None, tuple(paths), None, None, cwd, worktree)
