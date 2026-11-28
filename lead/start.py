"""start.py — `bw start [path]`: update the tools, re-verify, read quota, pick the lead, open it.

What it prints (sections on a terminal, `bw start: ` lines elsewhere) is `lead/startout.py`'s. Steps:
  1. resolve the project; refuse when bw's own real path or its config file lies inside the
     target worktree or its common git dir (`projects.refuse_inside`, shared with `bw lead`: a
     lead could edit the next `bw lead`, or the update/version commands bw runs unsandboxed);
  2. update every tool in parallel (`lead.updates`), unless --no-update;
  3. re-verify every lead runtime's keys on a pool, one worker per runtime: a runtime is
     submitted when its tool's update finishes, or at once when no tool updates it (all of
     them with --no-update); the pool is joined before the workspace opens; then, for each runtime
     with a guarded lead, the guard eval (`lead.guard.evaluate`) runs when its key changed or its last
     result was incomplete (--no-verify: no judge call, the line says pending);
  4. refresh quota for every plan the profile touches (`lead.probes`: OpenRouter's key, one Orca
     read when a plan maps Orca; agy awaits its shape), one line per plan with its source and age, then the lead
     decision at the "start" moment;
  5. rebuild `state_dir/registry.json` (`lead.registry`), a derived snapshot; then, on a terminal only,
     offer the guard's interactive approval check for each runtime that needs it and lacks it
     (`lead.guard.interactive`; Enter skips; not with --no-verify); then, on a terminal only, offer a short
     review of a few unmarked guard decisions, at most once a day (`lead.guard.offer`; Enter skips;
     not with --no-verify);
  6. open the worktree. When stdin and stdout are terminals and the target is the worktree of the
     current directory (no path, or a path inside it), the lead starts in this terminal: `bw start`
     does every other step first, then `chdir`s and `exec`s `bw lead` (what the `.command` file runs)
     as its last act. Otherwise a Terminal tab through a `.command` file in the state dir; with
     `canvas: ghostty`, a Ghostty tab that runs the same file (through Ghostty's AppleScript), else the
     Terminal tab; with `canvas: orca`, an Orca pane when Orca runs, else the Terminal tab; neither → print the command and exit 1. A worktree whose lock is
     held already has a lead and is not opened again;
  7. verify every reachable seat whose key changed, in one detached background child; the lead
     never waits (the child reads the seat keys itself; start reads none);
  8. the cockpit hint, the cc-switch checks, the Orca checks when Orca is in use, config warnings.
`state_dir/start.json` records what happened. `--dry-run` runs only git (and `orca status`
when the canvas is Orca) and writes nothing.
"""
import os
import pwd
import re
import shlex
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from . import selfupdate
from . import ccswitch, checks, orca, probes, proc, quota, registry, rotation, runner, updates, verify as verifymod
from .status import orca_checks
from .locks import NON_RUNTIME_TOOLS, runtime_tools, tool_runtime
from .paths import ROOT, read_json, write_json_atomic
from .progress import Progress
from .projects import NotARepository, refuse_inside, resolve_project
from .rotation import Decision, decide
from .startout import BAD, INFO, OK, Report
from .guard import evaluate, gate, interactive, offer
from seats import verify as seat_verify

PASSED_ENV = ("BALANCEWHEEL_CONFIG", "BW_STATE_DIR")
ORCA_CREATE_TIMEOUT_S = 30
OPEN_TIMEOUT_S = 30
# The first Ghostty call can wait on macOS's Automation prompt; give the user time to answer it.
GHOSTTY_TIMEOUT_S = 120
# A Ghostty tab (Ghostty 1.4 AppleScript) that runs the .command file in the worktree. The shell command
# and the worktree reach the run handler as arguments, never as script text. Ghostty runs `command` as
# shell text, so the command is built (and quoted) in Python: the user's login shell, interactive, runs
# the file, as Terminal does, so the lead gets the same PATH and environment as a Terminal tab.
GHOSTTY_SCRIPT = [
    "on run argv",
    'tell application "Ghostty"',
    "set cfg to new surface configuration",
    "set command of cfg to item 1 of argv",
    "set initial working directory of cfg to item 2 of argv",
    "set wait after command of cfg to true",
    "if (count of windows) > 0 then",
    "new tab in front window with configuration cfg",
    "else",
    "new window with configuration cfg",
    "end if",
    "try",
    "activate",  # focus only: a failure here must not count as a failed open (no second launch)
    "end try",
    "end tell",
    "end run",
]


