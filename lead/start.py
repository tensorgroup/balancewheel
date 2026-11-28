"""start.py — `bw start [path]`: update the tools, re-verify, read quota, pick the lead, open it.

One `bw start:` line per event. Steps:
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
  6. open the worktree: a Terminal tab through a `.command` file in the state dir; with
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
import shlex
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import ccswitch, checks, orca, probes, proc, quota, registry, rotation, runner, updates, verify as verifymod
from .status import orca_checks
from .locks import NON_RUNTIME_TOOLS, runtime_tools, tool_runtime
from .paths import ROOT, read_json, write_json_atomic
from .projects import refuse_inside, resolve_project
from .rotation import decide
from .guard import evaluate, gate, interactive, offer
from seats import verify as seat_verify

PASSED_ENV = ("BALANCEWHEEL_CONFIG", "BW_STATE_DIR")
ORCA_CREATE_TIMEOUT_S = 30
OPEN_TIMEOUT_S = 30
# A Ghostty tab (Ghostty 1.3+ AppleScript) that runs the .command file in the worktree. The path and
# the worktree reach the run handler as arguments, never as script text, so no quoting can break out.
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
    "activate",
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


def _ghostty_argv(path, worktree):
    return ["osascript", *[a for line in GHOSTTY_SCRIPT for a in ("-e", line)], path, worktree]


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


def _item_line(item):
    lines = [ln for ln in (item.reason or "").splitlines() if ln.strip()]
    return f"verify {item.runtime} {item.kind}: {item.state}" + (f" — {lines[-1].strip()}" if lines else "")


def start(config, state, path, run=proc.run, out=sys.stdout, now=None, dry_run=False, verify=True, update=True,
          bw_path=None, env=os.environ, fetch=probes._fetch, clock=None, stdin=None) -> int:
    """`clock` (default time.time; a fixed `now` freezes it) is read at the start, again before
    quota (step 4, after updates and attacks that can take minutes), and again before the registry."""
    if clock is None:
        clock = time.time if now is None else (lambda: now)
    now = clock()
    print_lock = threading.Lock()

    def say(msg):
        with print_lock:
            print(f"bw start: {msg}", file=out, flush=True)

    # 1. the project, and bw itself outside it
    try:
        project = resolve_project(path or os.getcwd(), config, run)
    except ValueError as e:
        say(str(e))
        return 2
    bw = os.path.realpath(bw_path or os.path.join(ROOT, "bin", "bw"))
    refusal = refuse_inside(project, bw, config.get("path"))
    if refusal:
        say(refusal)
        return 2
    profile = (config.get("profiles") or {}).get(project.profile) or {}
    runtimes = verifymod.lead_runtimes(config)
    rt_tool = runtime_tools(config)
    tools = orca.active_tools(config)
    plans = rotation.plans_touched(config, profile, runner.read_pin(state, project.worktree_key))
    if dry_run:
        return _dry_run(config, state, project, profile, runtimes, rt_tool, tools, plans, run, now, update, verify,
                        bw, env, say)

    # 2 and 3. updates, and the re-verify pool fed as each tool finishes
    items_by_rt = {}

    def check(rt, version_ok):
        try:
            items = verifymod.reverify(config, state, rt, now, run=run, allow_run=verify, version_ok=version_ok)
        except Exception as e:  # one runtime's failure is its line, never the start's end
            items = [verifymod.Item(rt, "verify", "failed", f"{type(e).__name__}: {e}")]
        items_by_rt[rt] = items
        for item in items:
            say(_item_line(item))

    submitted = set()
    waiting = [rt for rt in runtimes if update and rt in rt_tool]
    last = read_json(os.path.join(state, "start.json"), {})
    previous = last.get("tools") if isinstance(last, dict) else None
    previous = previous if isinstance(previous, dict) else {}
    results = dict(previous)  # --no-update keeps each tool's history (last_update_at) for the next start
    with ThreadPoolExecutor(max_workers=max(1, len(runtimes))) as pool:
        def submit(rt, version_ok):
            if rt not in submitted:
                submitted.add(rt)
                pool.submit(check, rt, version_ok)

        for rt in runtimes:
            if rt not in waiting:
                submit(rt, True)
        if update:
            for rt in runtimes:
                if rt not in rt_tool:
                    say(f"no update lock for runtime {rt}")

            def on_done(res):
                say(f"{res.tool}: {res.before} → {res.after} {res.result} ({res.seconds}s)"
                    + (f" — {res.detail}" if res.detail else ""))
                if res.runtime in waiting:
                    submit(res.runtime, res.after != updates.UNREADABLE)

            results = updates.update_all(config, state, now, run=run, on_done=on_done, previous=previous)
            for rt in waiting:  # a runtime whose tool never reported still gets its check
                submit(rt, True)
    verify_items = [i for rt in runtimes for i in items_by_rt.get(rt, [])]
    _guard_evals(config, runtimes, state, verify, run, say, profile=project.profile)

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
        say(line)
    _quota_and_lead(config, state, project, profile, plans, now, say)
    write_json_atomic(os.path.join(state, "start.json"),
                      {"ts": started, "project": project.worktree, "tools": results,
                       "verify": [i._asdict() for i in verify_items], "orca_version": orca_version})

    # 5. the registry (derived; after start.json, which holds the versions it shows)
    try:
        registry.write(state, registry.build(config, state, clock(), run=run))
        say(f"registry: {registry.path(state)}")
    except Exception as e:  # a derived file; never the start's end, and never the exception's text
        say(f"registry not written ({type(e).__name__})")

    if verify:
        try:
            interactive.maybe_offer(config, state, stdin or sys.stdin, out, profile=project.profile)
        except Exception as e:  # never the start's end, and never the exception's text
            say(f"guard interactive check: failed to run ({type(e).__name__})")
        try:
            offer.maybe_offer(config, state, project.profile, stdin or sys.stdin, out, clock())
        except Exception as e:  # never the start's end, and never the exception's text
            say(f"guard review offer: failed to run ({type(e).__name__})")

    # 6. the workspace
    code = _workspace(state, project, run, bw, env, say, orca.canvas(config))

    # 7. the seats, in the background (after the lead opens; no seat key is read here)
    _seats(config, state, verify, bw, say, env)

    # 8. pointers and checks
    say("cockpit: run bw cockpit")
    orca_lines = checks.lines("orca", orca_checks(state, run)) if orca.in_use(config) else []
    for line in orca_lines + checks.lines("cc-switch", ccswitch.check_settings()):
        say(line.strip())
    for w in config.get("warnings", []):
        say(f"config warning: {w}")
    return code


def _guard_evals(config, runtimes, state, verify, run, say, profile=None):
    """The guard eval for each runtime with a guarded lead; it runs only when its key changed. With no
    judge model, or a profile pinned shadow, nothing can promote: no eval runs and no line prints."""
    if not (config.get("guard") or {}).get("model") or (profile and gate.pin(config, profile) == "shadow"):
        return
    for rt in runtimes:
        if not any(kind == "guard" for kind, _ in verifymod.items_for_runtime(config, rt)):
            continue
        try:
            _, line = evaluate.ensure(config, state, rt, allow_run=verify, run=run)
        except Exception as e:  # one runtime's failure is its line, never the start's end, and never the text
            line = f"guard eval {rt}: failed to run ({type(e).__name__})"
        say(line)


def _seats(config, state, verify, bw, say, env):
    if not verify:
        say("seats: not checked (--no-verify)")
        return
    try:  # the child gets start()'s own config and state, as absolute paths (as the lead does)
        started = seat_verify.spawn_verify(config, state, bw=bw, env=dict(_passed(env)))
    except Exception as e:  # never the start's end, and never the exception's text
        say(f"seats: could not start the check ({type(e).__name__})")
        return
    log = os.path.join(state, "seats", "verify-log.txt")
    say(f"checking seats in the background (log: {log})" if started else "seats: a verify is already running")


def _quota_and_lead(config, state, project, profile, plans, now, say):
    snaps = quota.read_quota(state, config, now)
    rot = config.get("rotation") or {}
    for p in plans:
        n = quota.notice(snaps.get(p), config["plans"].get(p) or {}, now, rot.get("probe_every_min", 15))
        say(f"quota {p}: {quota.summary(snaps.get(p))}" + (f" — {n}" if n else ""))
    caps = quota.read_caps(state, now, rot.get("cap_expiry_hours", 5), include_expired=True)
    pinned = runner.read_pin(state, project.worktree_key)
    d = decide(profile, config["leads"], rot, None, pinned, snaps, caps, now, "start")
    if d.notice:
        say(d.notice)
    say(f"lead: {d.lead} ({d.reason})" if d.lead else f"no lead would start — {d.notice or 'no lead available'}")


def _workspace(state, project, run, bw, env, say, canvas):
    lock = runner.Lock(state, project.worktree_key)
    try:
        if not lock.acquire():
            say(f"{runner.holder_line(project.worktree, lock.path)}; not opening another")
            return 0
    finally:
        os.close(lock.fd)  # closing releases the flock taken just to test it
    cmd = _lead_command(bw, env)
    if canvas == "orca":
        up, why = orca.running(run)
        if up:
            r = run(_orca_argv(project, cmd), ORCA_CREATE_TIMEOUT_S)
            if r.rc == 0 and not r.timed_out:
                say(f"opened an Orca pane for {project.worktree}")
                return 0
            say(f"orca terminal create failed ({'timed out' if r.timed_out else _detail(r)}); opening a Terminal tab instead")
        else:
            say(f"orca: {why}; opening a Terminal tab")
    path, content = _command_file(state, project, bw, env)
    _write_command_file(path, content)
    if canvas == "ghostty":
        r = run(_ghostty_argv(path, project.worktree), OPEN_TIMEOUT_S)
        if r.rc == 0 and not r.timed_out:
            say(f"opened a Ghostty tab for {project.worktree}")
            return 0
        say(f"ghostty: could not open a tab ({'timed out' if r.timed_out else _detail(r)}); opening a Terminal tab instead")
    r = run(["open", "-a", "Terminal", path], OPEN_TIMEOUT_S)
    if r.rc == 0 and not r.timed_out:
        say(f"opened a Terminal tab for {project.worktree}")
        return 0
    say(f"could not open a Terminal tab ({'timed out' if r.timed_out else _detail(r)}); "
        f"run it yourself: cd {shlex.quote(project.worktree)} && {cmd}")
    return 1


def _dry_run(config, state, project, profile, runtimes, rt_tool, tools, plans, run, now, update, verify, bw, env, say):
    say(f"dry run for {project.worktree} (profile {project.profile}); nothing is run or written")
    for name, tcfg in tools.items():
        say(_tool_plan_line(name, tcfg or {}, update))
    if update:
        for rt in runtimes:
            if rt not in rt_tool:
                say(f"no update lock for runtime {rt}")
    for rt in runtimes:
        kinds = [k for k, _ in verifymod.items_for_runtime(config, rt)]
        say(f"would check keys for {rt}: {', '.join(kinds) if kinds else 'nothing to check'}"
            + (" (--no-verify: a changed key stays pending)" if kinds and not verify else ""))
    for p in plans:
        say(f"would read quota for plan {p}")
    say("would check seats in the background" if verify else "seats: would not check (--no-verify)")
    _quota_and_lead(config, state, project, profile, plans, now, say)
    cmd = _lead_command(bw, env)
    up, why = orca.running(run) if orca.canvas(config) == "orca" else (False, None)
    if up:
        say(f"would run: {shlex.join(_orca_argv(project, cmd))}")
    else:
        path, content = _command_file(state, project, bw, env)
        say(f"orca: {why}; would write {path}:" if why else f"would write {path}:")
        for line in content.splitlines():
            say(f"  {line}")
        if orca.canvas(config) == "ghostty":
            say(f"would open a Ghostty tab: osascript <new-tab script> {shlex.join([path, project.worktree])}")
        else:
            say(f"would run: {shlex.join(['open', '-a', 'Terminal', path])}")
    return 0
