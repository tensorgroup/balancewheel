"""`bw status`: gather the facts once, render them as lines.

`facts` is the data (the cockpit reads the same dict later); `render` is the text. Every
external surface it touches (Orca, cc-switch) reads as `unknown` with a reason when absent.
"""
import os
import time

from lead import ccswitch, orca, proc, quota, runner
from lead.checks import Check
from lead.guard import review as guard_review
from lead.paths import read_json
from lead.rotation import effective_quota
from lead.runtimes import ADAPTERS


def _plans(config, state, now):
    caps = quota.read_caps(state, now, config["rotation"]["cap_expiry_hours"])
    snaps = quota.read_quota(state)
    out = []
    for name, plan in sorted(config["plans"].items()):
        q = effective_quota(snaps.get(name), now)
        out.append({"name": name, "billing": plan["billing"],
                    "quota": quota.summary(q) if q else "quota unknown",
                    "capped_until": caps[name]["resets_at"] if name in caps else None})
    return out


def _worktrees(state):
    pins = read_json(os.path.join(state, "leads.json"), {}) or {}
    locks = os.path.join(state, "locks")
    out = []
    for f in sorted(os.listdir(locks)) if os.path.isdir(locks) else []:
        meta = read_json(os.path.join(locks, f), {}) or {}
        key = f[:-5]
        if meta.get("lead") or key in pins:
            out.append({"worktree": meta.get("worktree", key), "lead": meta.get("lead", "idle"),
                        "since": meta.get("started"), "pinned": pins.get(key)})
    return out


def _leads(config):
    return [{"name": name, "runtime": ld["runtime"], "model": ld["model"], "plan": ld["plan"],
             "contained_by": ADAPTERS[ld["runtime"]].contained_by}
            for name, ld in sorted(config["leads"].items())]


ORCA_APP = "orca app"


def orca_checks(state, run=proc.run):
    d = orca.support_dir()
    if not os.path.isdir(d):
        return [Check("orca", "unknown", "not installed (no Orca support dir)")]
    up, why = orca.running(run)
    presence = Check(ORCA_APP, "unknown" if up is None else "ok",
                     f"{why} (settings checked)" if up is False else why)
    return [presence] + orca.check_settings(*orca.read_settings(state, d))


def facts(config, state, now, run=proc.run):
    return {"plans": _plans(config, state, now),
            "worktrees": _worktrees(state),
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
    lines.append("worktrees:")
    for w in f["worktrees"]:
        since = time.ctime(w["since"]) if w["since"] else "-"
        lines.append(f"  {w['worktree']}: {w['lead']} since {since}"
                     + (f" · pinned {w['pinned']}" if w["pinned"] else ""))
    lines.append("leads:")
    for ld in f["leads"]:
        lines.append(f"  {ld['name']}: {ld['runtime']} {ld['model']} on {ld['plan']} · contained by {ld['contained_by']}")
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