def _passed(env):
    """(name, absolute path) for each PASSED_ENV set here: the lead's shell starts in the target
    worktree, where a relative path would name something else."""
    return [(k, os.path.abspath(os.path.expanduser(env[k]))) for k in PASSED_ENV if env.get(k)]


def _lead_command(bw, env):
    assignments = [f"{k}={v}" for k, v in _passed(env)]
    return shlex.join(["env", *assignments, bw, "lead"])


def _command_file(state, project, bw, env):
    path = os.path.join(state, "start", f"{project.worktree_key}.command")
    lines = ["#!/bin/sh"]
    lines += [f"export {k}={shlex.quote(v)}" for k, v in _passed(env)]
    lines += [f"cd {shlex.quote(project.worktree)} || exit 1", f"exec {shlex.quote(bw)} lead"]
    return path, "\n".join(lines) + "\n"


def _login_shell():
    try:
        shell = pwd.getpwuid(os.getuid()).pw_shell
    except KeyError:
        shell = ""
    return shell if shell and os.path.isabs(shell) else "/bin/sh"


def _ghostty_command(path, shell=None):
    """The shell text Ghostty runs: the login shell, interactive, exec'ing the .command file."""
    return shlex.join([shell or _login_shell(), "-l", "-i", "-c", "exec " + shlex.quote(path)])


def _ghostty_argv(path, worktree, shell=None):
    return ["osascript", *[a for line in GHOSTTY_SCRIPT for a in ("-e", line)], _ghostty_command(path, shell), worktree]


# osascript error numbers bw explains: the app is missing (the script does not compile), or the user
# refused Automation access.
GHOSTTY_ERRORS = {"(-2741)": "Ghostty 1.4 or later not found",
                  "(-1743)": "Automation access to Ghostty refused (System Settings > Privacy & Security > Automation)"}


def _ghostty_detail(r):
    if r.timed_out:
        return "timed out"
    text = (r.err or "") + (r.out or "")
    for code, why in GHOSTTY_ERRORS.items():
        if code in text:
            return why
    return _detail(r)


def _write_command_file(path, content):
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o700, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o700)
    try:
        os.fchmod(fd, 0o700)
        os.write(fd, content.encode())
    finally:
        os.close(fd)
    os.replace(tmp, path)


def _detail(r):
    first = (r.err or r.out or "").strip().splitlines()
    return f"exit {r.rc}" + (f": {first[0]}" if first else "")


def _orca_argv(project, cmd):
    return ["orca", "terminal", "create", "--worktree", f"path:{project.worktree}",
            "--title", f"{os.path.basename(project.worktree)} lead", "--command", cmd]


def _tool_plan_line(name, tcfg, update):
    if tcfg.get("self_updating"):
        return f"{name}: self-updating; version only"
    if not tcfg.get("update"):
        return f"{name}: no update command; version only"
    if not update:
        return f"{name}: not updated (--no-update)"
    rt = tool_runtime(name, tcfg)
    if rt is None and name not in NON_RUNTIME_TOOLS:
        return f"{name}: not updated — no update lock, set tools.{name}.runtime"
    lock = f"lock runtime-{rt}" if rt else "no lock"
    return f"would update {name}: {shlex.join(tcfg['update'])} · {lock} · installer {updates.installer_of(tcfg)}"


KIND_WORDS = {"containment": "sandbox", "guard": "guard"}
LEAD_REASONS = {"pinned": "pinned to this worktree", "order": "first in the profile's lead order",
                "capped": "an earlier lead's plan is capped", "quota-low": "an earlier lead's quota is low",
                "pool-full": "an earlier lead is in a full quota pool"}


