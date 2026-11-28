"""claude_pane.py — `bw setup claude-pane` and the live pane check at a Claude Code launch.

The live pane (`claude/live-pane/`) runs unsandboxed in every Claude session, and Claude Code reads its
files in place from ROOT at each session start, so a change to ROOT's files reaches the next session with
no install step. Its only source is bw's own checkout (ROOT): a marketplace named `balancewheel` whose
directory is ROOT, and the plugin `live-pane@balancewheel` from it. `refusal` says when ROOT is not safe:
ROOT lies inside a configured project or one of its worktrees, a worktree or the common git dir of ROOT's
own repository lies inside a configured project, or (at a launch) ROOT lies inside the lead's worktree or
common git dir, or the lead works in ROOT's own repository. A git call that fails for a path that holds
`.git`, or fails in any other way than "not a git repository", refuses too (a lead can corrupt git data
it may write). Setup refuses on a config error, and leaves a `balancewheel` marketplace that points
anywhere else alone. `--remove` takes out only the plugin and the marketplace that point at ROOT.

An installed `live-pane@balancewheel` whose `balancewheel` marketplace is missing, or points anywhere but
ROOT, has an unknown source and is not trusted: setup and the launch check uninstall it, and install it
again from ROOT when the marketplace can be ROOT's (missing; a foreign one stays until the user removes it).

`ensure` is the check `lead.runner` runs before it starts any lead: every read it makes shares one
CHECK_TIMEOUT_S limit. When `refusal` refuses, it uninstalls an installed plugin (one line) on every
runtime, also when `claude_pane` is false. Before a `claude-code` lead it also installs a missing plugin,
updates one whose version is not bw's VERSION, replaces an untrusted one, and leaves a disabled one alone. Before it changes anything it prints
one `bw: setting up the live pane: ...` line (and one more only on a failure or Ctrl-C). Ctrl-C stops the
step and the launch goes on. It never raises.

Every `claude` call goes through `claude_cmd()`: BW_CLAUDE_CMD (a JSON list; tests point it at a fake)
or plain `claude`. Every one runs with cwd ROOT (CLAUDE_CWD), never bw's own cwd: at `bw lead` that is the
lead's worktree, whose project `.claude/` settings a lead can write. The install names `--scope user`.
"""
import json
import os
import contextlib
import re
import shlex
import signal
import sys
import threading
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
CLAUDE_CWD = ROOT  # bw's checkout: no lead writes it (setup and the launch check refuse otherwise)
INSTALL = ["plugin", "install", "--scope", "user", PLUGIN_ID]


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
    r = run(claude_cmd() + args, timeout, cwd=CLAUDE_CWD)
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


class _Refused(Exception):
    pass


def _git(run, path, args, what):
    """`git -C path args` output, bounded by CHECK_TIMEOUT_S; None when `path` is not a git repository
    (git says so and `path` holds no `.git`). Any other failure raises _Refused: it fails closed."""
    r = run(["git", "-C", path, *args], CHECK_TIMEOUT_S)
    if r.timed_out:
        raise _Refused(f"refusing: {what} of {path} could not be read in time")
    if r.rc == 0:
        return r.out
    if "not a git repository" in (r.err or "") + (r.out or "") and not os.path.lexists(os.path.join(path, ".git")):
        return None
    raise _Refused(f"refusing: {what} of {path} could not be read ({_detail(r)})")


def _worktrees(run, path):
    out = _git(run, path, ["worktree", "list", "--porcelain"], "the worktrees")
    return [os.path.realpath(ln[len("worktree "):]) for ln in (out or "").splitlines() if ln.startswith("worktree ")]


def refusal(config: Optional[dict], root: str = ROOT, run=proc.run, project=None) -> Optional[str]:
    """Why the pane must not be installed (or stay installed) from `root`, or None. Refuses when `root`
    lies inside a configured project or a worktree that git lists for it; when a worktree or the common
    git dir of `root`'s own repository lies inside a configured project; and, for `project` (the lead
    target at a launch, also one reached through `default_profile`), when `root` lies inside its worktree
    or common git dir, or the lead works in `root`'s own repository. Each git call is bounded by
    CHECK_TIMEOUT_S and fails closed (`_git`). A configured path that is not a dir has no worktrees."""
    real = os.path.realpath(root)
    try:
        keys = [os.path.realpath(os.path.expanduser(k)) for k in (config or {}).get("projects") or {}]
        for path in keys:
            if _inside(real, path):
                return _refusal_text(real, path)
            for wt in _worktrees(run, path) if os.path.isdir(path) else []:
                if _inside(real, wt):
                    return _refusal_text(real, wt)
        own = _worktrees(run, real)
        common = _git(run, real, ["rev-parse", "--path-format=absolute", "--git-common-dir"], "the git dir")
        own += [os.path.realpath(common.strip())] if common and common.strip() else []
        for path in keys:
            for x in own:
                if _inside(x, path):
                    return (f"refusing: {x}, of the repository of bw's checkout ({real}), lies inside {path}, "
                            "a lead target; a lead there could change the pane that runs in every Claude session")
        if project is not None:
            for where in (project.worktree, project.common_dir):
                if _inside(real, os.path.realpath(where)):
                    return _refusal_text(real, os.path.realpath(where))
            if any(os.path.realpath(x) in own for x in (project.worktree, project.common_dir)):
                return (f"refusing: the lead works in {project.worktree}, of the repository of bw's checkout "
                        f"({real}); it could change the pane that runs in every Claude session")
    except _Refused as e:
        return f"{e}; bw's checkout ({real}) may lie inside a lead target"
    return None


