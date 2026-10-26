"""`bw status`: gather the facts once, render them as lines.

`facts` is the data (the cockpit reads the same dict later); `render` is the text. Every
external surface it touches (Orca, cc-switch) reads as `unknown` with a reason when absent.
"""
import math
import os
import time

from lead import ccswitch, inbox, orca, proc, quota, runner
from lead.checks import Check
from lead.guard import review as guard_review
from lead.guard.review import safe
from lead.paths import read_json
from lead.runtimes import ADAPTERS


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _spend(snap, budget, now, every):
    """A metered plan's month spend from its probe snapshot while fresh, else unknown with the reason."""
    if not isinstance(snap, dict):
        return {"unknown": True, "reason": "no probe reading", "budget_usd_month": budget}
    if snap.get("unknown"):
        return {"unknown": True, "reason": str(snap.get("reason") or "unknown"), "budget_usd_month": budget}
    t, spend = snap.get("observed_at"), snap.get("spend_usd")
    if not _num(t) or t > now + quota.FUTURE_SLACK_S or now - t > 2 * every * 60:
        return {"unknown": True, "reason": "probe reading stale or undated", "budget_usd_month": budget}
    if not _num(spend):
        return {"unknown": True, "reason": str(snap.get("note") or "monthly spend unknown"), "budget_usd_month": budget}
    return {"unknown": False, "spend_usd": spend, "budget_usd_month": budget, "observed_at": t}


def plan_facts(config, state, now):
    """Per plan: the arbitrated reading (windows, pools, source, age, reason, limits, cross-check),
    its one-line summary, notice and cap; a metered plan with a budget adds its month `spend`."""
    rot = config["rotation"]
    every = rot.get("probe_every_min", 15)
    caps = quota.read_caps(state, now, rot["cap_expiry_hours"])
    sources = quota.read_sources(state)
    arbitrated = {name: quota.arbitrate(sources.get(name, {}), pcfg, now, every)
                  for name, pcfg in config["plans"].items()}
    out = []
    for name, plan in sorted(config["plans"].items()):
        a = arbitrated[name]
        unknown = bool(a.get("unknown"))
        row = {"name": name, "billing": plan["billing"], "quota": quota.summary(a), "quota_mode": plan.get("quota", "none"),
               "windows": [] if unknown else a.get("windows", []), "unknown": unknown,
               "reason": a.get("reason") if unknown else None, "last_known_at": a.get("last_known_at"),
               "source": a.get("source"), "age_s": a.get("age_s"), "cross_check": a.get("cross_check"),
               "pools": a.get("pools", []), "limits": a.get("limits"), "notice": quota.notice(a, plan, now, every),
               "capped_until": caps[name]["resets_at"] if name in caps else None}
        if plan["billing"] == "metered" and plan.get("budget_usd_month") is not None:
            row["spend"] = _spend((sources.get(name) or {}).get("probe"), plan["budget_usd_month"], now, every)
        out.append(row)
    return out


def _worktrees(state):
    pins = read_json(os.path.join(state, "leads.json"), {}) or {}
    locks = os.path.join(state, "locks")
    out = []
    for f in sorted(os.listdir(locks)) if os.path.isdir(locks) else []:
        # bw writes lock metadata, but a malformed file (an older bw, a crash) must not break
        # status or the cockpit: validated and escaped like runner.holder_line
        meta = read_json(os.path.join(locks, f), {})
        meta = meta if isinstance(meta, dict) else {}
        key = f[:-5]
        if meta.get("lead") or key in pins:
            wt = meta.get("worktree")
            out.append({"key": key, "worktree": safe(wt if wt is not None else key),
                        "lead": safe(meta["lead"]) if meta.get("lead") else "idle",
                        "since": runner.lock_started(meta), "pinned": pins.get(key)})
    return out


def _leads(config):
    return [{"name": name, "runtime": ld["runtime"], "model": ld["model"], "plan": ld["plan"],
             "contained_by": ADAPTERS[ld["runtime"]].contained_by}
            for name, ld in sorted(config["leads"].items())]