def _lead_reason(d, profile):
    """The decision's reason in words. "quota-low" on the profile's first lead means no lead had quota to
    spare and bw took the first, not that an earlier lead was skipped."""
    order = (profile or {}).get("lead_order") or []
    if d.reason == "quota-low" and (not order or d.lead == order[0]):
        return "every lead's quota is low; this one is first in the order"
    return LEAD_REASONS.get(d.reason, d.reason)


def _tool_line(rep, res):
    """One tool's result in plain words."""
    name, ver = res.tool, res.after
    if res.result == "updated":
        rep.ok("Updates", f"{name}: updated {res.before} → {res.after}")
    elif res.result == "current":
        rep.ok("Updates", f"{name}: {ver}, current")
    elif res.result == "failed":
        rep.bad("Updates", f"{name}: update failed ({res.detail})")
    elif res.result == "timed-out":
        rep.bad("Updates", f"{name}: update timed out ({res.detail})")
    elif res.result == "unreadable" and res.after != updates.UNREADABLE:  # only the old version was unreadable
        rep.ok("Updates", f"{name}: updated to {res.after}; the old version could not be read")
    elif res.result == "unreadable":
        rep.bad("Updates", f"{name}: updated, but the new version cannot be read ({res.detail})")
    elif res.result == "no-update-command":
        rep.line("Updates", f"{name}: {ver}, no update command")
    elif res.result == "self-updating":
        rep.line("Updates", f"{name}: {ver}, updates itself")
    else:  # skipped-locked, skipped-interval, skipped-no-lock
        rep.line("Updates", f"{name}: {ver}, not updated ({res.detail})")


def _no_lock_line(rep, config, runtimes, rt_tool, tools):
    """One line for every runtime that no tool updates (it was one line each)."""
    missing = [rt for rt in runtimes if rt not in rt_tool]
    if not missing:
        return
    if not any((t or {}).get("update") for t in tools.values()):
        rep.line("Updates", 'skipped: your config lists no update commands (see "tools" in docs/leads.md)')
    else:
        rep.line("Updates", f"not updated: no tool in your config updates {', '.join(missing)} "
                            '(set tools.<name>.runtime; see "tools" in docs/leads.md)')


def _item_line(rep, item):
    label = f"{item.runtime} {KIND_WORDS.get(item.kind, item.kind)}"
    reason = item.reason or ""
    if item.state == "verified":
        rep.ok("Checks", f"{label}: passed earlier, nothing changed since")
    elif item.state == "passed":
        rep.ok("Checks", f"{label}: passed")
    elif item.state == "pending":
        rep.line("Checks", f"{label}: not tested (--no-verify)" if reason == "--no-verify" else f"{label}: not tested: {reason}")
    else:
        lines = [ln.strip() for ln in reason.splitlines() if ln.strip()]
        why = re.sub(r"^bw: (not recorded — )?(failed: )?", "", lines[-1]) if lines else ""
        found = re.findall(r"evidence in (.+?)\s*$", reason, re.M)  # the path may hold spaces
        why = why.replace("; see the evidence", "") if found else why
        rep.bad("Checks", f"{label}: failed" + (f": {why}" if why else "") + (f". Evidence: {found[-1]}" if found else ""))


def _wait_text(runtime, todo, updated):
    """What an attack test is about to do and why, for the line printed before the first one starts."""
    kinds = " and ".join(KIND_WORDS.get(k, k) for k, _ in todo)
    reasons = []
    for kind, why in todo:
        word = KIND_WORDS.get(kind, kind)
        if why == "never tested":
            text = f"{runtime}'s {word} has not been tested yet"
        elif why == "version changed":
            text = f"{runtime} was just updated" if runtime in updated else f"{runtime}'s version changed since its last test"
        else:
            text = f"{runtime}'s settings changed since its last test"
        if text not in reasons:
            reasons.append(text)
    return (f"testing the {runtime} lead's {kinds}. This takes about a minute and uses a little model quota, "
            f"because {' and '.join(reasons)}. Please wait.")


SHOWN_REPOS = 8


def _shown_path(path, home):
    """`path` as the user types it: `~/…` under their home, shell-quoted."""
    home = os.path.realpath(home).rstrip(os.sep) if home else ""
    if home and path.startswith(home + os.sep):
        return "~/" + shlex.quote(path[len(home) + 1:])
    return shlex.quote(path)


