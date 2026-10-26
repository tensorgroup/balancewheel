"""views.py — the cockpit's JSON views: Models, Usage, History, Now (and Graph, next step).

`build` returns zero-argument callables the server maps to `/api/<name>`. Each call reads fresh:
one metrics store load, at most one cc-switch copy. Numbers nobody reported are None ("unavailable"
on the page), never 0. Nothing here writes a file.
"""
import math
import time
from datetime import datetime

from lead import paths  # noqa: F401  (puts metrics/ on sys.path)
from lead import ccswitch, inbox, proc, quota, status
import store  # noqa: E402
import summary  # noqa: E402

USAGE_DAYS = 30
SMALL_N = 10  # below this many verdicts a row shows counts only, no rates
# Design decision 20: the spec's task-based lead dimensions wait for `task` events and `panel.subject`.
LEAD_COLUMNS_PENDING = ["throughput per task", "defects", "escapes", "clean work", "moderation quality"]


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _load(config):
    return store.load(store.log_path(config))


def _seat_usage(data, since):
    """Seat calls per local day, seat and model from `usage` events since `since`; a total with no
    reported value is None."""
    groups, skipped = {}, 0
    for e in data["usage"]:
        t = summary._parse(e.get("ts"))
        if t is None:
            skipped += 1
            continue
        epoch = t.timestamp()
        if epoch < since:
            continue
        day = datetime.fromtimestamp(epoch).strftime("%Y-%m-%d")
        groups.setdefault((day, str(e.get("seat")), e.get("model") or ""), []).append(e)
    rows = [{"day": day, "seat": seat, "model": model or None, "calls": len(es),
             "tokens_in": summary._total(es, "tokens_in"), "tokens_out": summary._total(es, "tokens_out"),
             "cost_usd": summary._total(es, "cost_usd")}
            for (day, seat, model), es in sorted(groups.items())]
    return rows, skipped


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


def _plans(config, state, now):
    rot = config["rotation"]
    every = rot.get("probe_every_min", 15)
    arbitrated = quota.read_quota(state, config, now)
    caps = quota.read_caps(state, now, rot["cap_expiry_hours"])
    sources = quota.read_sources(state)
    out = []
    for name, plan in sorted(config["plans"].items()):
        a = arbitrated[name]
        row = {"name": name, "billing": plan["billing"], "quota": plan["quota"],
               "windows": [] if a.get("unknown") else a.get("windows", []), "pools": a.get("pools", []),
               "source": a.get("source"), "age_s": a.get("age_s"), "unknown": bool(a.get("unknown")),
               "reason": a.get("reason") if a.get("unknown") else None, "last_known_at": a.get("last_known_at"),
               "cap_until": caps[name].get("resets_at") if name in caps else None,
               "notice": quota.notice(a, plan, now, every), "cross_check": a.get("cross_check"),
               "limits": a.get("limits")}
        if plan["billing"] == "metered" and plan.get("budget_usd_month") is not None:
            row["spend"] = _spend(sources.get(name, {}).get("probe"), plan["budget_usd_month"], now, every)
        out.append(row)
    return out


def _worktrees(facts):
    """The current and pinned lead per worktree, with its open inbox counts, from `status.facts`."""
    box = facts.get("inbox")
    out = []
    for w in facts["worktrees"]:
        mine = (box or {}).get(w.get("key")) or {}
        out.append({"worktree": w["worktree"], "lead": w["lead"], "since": w["since"], "pinned": w["pinned"],
                    "asks": None if box is None else len(mine.get("asks", [])),
                    "notices": None if box is None else len(mine.get("notices", []))})
    return out


def build(config, state, now_fn=time.time, run=proc.run) -> dict:
    def models():
        data = _load(config)
        return {"seats": summary.seat_rows(data, config), "leads": summary.lead_rows(data, inbox.load(state)["items"]),
                "lead_columns_pending": list(LEAD_COLUMNS_PENDING), "small_n": SMALL_N}

    def usage():
        since = now_fn() - USAGE_DAYS * 86400
        data = _load(config)
        rows, skipped = _seat_usage(data, since)
        return {"days": USAGE_DAYS, "ccswitch": ccswitch.usage(state, since=since), "seats": rows,
                "seat_events_skipped": skipped}

    def history():
        data = _load(config)
        return {"timeline": summary.timeline(data), "disputes": summary.disputes(data),
                "findings": summary.findings(data), "markers": summary.markers(data)}

    def now():
        t = now_fn()
        data = _load(config)
        f = status.facts(config, state, t, run=run)
        return {"generated_at": t, "plans": _plans(config, state, t), "worktrees": _worktrees(f),
                "inbox_readable": f["inbox"] is not None, "watches": summary.watches(data)}

    def graph():
        return {"available": False, "reason": "not built yet"}

    return {"models": models, "usage": usage, "history": history, "now": now, "graph": graph}
