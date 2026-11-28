"""claude_pane.py — `bw setup claude-pane` and the live pane check at a Claude Code launch.

The live pane (`claude/live-pane/`) runs unsandboxed in every Claude session, so its only source is bw's
own checkout (ROOT): a marketplace named `balancewheel` whose directory is ROOT, and the plugin
`live-pane@balancewheel` from it. Setup refuses when ROOT lies inside a configured project or one of its
worktrees (a lead there could edit the pane), and leaves a `balancewheel` marketplace that points
anywhere else alone. `--remove` takes out only the plugin and the marketplace that point at ROOT.

`ensure` is the check `lead.runner` runs before it starts a `claude-code` lead: every read it makes
shares one CHECK_TIMEOUT_S limit; it installs a missing plugin, updates one whose version is not bw's
VERSION, and leaves a disabled one alone. It prints one `bw:` line only when it acts, and never raises.

Every `claude` call goes through `claude_cmd()`: BW_CLAUDE_CMD (a JSON list; tests point it at a fake)
or plain `claude`.
"""
import json
import os
import re
import shlex
import sys
import time
from typing import Optional

from . import proc
from .paths import ROOT
from .projects import _inside
from .version import VERSION

MARKETPLACE = "balancewheel"
PLUGIN_ID = "live-pane@balancewheel"
MIN_CLAUDE = (2, 1, 332)  # the version the pane is tested on; docs/stack.md records it
STEP_TIMEOUT_S = 20
CHECK_TIMEOUT_S = 5


class CheckFailed(Exception):
    """A read-only check could not be answered (timeout, exit status, bad JSON): never read as "absent"."""


def claude_cmd() -> list:
    """BW_CLAUDE_CMD as a JSON list of strings, else ["claude"]. A value that is set but not such a list
    raises ValueError: falling back to the real `claude` would defeat the variable's purpose."""
    raw = os.environ.get("BW_CLAUDE_CMD")
    if raw is None:
        return ["claude"]
    try:
        cmd = json.loads(raw)
    except ValueError:
        cmd = None
    if not (isinstance(cmd, list) and cmd and all(isinstance(a, str) and a for a in cmd)):
        raise ValueError("BW_CLAUDE_CMD must be a JSON list of strings")
    return cmd


def parse_version(text: str) -> Optional[tuple]:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(g) for g in m.groups()) if m else None


def _version_text(v):
    return ".".join(map(str, v))


def _detail(r):
    if r.timed_out:
        return "timed out"
    lines = [ln.strip() for ln in (r.err or r.out or "").splitlines() if ln.strip()]
    return f"exit {r.rc}" + (f": {lines[-1][:200]}" if lines else "")


def _list(run, args, timeout):
    r = run(claude_cmd() + args, timeout)
    if r.timed_out or r.rc != 0:
        raise CheckFailed(f"claude {' '.join(args)}: {_detail(r)}")
    try:
        doc = json.loads(r.out)
    except ValueError:
        raise CheckFailed(f"claude {' '.join(args)}: not JSON") from None
    if not isinstance(doc, list):
        raise CheckFailed(f"claude {' '.join(args)}: not a JSON list")
    return [e for e in doc if isinstance(e, dict)]


def installed(run=proc.run, timeout=CHECK_TIMEOUT_S) -> Optional[dict]:
    """The PLUGIN_ID entry of `claude plugin list --json` (`id`, `version`, `enabled`, ...; the user-scope
    one when there are several), or None when it is not installed. Raises CheckFailed when the list
    cannot be read."""
    found = [e for e in _list(run, ["plugin", "list", "--json"], timeout) if e.get("id") == PLUGIN_ID]
    return next((e for e in found if e.get("scope") == "user"), found[0] if found else None)


def marketplace_source(run, timeout) -> Optional[str]:
    """Where the `balancewheel` marketplace points: its directory for a directory source, else a short
    description of the source; None when there is no such marketplace. Raises CheckFailed when the list
    cannot be read."""
    for e in _list(run, ["plugin", "marketplace", "list", "--json"], timeout):
        if e.get("name") != MARKETPLACE:
            continue
        if e.get("source") == "directory" and isinstance(e.get("path"), str) and e["path"]:
            return e["path"]
        where = e.get("repo") or e.get("url") or e.get("path") or ""
        return f"{e.get('source') or 'an unknown source'} {where}".strip()
    return None


