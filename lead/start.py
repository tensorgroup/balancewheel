"""start.py — `bw start [path]`: update the tools, re-verify, read quota, pick the lead, open it.

One `bw start:` line per event. Steps:
  1. resolve the project; refuse when bw's own real path lies inside the target worktree or its
     common git dir (a lead could edit the next `bw lead`);
  2. update every tool in parallel (`lead.updates`), unless --no-update;
  3. re-verify every lead runtime's keys on a pool, one worker per runtime: a runtime is
     submitted when its tool's update finishes, or at once when no tool updates it (all of
     them with --no-update); the pool is joined before the workspace opens;
  4. quota per plan the profile touches, then the lead decision at the "start" moment;
  6. open the worktree: an Orca pane when Orca runs, else a Terminal tab through a `.command`
     file in the state dir; neither → print the command and exit 1. A worktree whose lock is
     held already has a lead and is not opened again;
  7. the cockpit hint, the Orca and cc-switch checks, config warnings.
`state_dir/start.json` records what happened. `--dry-run` runs only git and `orca status` and
writes nothing.
"""
import os
import shlex
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import ccswitch, checks, orca, proc, quota, runner, updates, verify as verifymod
from .status import orca_checks
from .locks import NON_RUNTIME_TOOLS, runtime_tools, tool_runtime
from .paths import ROOT, read_json, write_json_atomic
from .projects import resolve_project
from .rotation import decide, effective_quota

PASSED_ENV = ("BALANCEWHEEL_CONFIG", "BW_STATE_DIR")
ORCA_CREATE_TIMEOUT_S = 30
OPEN_TIMEOUT_S = 30


def _inside(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _plans_touched(config, profile):
    """Lead plans in the profile's lead_order, then plans of seats in its checkpoints."""
    out = []
    for name in profile.get("lead_order", []):
        p = config["leads"].get(name, {}).get("plan")
        if p and p not in out:
            out.append(p)
    for cp in (profile.get("checkpoints") or {}).values():
        for seat in cp.get("seats", []):
            p = ((config.get("seats") or {}).get(seat) or {}).get("plan")
            if p and p not in out:
                out.append(p)
    return out


def _lead_command(bw, env):
    assignments = [f"{k}={env[k]}" for k in PASSED_ENV if env.get(k)]
    return shlex.join(["env", *assignments, bw, "lead"])


def _command_file(state, project, bw, env):
    path = os.path.join(state, "start", f"{project.worktree_key}.command")
    lines = ["#!/bin/sh"]
    lines += [f"export {k}={shlex.quote(env[k])}" for k in PASSED_ENV if env.get(k)]
    lines += [f"cd {shlex.quote(project.worktree)} || exit 1", f"exec {shlex.quote(bw)} lead"]
    return path, "\n".join(lines) + "\n"


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
          bw_path=None, env=os.environ) -> int:
    now = time.time() if now is None else now
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
    for root in (project.worktree, project.common_dir):
        if _inside(bw, root):
            say(f"refusing: bw ({bw}) lies inside {root}; a lead there could edit the next `bw lead` — "
                "run bw from a checkout outside the target repository")
            return 2
    profile = (config.get("profiles") or {}).get(project.profile) or {}
    runtimes = verifymod.lead_runtimes(config)
    rt_tool = runtime_tools(config)
    tools = config.get("tools") or {}
    plans = _plans_touched(config, profile)
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

    # 4. quota and the lead
    _quota_and_lead(config, state, project, profile, plans, now, say)
    orca_entry = results.get("orca")
    orca_version = (orca_entry.get("after") if isinstance(orca_entry, dict) else None) or orca.app_version()
    write_json_atomic(os.path.join(state, "start.json"),
                      {"ts": now, "project": project.worktree, "tools": results,
                       "verify": [i._asdict() for i in verify_items], "orca_version": orca_version})

    # 6. the workspace
    code = _workspace(state, project, run, bw, env, say)

    # 7. pointers and checks
    say("cockpit: run bw cockpit")
    for line in checks.lines("orca", orca_checks(state, run)) + checks.lines("cc-switch", ccswitch.check_settings()):
        say(line.strip())
    for w in config.get("warnings", []):
        say(f"config warning: {w}")
    return code


def _quota_and_lead(config, state, project, profile, plans, now, say):
    snaps = quota.read_quota(state)
    for p in plans:
        say(f"quota {p}: {quota.summary(effective_quota(snaps.get(p), now))}")
    rot = config.get("rotation") or {}
    caps = quota.read_caps(state, now, rot.get("cap_expiry_hours", 5), include_expired=True)
    pinned = runner.read_pin(state, project.worktree_key)
    d = decide(profile, config["leads"], rot, None, pinned, snaps, caps, now, "start")
    if d.notice:
        say(d.notice)
    say(f"lead: {d.lead} ({d.reason})" if d.lead else f"no lead would start — {d.notice or 'no lead available'}")


def _workspace(state, project, run, bw, env, say):
    lock = runner.Lock(state, project.worktree_key)
    try:
        if not lock.acquire():
            say(f"{runner.holder_line(project.worktree, lock.path)}; not opening another")
            return 0
    finally:
        os.close(lock.fd)  # closing releases the flock taken just to test it
    cmd = _lead_command(bw, env)
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
    _quota_and_lead(config, state, project, profile, plans, now, say)
    cmd = _lead_command(bw, env)
    up, why = orca.running(run)
    if up:
        say(f"would run: {shlex.join(_orca_argv(project, cmd))}")
    else:
        path, content = _command_file(state, project, bw, env)
        say(f"orca: {why}; would write {path}:")
        for line in content.splitlines():
            say(f"  {line}")
        say(f"would run: {shlex.join(['open', '-a', 'Terminal', path])}")
    return 0