def not_a_repository_help(e, home=None):
    """The `bw start` message for a folder that is not in a git repository: what is wrong, what to do,
    and, when the folder holds repositories, the exact commands that start one of them."""
    home = home if home is not None else os.path.expanduser("~")
    lines = [str(e),
             "bw start opens a lead on one git repository. Run it inside a repository, "
             "or give the repository's path."]
    if e.repos:
        repos = e.repos
        lines.append("Repositories in this folder, most recently used first:")
        lines += [f"  bw start {_shown_path(r, home)}" for r in repos[:SHOWN_REPOS]]
        if len(repos) > SHOWN_REPOS:
            lines.append(f"  … and {len(repos) - SHOWN_REPOS} more")
    else:
        lines.append("Example: bw start ~/code/my-project")
    lines.append("New project: git init <dir>, then bw start <dir>.")
    return "\n".join(lines)


def _terminal(stream):
    return bool(getattr(stream, "isatty", lambda: False)())


def _foreground(stream, tcgetpgrp=os.tcgetpgrp, getpgrp=os.getpgrp):
    """Whether this process owns the terminal's foreground process group. A job started with `&` does not,
    and must not take the terminal from the shell. A stream with no descriptor cannot be in the background."""
    try:
        fd = stream.fileno()
    except (AttributeError, OSError, ValueError):
        return True
    try:
        return tcgetpgrp(fd) == getpgrp()
    except OSError:
        return False


def _in_this_worktree(config, project, run, cwd):
    """Whether the current directory is in the target's worktree (a path inside it counts)."""
    try:
        return resolve_project(cwd or os.getcwd(), config, run).worktree == project.worktree
    except (ValueError, OSError):
        return False