def _ours(source, root=ROOT):
    """A marketplace source is bw's when it is a directory whose real path is bw's checkout."""
    return bool(source) and os.path.isabs(source) and os.path.realpath(source) == os.path.realpath(root)


def refusal(config: Optional[dict], root: str = ROOT, run=proc.run) -> Optional[str]:
    """Why the pane must not be installed from `root`, or None: `root` lies inside a configured project
    or inside a worktree that git lists for it (each list bounded by CHECK_TIMEOUT_S; one that times out
    refuses too, since it could not be checked). A path that is not a git repository has no worktrees."""
    real = os.path.realpath(root)
    for key in (config or {}).get("projects") or {}:
        path = os.path.realpath(os.path.expanduser(key))
        if _inside(real, path):
            return _refusal_text(real, path)
        r = run(["git", "-C", path, "worktree", "list", "--porcelain"], CHECK_TIMEOUT_S)
        if r.timed_out:
            return f"refusing: the worktrees of {path} could not be listed in time; bw's checkout ({real}) may lie inside one"
        if r.rc != 0:
            continue
        for line in r.out.splitlines():
            if line.startswith("worktree "):
                wt = os.path.realpath(line[len("worktree "):])
                if _inside(real, wt):
                    return _refusal_text(real, wt)
    return None


def _refusal_text(real, path):
    return (f"refusing: bw's checkout ({real}) lies inside {path}; a lead there could edit the pane that runs "
            "in every Claude session")


def _step(run, args, say):
    """One changing `claude` call, bounded by STEP_TIMEOUT_S; True when it worked. A failure is one line."""
    r = run(claude_cmd() + args, STEP_TIMEOUT_S)
    if r.timed_out or r.rc != 0:
        say(f"{shlex.join(['claude', *args])} failed ({_detail(r)})")
        return False
    return True


def _foreign(source):
    return f"a marketplace named {MARKETPLACE} points at {source}, not this checkout"


def setup(config: Optional[dict], remove: bool = False, run=proc.run, out=sys.stdout) -> int:
    """`bw setup claude-pane [--remove]`. `config` None (a config error) skips the project check.
    Prints one line per step it ran; 0 when the pane is installed and current (or removed), else 1."""
    def say(msg):
        print(f"bw: {msg}", file=out, flush=True)

    try:
        claude_cmd()
    except ValueError as e:
        say(str(e))
        return 1
    if remove:
        return _remove(run, say)
    why = refusal(config, run=run)
    if why:
        say(why)
        return 1
    r = run(claude_cmd() + ["--version"], STEP_TIMEOUT_S)
    v = parse_version(r.out) if r.rc == 0 and not r.timed_out else None
    if v is None or v < MIN_CLAUDE:
        found = f"Claude Code {_version_text(v)}" if v else f"Claude Code's version could not be read ({_detail(r)})"
        say(f"{found}; the live pane needs {_version_text(MIN_CLAUDE)} or later, and mods cannot load before it")
        return 1
    try:
        source = marketplace_source(run, STEP_TIMEOUT_S)
    except CheckFailed as e:
        say(f"could not read Claude Code's marketplaces ({e}); nothing changed")
        return 1
    if source is not None and not _ours(source):
        say(f"{_foreign(source)}; remove it first")
        return 1
    if source is None:
        if not _step(run, ["plugin", "marketplace", "add", ROOT], say):
            return 1
        say(f"added the {MARKETPLACE} marketplace at {ROOT}")
    try:
        entry = installed(run, STEP_TIMEOUT_S)
    except CheckFailed as e:
        say(f"could not read Claude Code's plugins ({e})")
        return 1
    if entry is None:
        if not _step(run, ["plugin", "install", PLUGIN_ID], say):
            return 1
        say(f"installed {PLUGIN_ID} {VERSION}")
        return 0
    if entry.get("version") != VERSION:
        if not (_step(run, ["plugin", "marketplace", "update", MARKETPLACE], say)
                and _step(run, ["plugin", "update", PLUGIN_ID], say)):
            return 1
        say(f"updated {PLUGIN_ID} from {entry.get('version')} to {VERSION}")
        return 0
    if entry.get("enabled") is False:
        say(f"the live pane is installed and current ({VERSION}), and disabled; "
            f"`claude plugin enable {PLUGIN_ID}` turns it on")
        return 0
    say(f"the live pane is installed and current ({VERSION})")
    return 0