def _inbox(state):
    """Open asks and notices per worktree (keyed by worktree key; an ask names its worktree);
    None when the inbox cannot be read."""
    out = {}
    try:
        items = inbox.open_items(state)
    except OSError:
        return None
    for i in items:
        w = out.setdefault(i.get("project"), {"worktree": None, "asks": [], "notices": []})
        if i["type"] == "ask":
            w["worktree"] = w["worktree"] or i.get("worktree")
            w["asks"].append(i)
        else:
            w["notices"].append(i)
    return out


ORCA_APP = "orca app"


def orca_checks(state, run=proc.run):
    d = orca.support_dir()
    if not os.path.isdir(d):
        return [Check("orca", "unknown", "not installed (no Orca support dir)")]
    up, why = orca.running(run)
    presence = Check(ORCA_APP, "unknown" if up is None else "ok",
                     f"{why} (settings checked)" if up is False else why)
    return [presence] + orca.check_settings(*orca.read_settings(state, d))


def worktree_facts(state):
    """The local, shell-free half of `facts`: worktrees (current and pinned lead) and the open inbox."""
    return {"worktrees": _worktrees(state), "inbox": _inbox(state)}


def facts(config, state, now, run=proc.run):
    return {"plans": plan_facts(config, state, now),
            **worktree_facts(state),
            "leads": _leads(config),
            "guard": guard_review.status_lines(state),
            "checks": {"orca": orca_checks(state, run), "cc-switch": ccswitch.check_settings()},
            "last_start": read_json(os.path.join(state, "start.json"), None)}


def _shown_ok(c):
    """An ok check still worth a line: Orca installed but not running."""
    return c.name == ORCA_APP and c.state == "ok" and c.detail.startswith("not running")


def _check_lines(title, checks):
    shown = [c for c in checks if c.state != "ok" or _shown_ok(c)]
    if not shown:
        return [f"{title}: ok"]
    return [f"{title}: {c.state} — {c.detail}" for c in shown]


def render(f):
    lines = ["plans:"]
    for p in f["plans"]:
        cs = f" · capped until {time.ctime(p['capped_until'])}" if p["capped_until"] is not None else ""
        lines.append(f"  {p['name']} ({p['billing']}): {p['quota']}{cs}")
        if p.get("notice"):
            lines.append(f"    {p['notice']}")
    lines.append("worktrees:")
    for w in f["worktrees"]:
        since = time.ctime(w["since"]) if w["since"] else "-"
        lines.append(f"  {w['worktree']}: {w['lead']} since {since}"
                     + (f" · pinned {w['pinned']}" if w["pinned"] else ""))
    lines.append("leads:")
    for ld in f["leads"]:
        lines.append(f"  {ld['name']}: {ld['runtime']} {ld['model']} on {ld['plan']} · contained by {ld['contained_by']}")
    lines.append("inbox: unreadable" if f.get("inbox") is None else "inbox:" if f["inbox"] else "inbox: empty")
    for key, w in sorted((f.get("inbox") or {}).items(), key=lambda kv: str(kv[0])):
        lines.append(f"  {safe(w['worktree'] or key)}: {len(w['asks'])} ask(s), {len(w['notices'])} notice(s)")
        for i in w["asks"]:
            lines.append(f"    [{safe(i.get('id'))}] ask from {safe(i.get('from'))}: {inbox.first_line(i.get('text', ''))}")
        for i in w["notices"]:
            lines.append(f"    [{safe(i.get('id'))}] {safe(i.get('kind'))}: {inbox.first_line(i.get('text', ''))}")
    if f["guard"]:
        lines.append("guard:")
        lines.extend(f["guard"])
    for title in ("orca", "cc-switch"):
        lines.extend(_check_lines(title, f["checks"][title]))
    ls = f.get("last_start")
    if isinstance(ls, dict) and isinstance(ls.get("ts"), (int, float)):
        lines.append(f"last start: {time.ctime(ls['ts'])}")
    lines.append(runner.V0_SCOPE)
    return lines