def start(config, state, path, run=proc.run, out=sys.stdout, now=None, dry_run=False, verify=True, update=True,
          bw_path=None, env=os.environ, fetch=probes._fetch, clock=None, stdin=None, tty=None, width=None,
          cwd=None, chdir=os.chdir, execve=os.execve, foreground=_foreground, tab=False, self_update=False,
          restart=None) -> int:
    """`clock` (default time.time; a fixed `now` freezes it) is read at the start, again before
    quota (step 4, after updates and attacks that can take minutes), and again before the registry.
    `tty` (default: `out` is a terminal) picks the sectioned layout; `width` its line width.
    With stdin and stdout on a terminal and the target the worktree of `cwd` (default: the current
    directory) and `tab` is not set, the lead starts in this process (`chdir`, then `execve`, as the last step) instead of
    in a new tab."""
    if clock is None:
        clock = time.time if now is None else (lambda: now)
    now = clock()
    if tty is None:
        tty = bool(getattr(out, "isatty", lambda: False)())
    rep = Report(out, tty=tty, width=width)

    # 1. the project, and bw itself outside it
    try:
        project = resolve_project(path or cwd or os.getcwd(), config, run)
    except NotARepository as e:
        for line in not_a_repository_help(e, (env or {}).get("HOME")).splitlines():
            rep.say(line)  # every plain line keeps the `bw start: ` prefix
        return 2
    except ValueError as e:
        rep.say(str(e))
        return 2
    bw = os.path.realpath(bw_path or os.path.join(ROOT, "bin", "bw"))
    refusal = refuse_inside(project, bw, config.get("path"))
    if refusal:
        rep.say(refusal)
        return 2
    profile = (config.get("profiles") or {}).get(project.profile) or {}
    runtimes = verifymod.lead_runtimes(config)
    rt_tool = runtime_tools(config)
    tools = orca.active_tools(config)
    plans = rotation.plans_touched(config, profile, runner.read_pin(state, project.worktree_key))
    stdin = stdin or sys.stdin
    here = (not tab and _terminal(out) and _terminal(stdin) and foreground(stdin) and _in_this_worktree(config, project, run, cwd))
    restarted = env.get(selfupdate.RESTARTED_ENV)
    env = {k: v for k, v in env.items() if k != selfupdate.RESTARTED_ENV}  # never passed on to a lead
    os.environ.pop(selfupdate.RESTARTED_ENV, None)  # a lead's environment is built from os.environ too
    bw_root = os.path.dirname(os.path.dirname(bw))
    if dry_run:
        return _dry_run(config, state, project, profile, runtimes, rt_tool, tools, plans, run, now, update, verify,
                        bw, env, rep, here, bw_root if self_update else None)
    if not restarted:  # a restarted bw start goes on under the header its first run printed
        rep.header(project.worktree)
    if update and self_update and (config.get("updates") or {}).get("self_update", True):
        if _self_update(rep, bw_root, run, env, restarted, restart):
            return 0  # only when the restart hook returns (a test); a real restart does not come back

    # 2 and 3. updates, and the re-verify pool fed as each tool finishes
    items_by_rt, updated_rts = {}, set()

    # Each part that can take minutes is named here; while any runs, a "still waiting" line repeats.
    # A heartbeat line goes to Checks: Report prints it under Updates while that section is open.
    progress = Progress(lambda text: rep.wait("Checks", text))

    def on_attack(rt, todo):
        progress.begin(("checks", rt), f"{rt} checks")
        rep.wait("Checks", _wait_text(rt, todo, updated_rts))

    def on_lock_wait(rt):
        progress.begin(("lock", rt), f"the {rt} lock")
        rep.wait("Checks", f"waiting for the {rt} lock: an update of {rt} holds it (another bw start, or a "
                           f"bw that updates {rt}, is running). The checks of {rt} start when it ends.")
        return lambda: progress.end(("lock", rt))

    def check(rt, version_ok):
        try:
            items = verifymod.reverify(config, state, rt, now, run=run, allow_run=verify, version_ok=version_ok,
                                       on_attack=on_attack, on_lock_wait=on_lock_wait)
        except Exception as e:  # one runtime's failure is its line, never the start's end
            items = [verifymod.Item(rt, "verify", "failed", f"{type(e).__name__}: {e}")]
        finally:
            progress.end(("checks", rt))
        items_by_rt[rt] = items
        for item in items:
            _item_line(rep, item)

    with progress:
        submitted = set()
        waiting = [rt for rt in runtimes if update and rt in rt_tool]
        last = read_json(os.path.join(state, "start.json"), {})
        previous = last.get("tools") if isinstance(last, dict) else None
        previous = previous if isinstance(previous, dict) else {}
        results = dict(previous)  # --no-update keeps each tool's history (last_update_at) for the next start
        if update:
            rep.begin("Updates")
        try:
            with ThreadPoolExecutor(max_workers=max(1, len(runtimes))) as pool:
                def submit(rt, version_ok):
                    if rt not in submitted:
                        submitted.add(rt)
                        pool.submit(check, rt, version_ok)

                for rt in runtimes:
                    if rt not in waiting:
                        submit(rt, True)
                if update:
                    _no_lock_line(rep, config, runtimes, rt_tool, tools)
                    names = updates.will_update_names(config, previous, now)
                    if names:
                        rep.wait("Updates", f"updating {', '.join(names)}. This can take a minute.")

                    def on_done(res):
                        progress.end(("update", res.tool))
                        if res.result == "updated":
                            updated_rts.add(res.runtime)
                        _tool_line(rep, res)
                        if res.runtime in waiting:
                            submit(res.runtime, res.after != updates.UNREADABLE)

                    results = updates.update_all(config, state, now, run=run, on_done=on_done, previous=previous,
                                                 on_start=lambda name: progress.begin(("update", name), f"{name} update"))
                    for rt in waiting:  # a runtime whose tool never reported still gets its check
                        submit(rt, True)
        finally:
            rep.end("Updates")
        verify_items = [i for rt in runtimes for i in items_by_rt.get(rt, [])]
        _guard_evals(config, runtimes, state, verify, run, rep, profile=project.profile, progress=progress)

    # 4. quota (probes, one Orca read; expired caps cleared) and the lead, on a fresh clock
    started, now = now, clock()
    orca_entry = results.get("orca")
    orca_version = (orca_entry.get("after") if isinstance(orca_entry, dict) else None) or (orca.app_version() if orca.in_use(config) else None)
    try:
        refreshed = probes.refresh(config, state, now, plans=plans, run=run, fetch=fetch, env=env,
                                   orca_version=orca_version)
    except Exception as e:  # never the text: it can carry a URL or a header
        refreshed = {"notes": [f"quota refresh failed ({type(e).__name__}); deciding on what is on disk"]}
    for line in refreshed["notes"]:
        rep.line("Quota", line)
    decision = _quota_and_lead(config, state, project, profile, plans, now, rep)
    write_json_atomic(os.path.join(state, "start.json"),
                      {"ts": started, "project": project.worktree, "tools": results,
                       "verify": [i._asdict() for i in verify_items], "orca_version": orca_version})

    # 5. the registry (derived; after start.json, which holds the versions it shows); its line prints last
    rep.begin("Lead")  # the epilogue (Also) waits for the lead's section
    try:
        registry.write(state, registry.build(config, state, clock(), run=run))
        registry_line = (rep.line, f"registry: {registry.path(state)}")
    except Exception as e:  # a derived file; never the start's end, and never the exception's text
        registry_line = (rep.bad, f"registry not written ({type(e).__name__})")

    if verify:
        try:
            interactive.maybe_offer(config, state, stdin or sys.stdin, out, profile=project.profile)
        except Exception as e:  # never the start's end, and never the exception's text
            rep.bad("Also", f"guard interactive check: failed to run ({type(e).__name__})")
        try:
            offer.maybe_offer(config, state, project.profile, stdin or sys.stdin, out, clock())
        except Exception as e:  # never the start's end, and never the exception's text
            rep.bad("Also", f"guard review offer: failed to run ({type(e).__name__})")

    # 6. the workspace
    code, launch = _workspace(state, project, run, bw, env, rep, orca.canvas(config), decision, here, chdir, execve)
    rep.end("Lead")

    # 7. the seats, in the background (a tab is already open by now; with the lead to start here, the check
    # spawns before the lead does, because the exec is the last step; no seat key is read here)
    _seats(config, state, verify, bw, rep, env)

    # 8. pointers and checks
    rep.line("Also", "cockpit: run `bw cockpit` to see all of this in a browser")
    orca_lines = checks.marked("orca", orca_checks(state, run)) if orca.in_use(config) else []
    for state_, text in orca_lines + checks.marked("cc-switch", ccswitch.check_settings()):
        (rep.bad if state_ == "warn" else rep.line)("Also", text)  # only a warning is a failure
    registry_line[0]("Also", registry_line[1])
    for w in config.get("warnings", []):
        rep.line("Also", f"config warning: {w}")
    return launch() if launch else code  # the exec is last: nothing after it would run


