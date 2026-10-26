"""views.py — the cockpit's JSON views: Models, Usage, History, Now, Graph.

`build` returns zero-argument callables the server maps to `/api/<name>`. Each call reads fresh:
one metrics store load, at most one cc-switch copy. Numbers nobody reported are None ("unavailable"
on the page), never 0. Nothing here writes a file.
"""
import time
from datetime import datetime

from lead import paths  # noqa: F401  (puts metrics/ on sys.path)
from lead import ccswitch, inbox, proc, registry, status
import store  # noqa: E402
import summary  # noqa: E402

USAGE_DAYS = 30
SMALL_N = 10  # below this many verdicts a row shows counts only, no rates
# Design decision 20: the spec's task-based lead dimensions wait for `task` events and `panel.subject`.
LEAD_COLUMNS_PENDING = ["throughput per task", "defects", "escapes", "clean work", "moderation quality"]


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


def _plans(config, state, now):
    """status.plan_facts, with the cap under the cockpit's name `cap_until`."""
    return [dict({k: v for k, v in p.items() if k != "capped_until"}, cap_until=p["capped_until"])
            for p in status.plan_facts(config, state, now)]


def _worktrees(facts):
    """The current and pinned lead per worktree, with its open inbox counts, from `status.worktree_facts`."""
    box = facts.get("inbox")
    out = []
    for w in facts["worktrees"]:
        mine = (box or {}).get(w.get("key")) or {}
        out.append({"worktree": w["worktree"], "lead": w["lead"], "since": w["since"], "pinned": w["pinned"],
                    "asks": None if box is None else len(mine.get("asks", [])),
                    "notices": None if box is None else len(mine.get("notices", []))})
    return out


# Graph layers, left to right: the registry's edge direction (design: project -> ... -> model).
LAYERS = (("projects", "project", 0), ("profiles", "profile", 1), ("leads", "lead", 2), ("seats", "seat", 2),
          ("tools", "tool", 3), ("plans", "plan", 4), ("models", "model", 5))
HEALTHS = ("verified", "pending", "capped", "unknown")


def layout(reg) -> dict:
    """Layered positions for a registry snapshot: x by kind's layer, y by label order within the layer.
    Pure and deterministic; an edge whose endpoint is not a node is dropped."""
    reg = reg if isinstance(reg, dict) else {}
    layers = {}
    for coll, kind, layer in LAYERS:
        for n in reg.get(coll) or []:
            if isinstance(n, dict) and isinstance(n.get("id"), str):
                health = n.get("health") if n.get("health") in HEALTHS else "unknown"
                label = str(n.get("name") if n.get("name") is not None else n["id"])
                layers.setdefault(layer, {})[n["id"]] = {"id": n["id"], "label": label, "kind": kind,
                                                         "layer": layer, "health": health}
    nodes = []
    for layer in sorted(layers):
        for i, n in enumerate(sorted(layers[layer].values(), key=lambda n: (n["label"], n["id"]))):
            nodes.append(dict(n, x=40 + layer * 200, y=30 + i * 36))
    ids = {n["id"] for n in nodes}
    edges, seen = [], set()
    for e in reg.get("edges") or []:
        if isinstance(e, dict) and e.get("from") in ids and e.get("to") in ids and (e["from"], e["to"]) not in seen:
            seen.add((e["from"], e["to"]))
            edges.append({"from": e["from"], "to": e["to"]})
    return {"nodes": nodes, "edges": edges}


def build(config, state, now_fn=time.time, run=proc.run) -> dict:
    def models():
        data = _load(config)
        try:
            items, readable = inbox.load(state)["items"], True
        except (OSError, ValueError):  # an unreadable inbox costs the asks column, not the tab
            items, readable = [], False
        return {"seats": summary.seat_rows(data, config), "leads": summary.lead_rows(data, items),
                "inbox_readable": readable, "lead_columns_pending": list(LEAD_COLUMNS_PENDING), "small_n": SMALL_N}

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
        f = status.worktree_facts(state)  # local reads only: no Orca or cc-switch check per request
        return {"generated_at": t, "plans": _plans(config, state, t), "worktrees": _worktrees(f),
                "inbox_readable": f["inbox"] is not None, "watches": summary.watches(data)}

    def graph():
        try:
            return layout(registry.build(config, state, now_fn(), run))
        except Exception as e:  # a broken source costs the graph, never the server
            return {"available": False, "reason": "the registry could not be built (" + type(e).__name__ + ")"}

    return {"models": models, "usage": usage, "history": history, "now": now, "graph": graph}