def _refusal_text(real, path):
    return (f"refusing: bw's checkout ({real}) lies inside {path}; a lead there could edit the pane that runs "
            "in every Claude session")


def _step(run, args, say):
    """One changing `claude` call, bounded by STEP_TIMEOUT_S; True when it worked. A failure is one line."""
    r = run(claude_cmd() + args, STEP_TIMEOUT_S, cwd=CLAUDE_CWD)
    if r.timed_out or r.rc != 0:
        say(f"{shlex.join(['claude', *args])} failed ({_detail(r)})")
        return False
    return True


def _foreign(source):
    return f"a marketplace named {MARKETPLACE} points at {source}, not this checkout"


def _untrusted(source):
    """Why an installed plugin is not trusted: its marketplace is missing (None) or not ROOT."""
    if source is None:
        return f"it was installed with no {MARKETPLACE} marketplace, so its source is unknown"
    return f"its marketplace points at {source}, not this checkout"


def setup(config: Optional[dict], remove: bool = False, run=proc.run, out=sys.stdout) -> int:
    """`bw setup claude-pane [--remove]`. `config` None (a config error) refuses the install, because the
    project check cannot run; `--remove` still works. Prints one line per step it ran; 0 when the pane is
    installed and current (or removed), else 1."""
    def say(msg):
        print(f"bw: {msg}", file=out, flush=True)

    try:
        claude_cmd()
    except ValueError as e:
        say(str(e))
        return 1
    if remove:
        return _remove(run, say)
    if config is None:
        say("the config could not be read, so the project check cannot run; fix the config, then run "
            "bw setup claude-pane again")
        return 1
    why = refusal(config, run=run)
    if why:
        say(why)
        return 1
    r = run(claude_cmd() + ["--version"], STEP_TIMEOUT_S, cwd=CLAUDE_CWD)
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
    try:
        entry = installed(run, STEP_TIMEOUT_S)
    except CheckFailed as e:
        say(f"could not read Claude Code's plugins ({e}); nothing changed")
        return 1
    if source is not None and not _ours(source):
        if entry is not None:
            if not _step(run, ["plugin", "uninstall", PLUGIN_ID], say):
                return 1
            say(f"uninstalled {PLUGIN_ID}: {_untrusted(source)}")
        say(f"{_foreign(source)}; remove it first")
        return 1
    if source is None:
        # Uninstall before the add: if a later step fails, the next run still sees no marketplace (or no
        # plugin) and repairs again; ROOT's marketplace beside the old files would read as trusted. (The
        # files load in place from the marketplace folder; the version bw compares says only when to
        # run `claude plugin update`, not which files load.)
        if entry is not None:  # installed with no marketplace: its files came from somewhere unknown
            if not _step(run, ["plugin", "uninstall", PLUGIN_ID], say):
                return 1
            say(f"uninstalled {PLUGIN_ID}: {_untrusted(None)}")
            entry = None
        if not _step(run, ["plugin", "marketplace", "add", ROOT], say):
            return 1
        say(f"added the {MARKETPLACE} marketplace at {ROOT}")
    if entry is None:
        if not _step(run, INSTALL, say):
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
    """Uninstall the plugin (also an untrusted one: no marketplace, or a foreign one), and remove the
    marketplace only when it points at ROOT; a foreign `balancewheel` marketplace is left alone (exit 1)."""
    try:
        source = marketplace_source(run, STEP_TIMEOUT_S)
        entry = installed(run, STEP_TIMEOUT_S)
    except CheckFailed as e:
        say(f"could not read Claude Code's plugins ({e}); nothing changed")
        return 1
    ours = source is not None and _ours(source)
    if entry is not None:
        if not _step(run, ["plugin", "uninstall", PLUGIN_ID], say):
            return 1
        say(f"uninstalled {PLUGIN_ID}" + ("" if ours else f" ({_untrusted(source)})"))
    if source is None:
        if entry is None:
            say(f"nothing to remove: no {MARKETPLACE} marketplace and no {PLUGIN_ID}")
        return 0
    if not ours:
        say(f"{_foreign(source)}; left that marketplace alone")
        return 1
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