def _guard_evals(config, runtimes, state, verify, run, rep, profile=None, progress=None):
    """The guard eval for each runtime with a guarded lead; it runs only when its key changed. With no
    judge model, or a profile pinned shadow, nothing can promote: no eval runs and no line prints."""
    if not (config.get("guard") or {}).get("model") or (profile and gate.pin(config, profile) == "shadow"):
        return
    for rt in runtimes:
        if not any(kind == "guard" for kind, _ in verifymod.items_for_runtime(config, rt)):
            continue
        try:
            why = evaluate.will_run(config, state, rt, allow_run=verify)
            if why:
                rep.wait("Checks", f"testing the {rt} guard against the judge model. This takes about a minute and "
                                   f"uses a little model quota, because {why}. Please wait.")
        except Exception:  # a notice only: never the start's end
            pass
        if progress is not None:
            progress.begin(("guard", rt), f"the {rt} guard eval")
        try:
            status, line = evaluate.ensure(config, state, rt, allow_run=verify, run=run)
        except Exception as e:  # one runtime's failure is its line, never the start's end, and never the text
            status, line = "fail", f"guard eval {rt}: failed to run ({type(e).__name__})"
        finally:
            if progress is not None:
                progress.end(("guard", rt))
        mark = {"pass": OK, "fail": BAD}.get(status, INFO)
        rep.line("Checks", line, mark)


