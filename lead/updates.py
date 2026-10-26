"""Tool version reads and updates: every installer in parallel, tools sharing one in turn.

A tool's update runs only under its runtime's lock taken exclusively without blocking
(`lead/locks.py`): a lead session or a verification check holds it shared, so an update lands
neither under a running lead nor between a check and the launch it allowed. A held lock skips
the tool. A tool with an update command and no runtime is not updated at all; Orca and
cc-switch update with no lock. Versions are read before and after (10 s each); a version that
cannot be read after the attempt is "unreadable", which leaves that runtime's re-verify pending; a
successful update is "updated" only when both versions were read and differ.
"""
import os
import plistlib
import queue
import time
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor

from . import proc
from .locks import NON_RUNTIME_TOOLS, RuntimeLock, tool_runtime
from .runtimes import ADAPTERS

ToolResult = namedtuple("ToolResult", "tool runtime before after result detail seconds")
RESULTS = ("updated", "current", "failed", "timed-out", "skipped-locked", "skipped-interval",
           "skipped-no-lock", "no-update-command", "self-updating", "unreadable")
UNREADABLE = "unreadable"
VERSION_TIMEOUT_S = 10
DEFAULT_UPDATE_TIMEOUT_S = 600


def installer_of(tool_cfg) -> str:
    update = (tool_cfg or {}).get("update")
    return os.path.basename(update[0]) if update else ""


def _first_line(text):
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def _plist_version(app):
    try:
        with open(os.path.join(app, "Contents", "Info.plist"), "rb") as f:
            v = plistlib.load(f).get("CFBundleShortVersionString")
    except (OSError, plistlib.InvalidFileException, ValueError, AttributeError):
        return ""
    return v.strip() if isinstance(v, str) else ""


def read_version(name, tool_cfg, run=proc.run) -> str:
    """`tools.<t>.version`, then the runtime adapter's version argv, then the app bundle's
    Info.plist; the first source that yields a non-empty line wins, else "unreadable"."""
    tool_cfg = tool_cfg or {}
    argvs = []
    if tool_cfg.get("version"):
        argvs.append(tool_cfg["version"])
    rt = tool_runtime(name, tool_cfg)
    if rt in ADAPTERS:
        argvs.append(ADAPTERS[rt].version_argv)
    for argv in argvs:
        r = run(list(argv), VERSION_TIMEOUT_S)
        if r.rc == 0 and not r.timed_out:
            line = _first_line(r.out)
            if line:
                return line
    if tool_cfg.get("app"):
        v = _plist_version(tool_cfg["app"])
        if v:
            return v
    return UNREADABLE


def _failure_detail(r):
    first = _first_line(r.err) or _first_line(r.out)
    return f"exit {r.rc}" + (f": {first}" if first else "")


def _update_one(name, tool_cfg, state, now, interval_s, previous, run):
    """-> (ToolResult, last_update_at)."""
    t0 = time.monotonic()
    prev_at = (previous.get(name) or {}).get("last_update_at")
    rt = tool_runtime(name, tool_cfg)

    def done(before, after, result, detail, at=prev_at):
        return ToolResult(name, rt, before, after, result, detail, round(time.monotonic() - t0, 1)), at

    if tool_cfg.get("self_updating"):
        v = read_version(name, tool_cfg, run)
        return done(v, v, "self-updating", "updates itself; version only")
    if not tool_cfg.get("update"):
        v = read_version(name, tool_cfg, run)
        return done(v, v, "no-update-command", "no update command configured")
    if interval_s and isinstance(prev_at, (int, float)) and now - prev_at < interval_s:
        v = read_version(name, tool_cfg, run)
        hours = (now - prev_at) / 3600
        return done(v, v, "skipped-interval", f"updated {hours:.1f} h ago (min_interval_hours {interval_s // 3600})")
    if rt is None and name not in NON_RUNTIME_TOOLS:
        v = read_version(name, tool_cfg, run)
        return done(v, v, "skipped-no-lock", f"no update lock — set tools.{name}.runtime")
    lock = RuntimeLock(state, rt) if rt else None
    try:
        if lock is not None and not lock.try_exclusive():
            v = read_version(name, tool_cfg, run)
            return done(v, v, "skipped-locked", f"{rt} is in use (a lead or a verification holds its lock)")
        before = read_version(name, tool_cfg, run)
        r = run(list(tool_cfg["update"]), tool_cfg.get("timeout_s", DEFAULT_UPDATE_TIMEOUT_S))
        after = read_version(name, tool_cfg, run)
        if r.timed_out:
            return done(before, after, "timed-out",
                        f"no result after {tool_cfg.get('timeout_s', DEFAULT_UPDATE_TIMEOUT_S)} s", now)
        if r.rc != 0:
            return done(before, after, "failed", _failure_detail(r), now)
        if after == UNREADABLE:
            return done(before, after, UNREADABLE, "version unreadable after update", now)
        if before == UNREADABLE:
            return done(before, after, UNREADABLE, "version unreadable before update", now)
        return done(before, after, "updated" if after != before else "current", "", now)
    finally:
        if lock is not None:
            lock.release()


def update_all(config, state, now, run=proc.run, on_done=None, previous=None) -> dict:
    """Update every configured tool; -> {tool: ToolResult as dict + "last_update_at"}.

    One worker per installer (tools without an update command each get their own); `on_done`
    is called in the calling thread with each ToolResult as that tool finishes."""
    tools = config.get("tools") or {}
    previous = previous or {}
    interval_s = int((config.get("updates") or {}).get("min_interval_hours", 0)) * 3600
    groups = {}
    for name, tcfg in tools.items():
        inst = installer_of(tcfg)
        groups.setdefault(("installer", inst) if inst else ("tool", name), []).append(name)
    done_q = queue.Queue()

    def work(names):
        for name in names:
            try:
                done_q.put(_update_one(name, tools[name] or {}, state, now, interval_s, previous, run))
            except Exception as e:  # never raise out of a worker: the tool is reported, the rest go on
                rt = tool_runtime(name, tools[name])
                done_q.put((ToolResult(name, rt, UNREADABLE, UNREADABLE, "failed",
                                       f"{type(e).__name__}: {e}", 0.0), now))

    out = {}
    if not groups:
        return out
    with ThreadPoolExecutor(max_workers=len(groups)) as pool:
        for names in groups.values():
            pool.submit(work, names)
        for _ in range(len(tools)):
            res, at = done_q.get()
            entry = res._asdict()
            entry["last_update_at"] = at
            out[res.tool] = entry
            if on_done is not None:
                try:
                    on_done(res)
                except Exception as e:  # a display failure is the tool's note, never the run's end
                    note = f"on_done failed: {type(e).__name__}: {e}"
                    entry["detail"] = f"{entry['detail']}; {note}" if entry["detail"] else note
    return out