def _remove(run, say):
    """Uninstall the plugin and remove the marketplace, only when the marketplace points at ROOT. With no
    `balancewheel` marketplace, an installed `live-pane@balancewheel` did not come from this setup: left."""
    try:
        source = marketplace_source(run, STEP_TIMEOUT_S)
        entry = installed(run, STEP_TIMEOUT_S) if source is not None and _ours(source) else None
    except CheckFailed as e:
        say(f"could not read Claude Code's plugins ({e}); nothing changed")
        return 1
    if source is None:
        say(f"nothing to remove: no {MARKETPLACE} marketplace")
        return 0
    if not _ours(source):
        say(f"{_foreign(source)}; left it and its plugins alone")
        return 1
    if entry is not None:
        if not _step(run, ["plugin", "uninstall", PLUGIN_ID], say):
            return 1
        say(f"uninstalled {PLUGIN_ID}")
    if not _step(run, ["plugin", "marketplace", "remove", MARKETPLACE], say):
        return 1
    say(f"removed the {MARKETPLACE} marketplace")
    return 0


def _budgeted(run, seconds):
    """`run` with one shared deadline: each call's timeout is cut to the time left, and a call made with
    no time left is a timeout without running."""
    deadline = time.monotonic() + seconds

    def call(argv, timeout, **kw):
        left = deadline - time.monotonic()
        if left <= 0:
            return proc.Result(124, "", "", True)
        return run(argv, min(timeout, left), **kw)
    return call


def ensure(config: dict, run=proc.run, say=print, refused: Optional[str] = None) -> None:
    """Make sure the live pane is installed and current before a Claude Code lead starts. Does nothing
    when `claude_pane` is false, when `refused` (the runner's `refuse_inside` result) or `refusal(config)`
    refuses, when Claude Code is too old, when the `balancewheel` marketplace is not ROOT, when the plugin
    is disabled, or when a check fails or runs out of the shared CHECK_TIMEOUT_S. One line when it acts."""
    try:
        if config.get("claude_pane") is False or refused:
            return
        claude_cmd()  # a bad BW_CLAUDE_CMD: no call at all
        check = _budgeted(run, CHECK_TIMEOUT_S)
        if refusal(config, run=check):
            return
        r = check(claude_cmd() + ["--version"], CHECK_TIMEOUT_S)
        v = parse_version(r.out) if r.rc == 0 and not r.timed_out else None
        if v is None or v < MIN_CLAUDE:
            return
        source = marketplace_source(check, CHECK_TIMEOUT_S)
        if source is not None and not _ours(source):
            return
        entry = installed(check, CHECK_TIMEOUT_S)
        if entry is not None and (entry.get("enabled") is False or entry.get("version") == VERSION):
            return
        failed = []
        if entry is None:
            ok = ((source is not None or _step(run, ["plugin", "marketplace", "add", ROOT], failed.append))
                  and _step(run, ["plugin", "install", PLUGIN_ID], failed.append))
            done = f"installed the live pane plugin ({PLUGIN_ID} {VERSION})"
        else:
            ok = (_step(run, ["plugin", "marketplace", "update", MARKETPLACE], failed.append)
                  and _step(run, ["plugin", "update", PLUGIN_ID], failed.append))
            done = f"updated the live pane plugin from {entry.get('version')} to {VERSION}"
        say(f"bw: {done}" if ok else
            f"bw: the live pane plugin was not set up: {failed[-1]}; run bw setup claude-pane")
    except Exception:  # nonfatal by contract: the lead starts either way
        return