def ensure(config: dict, run=proc.run, say=print, refused: Optional[str] = None, project=None,
           install: bool = True) -> None:
    """The live pane check before a lead starts in `project`, on every runtime. When `refused` (the
    runner's `refuse_inside` result) or `refusal(config, project=project)` refuses, it uninstalls an
    installed plugin, with one line before and one more on a failure or Ctrl-C, also when `claude_pane`
    is false; it says nothing when it cannot read the plugin list. Else, only with `install` (a
    `claude-code` lead) and `claude_pane` not false, it makes sure the pane is installed from ROOT and
    current: it does nothing when Claude Code is too old, when the plugin is disabled, or when a check
    fails or runs out of the shared CHECK_TIMEOUT_S, and with a foreign `balancewheel` marketplace it only
    uninstalls the plugin. One `setting up` line before it changes anything; one more on a failure or Ctrl-C."""
    try:
        claude_cmd()  # a bad BW_CLAUDE_CMD: no call at all
        check = _budgeted(run, CHECK_TIMEOUT_S)
        why = refused or refusal(config, run=check, project=project)
        if why:
            try:
                entry = installed(check, CHECK_TIMEOUT_S)
            except CheckFailed:
                return
            if entry is not None:
                _run_plan(run, say, f"uninstalling {PLUGIN_ID} ({why})", [["plugin", "uninstall", PLUGIN_ID]],
                          "the live pane plugin could not be uninstalled", "bw setup claude-pane --remove")
            return
        if not install or config.get("claude_pane") is False:
            return
        r = check(claude_cmd() + ["--version"], CHECK_TIMEOUT_S, cwd=CLAUDE_CWD)
        v = parse_version(r.out) if r.rc == 0 and not r.timed_out else None
        if v is None or v < MIN_CLAUDE:
            return
        source = marketplace_source(check, CHECK_TIMEOUT_S)
        entry = installed(check, CHECK_TIMEOUT_S)
        plan = _ensure_plan(source, entry)
        if plan is None:
            return
        what, steps = plan
        _run_plan(run, say, what, steps, "the live pane plugin was not set up", "bw setup claude-pane")
    except Exception:  # nonfatal by contract: the lead starts either way
        return


def _run_plan(run, say, what, steps, not_done, fix):
    """One `setting up` line, the steps (Ctrl-C stops them), and one more line on a failure or Ctrl-C."""
    say(f"bw: setting up the live pane: {what}…")
    failed = []
    try:
        with _ctrl_c_stops():
            ok = all(_step(run, args, failed.append) for args in steps)
    except KeyboardInterrupt:
        say(f"bw: the live pane setup was interrupted; the lead starts anyway ({fix} finishes it)")
        return
    if not ok:
        say(f"bw: {not_done}: {failed[-1]}; run {fix}")


def _ensure_plan(source, entry):
    """(what, steps) for the launch check, or None when there is nothing to do. A disabled plugin is the
    user's choice and stays as it is; an untrusted one is uninstalled first."""
    if entry is not None and entry.get("enabled") is False:
        return None
    add = [["plugin", "marketplace", "add", ROOT]] if source is None else []
    install = [INSTALL]
    uninstall = [["plugin", "uninstall", PLUGIN_ID]]
    if source is not None and not _ours(source):
        if entry is None:
            return None
        return f"uninstalling {PLUGIN_ID} ({_untrusted(source)})", uninstall
    if entry is None:
        return f"installing {PLUGIN_ID} {VERSION} from this checkout", add + install
    if source is None:
        return (f"installing {PLUGIN_ID} {VERSION} again from this checkout ({_untrusted(None)})",
                uninstall + add + install)  # uninstall first: see setup
    if entry.get("version") == VERSION:
        return None
    return (f"updating {PLUGIN_ID} from {entry.get('version')} to {VERSION}",
            [["plugin", "marketplace", "update", MARKETPLACE], ["plugin", "update", PLUGIN_ID]])


@contextlib.contextmanager
def _ctrl_c_stops():
    """Ctrl-C raises KeyboardInterrupt while the block runs, even where bw ignores SIGINT (bw lead does,
    while it waits on a lead); proc.run then kills the step's process group. Main thread only."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    old = signal.getsignal(signal.SIGINT)
    try:
        signal.signal(signal.SIGINT, signal.default_int_handler)  # inside the try: a SIGINT after it restores
        yield
    finally:
        while True:  # a second Ctrl-C landing here must not skip the restore
            try:
                signal.signal(signal.SIGINT, old)
                break
            except KeyboardInterrupt:
                continue
