"""probes.py — quota probes: OpenRouter's key endpoint, agy (awaiting its shape), and the refresh
that runs them with one Orca read.

Network probes run at `bw start`, when `bw lead` starts and at its session boundary, and on
`bw probe` — never in the poll loop. `refresh` joins every thread it starts before returning.

The OpenRouter key comes from the environment variable the plan names, is sent only to
`KEY_URL` with no redirect followed (the header is unredirected as well), and appears in no
file, output, reason, or exception text: every failure is named by its type, never described.
"""
import calendar
import json
import math
import os
import socket
import threading
import time
import urllib.error
import urllib.request

from . import orca, proc, quota
from .paths import read_json, write_json_atomic

KEY_URL = "https://openrouter.ai/api/v1/key"
KEY_TIMEOUT_S = 10
MAX_BODY = 1 << 20
AGY_AWAITING = "agy /usage: awaiting Task G (shape not yet observed)"


def probe_agy(plan_cfg, state, now, run=proc.run, plan=None):
    """Unknown until Task G has seen `agy /usage`'s shape. Task G replaces this body: argv
    ["agy", "--print", "/usage", "--output-format", "json"], cwd state_dir/probes/agy-cwd (agy
    writes into its working directory), an environment without ORCA_*, 20 s."""
    return {"unknown": True, "reason": AGY_AWAITING, "source": "probe"}


class _RedirectRefused(Exception):
    def __init__(self, code):
        super().__init__()
        self.code = code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            fp.close()  # the refused response is never read; its socket goes now, not at garbage collection
        finally:
            raise _RedirectRefused(code)


def _reason(e):
    """A failure's name, never its text (which can carry a URL, a header, or the key)."""
    inner = e.reason if isinstance(e, urllib.error.URLError) else e
    if isinstance(inner, (socket.timeout, TimeoutError)):
        return "timeout"
    return type(inner).__name__ if isinstance(inner, BaseException) else type(e).__name__


def _fetch(url, key, timeout, _test_any_url=False):
    """(status, body, reason) from a GET of `KEY_URL` with the key as a bearer token. Any other
    URL is refused without a request (`_test_any_url` lifts only that check, for the tests'
    local servers); a redirect is refused, not followed; every exception is (0, b"", its name)."""
    if url != KEY_URL and not _test_any_url:
        return 0, b"", "url refused"
    opener = urllib.request.build_opener(_NoRedirect())
    req = urllib.request.Request(url, method="GET")
    req.add_unredirected_header("Authorization", "Bearer " + key)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read(MAX_BODY), ""
    except _RedirectRefused as e:
        return e.code, b"", "redirect refused"
    except urllib.error.HTTPError as e:
        e.close()
        return e.code, b"", ""
    except Exception as e:
        return 0, b"", _reason(e)