def _seats(config, state, verify, bw, rep, env):
    if not verify:
        rep.line("Also", "seats: not checked (--no-verify)")
        return
    try:  # the child gets start()'s own config and state, as absolute paths (as the lead does)
        started = seat_verify.spawn_verify(config, state, bw=bw, env=dict(_passed(env)))
    except Exception as e:  # never the start's end, and never the exception's text
        rep.bad("Also", f"seats: could not start the check ({type(e).__name__})")
        return
    log = os.path.join(state, "seats", "verify-log.txt")
    rep.line("Also", f"seats: checking in the background. Log: {log}" if started else "seats: a verify is already running")


def _quota_and_lead(config, state, project, profile, plans, now, rep):
    """Print the plans' quota; return the lead decision (printed by `_announce`, once the lead is certain)."""
    snaps = quota.read_quota(state, config, now)
    rot = config.get("rotation") or {}
    rows = []
    for p in plans:
        pcfg = config["plans"].get(p) or {}
        n = quota.notice(snaps.get(p), pcfg, now, rot.get("probe_every_min", 15))
        rows.append((p, quota.describe(snaps.get(p), pcfg) + (f" — {quota.plain_notice(n)}" if n else "")))
    rep.rows("Quota", rows, "quota")
    caps = quota.read_caps(state, now, rot.get("cap_expiry_hours", 5), include_expired=True)
    pinned = runner.read_pin(state, project.worktree_key)
    d = decide(profile, config["leads"], rot, None, pinned, snaps, caps, now, "start")
    return d._replace(reason=_lead_reason(d, profile))  # words from here on


def _announce(rep, d, here=False):
    """The lead decision's lines (the Lead section opens here). `here`: the lead starts in this terminal."""
    if d.notice:
        rep.line("Lead", d.notice)
    if d.lead:
        rep.ok("Lead", f"{d.lead} ({d.reason})" + (", starting here" if here else ""),
               label="lead")
    else:
        rep.bad("Lead", f"no lead would start — {d.notice or 'no lead available'}")


def _start_here(project, bw, env, rep, chdir, execve):
    """The step that replaces this process with the lead: what the .command file does (`cd`, the
    exported config and state dir, `exec bw lead`). It returns only when `execve` does (a test's)
    or fails."""
    def launch():
        # Everything that reads the current directory comes before the chdir: a relative
        # BALANCEWHEEL_CONFIG or BW_STATE_DIR means the directory the user typed `bw start` in.
        lead_env = {**os.environ, **dict(_passed(env))}
        by_hand = f"cd {shlex.quote(project.worktree)} && {_lead_command(bw, env)}"
        try:
            chdir(project.worktree)
            execve(bw, [bw, "lead"], lead_env)
        except OSError as e:
            rep.bad("Also", f"could not start the lead here ({type(e).__name__}); run it yourself: {by_hand}", raw=True)
            return 1
        return 0
    return launch


def _workspace(state, project, run, bw, env, rep, canvas, decision, here=False, chdir=os.chdir, execve=os.execve):
    """Open the worktree. -> (exit code, the launch step when the lead starts in this terminal, else None)."""
    lock = runner.Lock(state, project.worktree_key)
    try:
        if not lock.acquire():
            rep.line("Lead", f"{runner.holder_line(project.worktree, lock.path)}; not opening another")
            return 0, None
    finally:
        os.close(lock.fd)  # closing releases the flock taken just to test it
    _announce(rep, decision, here)
    if here:
        return 0, _start_here(project, bw, env, rep, chdir, execve)
    code = _open_tab(state, project, run, bw, env, rep, canvas)
    return code, None


def _open_tab(state, project, run, bw, env, rep, canvas):
    cmd = _lead_command(bw, env)
    if canvas == "orca":
        up, why = orca.running(run)
        if up:
            r = run(_orca_argv(project, cmd), ORCA_CREATE_TIMEOUT_S)
            if r.rc == 0 and not r.timed_out:
                rep.ok("Lead", f"opened an Orca pane for {project.worktree}")
                return 0
            rep.bad("Lead", f"orca terminal create failed ({'timed out' if r.timed_out else _detail(r)}); opening a Terminal tab instead")
        else:
            rep.line("Lead", f"orca: {why}; opening a Terminal tab")
    path, content = _command_file(state, project, bw, env)
    _write_command_file(path, content)
    if canvas == "ghostty":
        r = run(_ghostty_argv(path, project.worktree), GHOSTTY_TIMEOUT_S)
        if r.rc == 0 and not r.timed_out:
            rep.ok("Lead", f"opened a Ghostty tab for {project.worktree}")
            return 0
        rep.bad("Lead", f"ghostty: could not open a tab ({_ghostty_detail(r)}); opening a Terminal tab instead")
    r = run(["open", "-a", "Terminal", path], OPEN_TIMEOUT_S)
    if r.rc == 0 and not r.timed_out:
        rep.ok("Lead", f"opened a Terminal tab for {project.worktree}")
        return 0
    rep.bad("Lead", f"could not open a Terminal tab ({'timed out' if r.timed_out else _detail(r)}); "
                    f"run it yourself: cd {shlex.quote(project.worktree)} && {cmd}", raw=True)
    return 1


