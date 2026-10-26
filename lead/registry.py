"""registry.py — `state_dir/registry.json`: a derived snapshot of what the config wires together.

Config is canonical; this file is rebuilt by `bw registry` and by `bw start`, so deleting it loses
nothing. Nodes: tools, plans, models, seats, leads, profiles, projects; `edges` connect them
(`project→profile`, `profile→lead`, `profile→seat`, `lead→tool`, `seat→tool`, `tool→plan`,
`plan→model`), each once per pair and only between nodes that exist. Every node has an `id`
(`<kind>:<name>`) and a `health` in {verified, pending, capped, unknown}:
  lead/seat — capped when its plan is; else its verification state (a runtime bw does not contain
              and does not guard needs none: unknown);
  plan      — capped when a cap is recorded; verified when a quota reading is known; else unknown;
  tool      — its leads' verification, aggregated (pending beats verified);
  model     — capped when every plan reaching it is; else unknown;
  profile   — its leads' and seats' states aggregated; project — its profile's.
Nothing here reads a credential: a tool's `auth` is always "unknown". Every external that is
absent (Orca, cc-switch, a tool, seats.json, start.json) shows as unknown or "unavailable";
`build` never raises on one.
"""
import fcntl
import os
import shutil
import time

from . import ccswitch, containment, proc, quota, sandbox
from .locks import RuntimeLock, runtime_tools, tool_runtime
from .paths import read_json, write_json_atomic
from .projects import worktree_key
from .runtimes import ADAPTERS
from .updates import installer_of, read_version

HEALTH = ("verified", "pending", "capped", "unknown")
UNAVAILABLE = "unavailable"


def _guarded(lead):
    return lead.get("autonomy_source", "legacy") != "legacy" and lead.get("autonomy") == "guard"


def _dict(v):
    return v if isinstance(v, dict) else {}


def _lock_state(state, runtime):
    """held | free, by a non-blocking exclusive try on a separate open, closed at once: a running
    lead's shared lock makes it fail, and closing a descriptor we opened ourselves drops only ours."""
    if not runtime:
        return "free"
    try:
        lock = RuntimeLock(state, runtime)
    except OSError:
        return "free"
    try:
        return "free" if lock.try_exclusive() else "held"
    finally:
        lock.release()


def _runtime_verification(state, runtime, leads, run) -> str:
    """verified | pending | none for one runtime: the containment key and each guard key its leads need."""
    ad = ADAPTERS.get(runtime)
    if ad is None:
        return "none"
    which = sandbox.backend()
    states = []
    try:
        if ad.contained_by == "bw":
            key = containment.verification_key(runtime, ad.version_argv, which, run=run)
            states.append(containment.is_verified(state, runtime, key))
        if ad.guard_argv is not None and any(_guarded(l) for l in leads):
            key = containment.guard_key(runtime, ad.version_argv, which, ad.guard_hook_files(), run=run)
            states.append(containment.is_verified(state, runtime, key, store="guard-verify.json"))
    except Exception:  # an unreadable key is not a verification
        return "pending"
    if not states:
        return "none"
    return "verified" if all(states) else "pending"


def _aggregate(states):
    states = [s for s in states if s]
    if "pending" in states:
        return "pending"
    if states and all(s == "verified" for s in states):
        return "verified"
    return "unknown"


def _holder(state, key):
    """The lead holding a worktree's lock now, else None (a stale file nobody holds names no one)."""
    path = os.path.join(state, "locks", f"{key}.lock")
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            lead = _dict(read_json(path, {})).get("lead")
            return lead if isinstance(lead, str) and lead else None
        fcntl.flock(fd, fcntl.LOCK_UN)
        return None
    except OSError:
        return None
    finally:
        os.close(fd)


def _prices(state):
    try:
        out = ccswitch.prices(state)
    except Exception:
        return {}
    return out if isinstance(out, dict) else {}