def _finite(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _month_start(now, months_ahead=0):
    t = time.gmtime(now)
    y, m = divmod(t.tm_mon - 1 + months_ahead, 12)
    return float(calendar.timegm((t.tm_year + y, m + 1, 1, 0, 0, 0)))


def parse_openrouter_key(obj, budget_usd_month, baseline, now):
    """A probe snapshot from `GET /api/v1/key`'s document. The `budget` window (only with a
    positive budget) is this UTC month's spend against it: `usage_monthly` when the key reports
    it, else `usage` less a baseline taken at or before 00:00 UTC on the 1st — a later baseline
    misses earlier spend, so the window is left out rather than counting that spend as budget.
    The key's own credit limit is shown under `limits`, never a window: it has no reset."""
    data = obj.get("data") if isinstance(obj, dict) else None
    if not isinstance(data, dict):
        return {"unknown": True, "reason": "openrouter: no data object", "source": "probe"}
    usage = data.get("usage")
    if not _finite(usage):
        return {"unknown": True, "reason": "openrouter: data.usage is not a number", "source": "probe"}
    monthly = data.get("usage_monthly")
    note = None
    if _finite(monthly):
        spend = float(monthly)
    elif (isinstance(baseline, dict) and _finite(baseline.get("usage")) and _finite(baseline.get("at"))
          and baseline["at"] <= _month_start(now)):
        spend = max(float(usage) - baseline["usage"], 0.0)
    else:
        spend = None
        at = baseline.get("at") if isinstance(baseline, dict) else None
        note = f"monthly spend unknown before {time.ctime(at)}" if _finite(at) else "monthly spend unknown"
    windows = []
    budget = budget_usd_month if _finite(budget_usd_month) else None
    if budget and budget > 0 and spend is not None:
        windows.append({"name": "budget", "used_pct": min(spend / budget * 100, 100.0),
                        "resets_at": _month_start(now, 1)})
    snap = {"source": "probe", "windows": windows, "observed_at": now, "spend_usd": spend,
            "budget_usd_month": budget_usd_month,
            "is_free_tier": data.get("is_free_tier") if isinstance(data.get("is_free_tier"), bool) else None}
    limit, remaining = data.get("limit"), data.get("limit_remaining")
    if _finite(limit) and limit > 0:
        snap["limits"] = {"key_limit": {"limit": float(limit),
                                        "limit_remaining": float(remaining) if _finite(remaining) else None}}
    if note and budget and budget > 0:
        snap["note"] = note
    return snap


def _baseline_path(state, plan, now):
    return os.path.join(state, "probes", f"openrouter-{plan}-{time.strftime('%Y-%m', time.gmtime(now))}.json")


def _last_path(state, plan):
    return os.path.join(state, "probes", f"openrouter-{plan}-last.json")


def probe_openrouter(plan_cfg, state, now, fetch=_fetch, env=os.environ, plan=None):
    """The plan's spend from OpenRouter's key endpoint, or unknown with a reason. Each month gets a
    baseline in state_dir/probes/ (used only when the response has no `usage_monthly`): the last
    reading taken before the month began, carried forward — which counts the old month's last
    hours as this month's spend (overstated, never under) — else the month's first reading, which
    leaves the month's spend unknown. Every reading is kept as the plan's last one."""
    name = plan_cfg.get("key_env") or "OPENROUTER_API_KEY"
    key = env.get(name)
    if not key:
        return {"unknown": True, "reason": f"key env {name} unset", "source": "probe"}
    try:
        status, body, reason = fetch(KEY_URL, key, KEY_TIMEOUT_S)
    except Exception as e:
        status, body, reason = 0, b"", _reason(e)
    if status != 200 or reason:
        return {"unknown": True, "reason": reason or f"HTTP {status}", "source": "probe"}
    try:
        doc = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return {"unknown": True, "reason": "openrouter: response is not JSON", "source": "probe"}
    baseline = None
    if plan:
        path = _baseline_path(state, plan, now)
        baseline = read_json(path)
        data = doc.get("data") if isinstance(doc, dict) else None
        usage = data.get("usage") if isinstance(data, dict) else None
        if not isinstance(baseline, dict) and _finite(usage):
            last = read_json(_last_path(state, plan))
            if (isinstance(last, dict) and _finite(last.get("usage")) and _finite(last.get("at"))
                    and last["at"] <= _month_start(now) and last["usage"] <= usage):
                baseline = {"usage": float(last["usage"]), "at": last["at"]}
            else:
                baseline = {"usage": float(usage), "at": now}
            write_json_atomic(path, baseline)
        if _finite(usage):
            write_json_atomic(_last_path(state, plan), {"usage": float(usage), "at": now})
    return parse_openrouter_key(doc, plan_cfg.get("budget_usd_month"), baseline, now)


def _probe(name, cfg, state, now, run, fetch, env):
    kind = cfg.get("probe")
    if kind == "agy":
        return probe_agy(cfg, state, now, run=run, plan=name)
    if kind == "openrouter":
        return probe_openrouter(cfg, state, now, fetch=fetch, env=env, plan=name)
    return {"unknown": True, "reason": "no probe for this plan", "source": "probe"}


def refresh(config, state, now, plans=None, run=proc.run, fetch=_fetch, env=os.environ, orca_version=None):
    """Every source a plan has, read once: one `orca account list` shared by every Orca-mapped
    plan, and each `quota: "probe"` plan by its probe, in parallel; each snapshot written, then
    expired caps cleared. Threads are joined before return. Returns
    {"snapshots": {plan: probe snapshot}, "notes": [Orca version-change line]}."""
    chosen = {n: p for n, p in config["plans"].items() if plans is None or n in plans}
    snapshots, notes, lock = {}, [], threading.Lock()

    def read_orca():
        sub = dict(config, plans=chosen)
        try:
            note = orca.refresh(state, sub, now, run=run, version=orca_version)
        except Exception as e:
            note = f"orca read failed ({type(e).__name__})"
        if note:
            notes_add(note)

    def notes_add(line):
        with lock:
            notes.append(line)

    def read_probe(name, cfg):
        try:
            snap = _probe(name, cfg, state, now, run, fetch, env)
        except Exception as e:
            snap = {"unknown": True, "reason": f"probe failed ({type(e).__name__})", "source": "probe"}
        try:
            snap = quota.write_snapshot(state, name, "probe", snap, now)
        except OSError as e:
            notes_add(f"{name}: probe snapshot not written ({type(e).__name__})")
        with lock:
            snapshots[name] = snap

    threads = [threading.Thread(target=read_orca, name="bw-probe-orca")]
    threads += [threading.Thread(target=read_probe, args=(n, p), name=f"bw-probe-{n}")
                for n, p in sorted(chosen.items()) if p.get("quota") == "probe"]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rot = config.get("rotation") or {}
    quota.clear_expired_caps(state, quota.read_quota(state, config, now), now, rot.get("cap_expiry_hours", 5))
    return {"snapshots": snapshots, "notes": notes}