def _self_update(rep, root, run, env, restarted, restart):
    """Pull bw's own checkout; True when bw start restarts on the new code (once: not after a restart)."""
    if restarted:
        before, _, after = restarted.partition(" ")
        rep.ok("Updates", f"bw: updated {before} → {after}")
        return False
    rep.wait("Updates", f"bw: checking {root} for a new version (git pull --ff-only)")
    with Progress(lambda text: rep.wait("Updates", text)) as progress:
        progress.begin("pull", "the bw update (git pull)")
        res = selfupdate.update(root, run, environ=env)
    if res.state == "updated":
        rep.ok("Updates", f"bw: updated {res.before} → {res.after}; starting again on the new version")
        if restart is not None:
            restart({**env, selfupdate.RESTARTED_ENV: f"{res.before} {res.after}"})
            return True
        rep.line("Updates", "bw: the new version runs from the next command")
    elif res.state == "current":
        rep.ok("Updates", f"bw: {res.after}, current")
    elif res.state == "skipped":
        rep.line("Updates", f"bw: not updated: {res.detail}")
    else:
        rep.bad("Updates", f"bw: update failed ({res.detail})")
    return False


def _dry_run(config, state, project, profile, runtimes, rt_tool, tools, plans, run, now, update, verify, bw, env, rep,
             here=False, bw_root=None):
    rep.header(project.worktree, dry_profile=project.profile)
    if update and bw_root and (config.get("updates") or {}).get("self_update", True):
        rep.line("Updates", f"would update bw: {shlex.join(selfupdate.pull_argv(bw_root))} (only when its checkout "
                            f"has no local changes)")
    for name, tcfg in tools.items():
        rep.line("Updates", _tool_plan_line(name, tcfg or {}, update))
    if update:
        _no_lock_line(rep, config, runtimes, rt_tool, tools)
    for rt in runtimes:
        kinds = [KIND_WORDS.get(k, k) for k, _ in verifymod.items_for_runtime(config, rt)]
        rep.line("Checks", f"would check {rt}: {', '.join(kinds) if kinds else 'nothing to check'}"
                 + (" (--no-verify: a changed key stays pending)" if kinds and not verify else ""))
    for p in plans:
        rep.line("Quota", f"would read quota for plan {p}")
    decision = _quota_and_lead(config, state, project, profile, plans, now, rep)
    _announce(rep, decision)
    cmd = _lead_command(bw, env)
    up, why = orca.running(run) if orca.canvas(config) == "orca" and not here else (False, None)
    if here:
        rep.line("Lead", f"would start the lead here: {cmd}", raw=True)
    elif up:
        rep.line("Lead", f"would run: {shlex.join(_orca_argv(project, cmd))}", raw=True)
    else:
        path, content = _command_file(state, project, bw, env)
        rep.line("Lead", f"orca: {why}; would write {path}:" if why else f"would write {path}:")
        for line in content.splitlines():
            rep.detail("Lead", line)
        if orca.canvas(config) == "ghostty":
            rep.line("Lead", f"would open a Ghostty tab: osascript <new-tab script> "
                             f"{shlex.join([_ghostty_command(path), project.worktree])}", raw=True)
        else:
            rep.line("Lead", f"would run: {shlex.join(['open', '-a', 'Terminal', path])}", raw=True)
    rep.line("Also", "would check seats in the background" if verify else "seats: would not check (--no-verify)")
    return 0