def build(config, state, now, run=proc.run) -> dict:
    plans_cfg = _dict(config.get("plans"))
    leads_cfg = _dict(config.get("leads"))
    seats_cfg = _dict(config.get("seats"))
    tools_cfg = _dict(config.get("tools"))
    profiles_cfg = _dict(config.get("profiles"))
    projects_cfg = _dict(config.get("projects"))
    rt_tool = runtime_tools({"tools": tools_cfg})
    started = _dict(read_json(os.path.join(state, "start.json"), {}))
    start_tools = _dict(started.get("tools"))
    seats_state = read_json(os.path.join(state, "seats.json"), {})
    seats_state = seats_state if isinstance(seats_state, dict) else {}
    rot = _dict(config.get("rotation"))
    try:
        arbitrated = quota.read_quota(state, config, now)
    except Exception:
        arbitrated = {}
    caps = quota.read_caps(state, now, rot.get("cap_expiry_hours", 5))
    prices = _prices(state)

    # leads: verification per runtime once
    by_runtime = {}
    for l in leads_cfg.values():
        by_runtime.setdefault(l.get("runtime"), []).append(l)
    verification = {rt: _runtime_verification(state, rt, ls, run) for rt, ls in by_runtime.items()}

    def plan_capped(plan):
        return plan in caps

    leads = []
    for name, l in sorted(leads_cfg.items()):
        ad = ADAPTERS.get(l.get("runtime"))
        v = verification.get(l.get("runtime"), "none")
        health = "capped" if plan_capped(l.get("plan")) else v if v in ("verified", "pending") else "unknown"
        leads.append({"id": f"lead:{name}", "name": name, "runtime": l.get("runtime"), "model": l.get("model"),
                      "plan": l.get("plan"), "autonomy": l.get("autonomy"),
                      "contained_by": ad.contained_by if ad else "unknown",
                      "verified": v if v != "none" else "not required", "health": health})

    seats = []
    for name, s in sorted(seats_cfg.items()):
        ad = ADAPTERS.get(s.get("runtime"))
        entry = seats_state.get(name)
        if isinstance(entry, dict) and "verified" in entry:
            v = "verified" if entry["verified"] else "pending"
        else:
            v = "unknown"
        health = "capped" if plan_capped(s.get("plan")) else v
        seats.append({"id": f"seat:{name}", "name": name, "runtime": s.get("runtime"), "model": s.get("model"),
                      "plan": s.get("plan"), "fallback": s.get("fallback"), "verified": v,
                      "enforcement": ad.contained_by if ad else "unknown", "health": health})

    lead_by = {n["name"]: n for n in leads}
    seat_by = {n["name"]: n for n in seats}

    plans = []
    for name, p in sorted(plans_cfg.items()):
        snap = arbitrated.get(name) or {}
        known = bool(snap) and not snap.get("unknown")
        health = "capped" if plan_capped(name) else "verified" if known else "unknown"
        plans.append({"id": f"plan:{name}", "name": name, "billing": p.get("billing"), "quota": p.get("quota"),
                      "source": snap.get("source"), "snapshot": snap or None, "cap": caps.get(name),
                      "health": health})
    plan_health = {p["name"]: p["health"] for p in plans}

    tools = []
    for name, t in sorted(tools_cfg.items()):
        t = _dict(t)
        rt = tool_runtime(name, t)
        entry = _dict(start_tools.get(name))
        version = entry.get("after") if isinstance(entry.get("after"), str) else None
        if not version:
            try:
                version = read_version(name, t, run)
            except Exception:
                version = "unreadable"
        last = entry.get("last_update_at")
        tools.append({"id": f"tool:{name}", "name": name, "runtime": rt,
                      "path": shutil.which(name) or t.get("app"), "version": version, "install": installer_of(t),
                      "last_update_at": last if isinstance(last, (int, float)) and not isinstance(last, bool) else None,
                      "auth": "unknown", "locks": _lock_state(state, rt),
                      "health": _aggregate([lead_by[n]["verified"] for n, l in sorted(leads_cfg.items())
                                            if rt and l.get("runtime") == rt and (n in lead_by)]
                                           ) if rt else "unknown"})
    tool_names = {t["name"] for t in tools}

    def tool_of(runtime):
        n = rt_tool.get(runtime)
        return n if n in tool_names else None

    models = {}
    for kind, cfg in (("lead", leads_cfg), ("seat", seats_cfg)):
        for _, x in sorted(cfg.items()):
            m = x.get("model")
            if not isinstance(m, str) or not m:
                continue
            e = models.setdefault(m, {"tools": set(), "plans": set()})
            if tool_of(x.get("runtime")):
                e["tools"].add(tool_of(x.get("runtime")))
            if x.get("plan") in plans_cfg:
                e["plans"].add(x["plan"])
    model_nodes = []
    for m, e in sorted(models.items()):
        price = prices.get(m)
        ps = sorted(e["plans"])
        model_nodes.append({"id": f"model:{m}", "name": m, "tools": sorted(e["tools"]), "plans": ps,
                            "price": price if price else UNAVAILABLE,
                            "health": "capped" if ps and all(plan_health.get(p) == "capped" for p in ps) else "unknown"})

    pins = _dict(read_json(os.path.join(state, "leads.json"), {}))
    profiles, profile_health = [], {}
    for name, p in sorted(profiles_cfg.items()):
        p = _dict(p)
        order = [n for n in p.get("lead_order", []) if n in lead_by]
        cps = {cp: {"seats": [s for s in _dict(spec).get("seats", []) if s in seat_by],
                    "required": list(_dict(spec).get("required", [])), "min_seats": _dict(spec).get("min_seats")}
               for cp, spec in sorted(_dict(p.get("checkpoints")).items())}
        members = [lead_by[n]["health"] for n in order] + [seat_by[s]["health"] for c in cps.values() for s in c["seats"]]
        if order and all(lead_by[n]["health"] == "capped" for n in order):
            health = "capped"
        else:
            health = _aggregate([h if h in ("verified", "pending") else None for h in members]) if members else "unknown"
        profile_health[name] = health
        profiles.append({"id": f"profile:{name}", "name": name, "lead_order": order, "checkpoints": cps,
                         "health": health})

    projects = []
    for path, prof in sorted(projects_cfg.items()):
        key = worktree_key(path)
        projects.append({"id": f"project:{path}", "name": path, "path": path, "profile": prof,
                         "current_lead": _holder(state, key),
                         "pinned": pins.get(key) if isinstance(pins.get(key), str) else "auto",
                         "health": profile_health.get(prof, "unknown")})

    edges, seen = [], set()

    def edge(a, b, kind):
        if (a, b, kind) not in seen:
            seen.add((a, b, kind))
            edges.append({"from": a, "to": b, "kind": kind})

    for pr in projects:
        if pr["profile"] in profiles_cfg:
            edge(pr["id"], f"profile:{pr['profile']}", "project→profile")
    for pf in profiles:
        for n in pf["lead_order"]:
            edge(pf["id"], f"lead:{n}", "profile→lead")
        for c in pf["checkpoints"].values():
            for s in c["seats"]:
                edge(pf["id"], f"seat:{s}", "profile→seat")
    for kind, cfg in (("lead", leads_cfg), ("seat", seats_cfg)):
        for name, x in sorted(cfg.items()):
            t = tool_of(x.get("runtime"))
            plan = x.get("plan") if x.get("plan") in plans_cfg else None
            if t:
                edge(f"{kind}:{name}", f"tool:{t}", f"{kind}→tool")
                if plan:
                    edge(f"tool:{t}", f"plan:{plan}", "tool→plan")
            if plan and isinstance(x.get("model"), str) and x["model"]:
                edge(f"plan:{plan}", f"model:{x['model']}", "plan→model")

    return {"generated_at": now, "tools": tools, "plans": plans, "models": model_nodes, "seats": seats,
            "leads": leads, "profiles": profiles, "projects": projects, "edges": edges}


def path(state):
    return os.path.join(state, "registry.json")


def write(state, reg):
    write_json_atomic(path(state), reg)
