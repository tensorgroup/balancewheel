"""summary.py — the numbers behind the seat and lead views, shared by the static dashboard and the cockpit.

Pure functions over store.load() output; nothing here reads or writes a file. A token or cost total is
None when no usage event for that seat reports it: "unavailable" is not zero.
"""
from datetime import datetime, timezone

SEVERE = ("critical", "major")
VERDICTS = ("confirmed", "partial", "refuted")


def _panels(data):
    return sorted(data["panels"], key=lambda p: p["ts"] or "")


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0


def seat_names(config, data):
    return list(config["seats"]) + sorted({s for p in data["panels"] for s in p["seats"] if s not in config["seats"]})


def _standout(panel, seat):
    fd = panel["findings_detail"]
    if not isinstance(fd, list):
        return False
    rows = [f for f in fd if isinstance(f, dict)]
    for f in rows:
        if f.get("seat") != seat or f.get("verdict") != "confirmed" or f.get("severity") not in SEVERE:
            continue
        g = f.get("group")
        if g is None or not any(o.get("seat") != seat and o.get("group") == g for o in rows):
            return True
    return False


def _total(events, key):
    vals = [e[key] for e in events if _num(e.get(key))]
    return sum(vals) if vals else None


def seat_rows(data, config):
    panels = _panels(data)
    w = config["watches"]
    window = panels[-w["redundant_window_panels"]:] if w["redundant_window_panels"] else panels
    rows = []
    for seat in seat_names(config, data):
        mine = [p for p in panels if seat in p["findings"]]
        conf, part, ref = (sum(p["findings"][seat][k] or 0 for p in mine) for k in VERDICTS)
        recent = [p for p in window if seat in p["findings"] and isinstance(p["findings"][seat].get("unique"), int)][-w["redundant_min_panels"]:]
        calls = [u for u in data["usage"] if u.get("seat") == seat]
        disputes = [d for p in panels for d in p["disputes"]]
        rows.append({
            "seat": seat, "panels": len(mine), "confirmed": conf, "partial": part, "refuted": ref, "n": conf + part + ref,
            "unique_recent": sum(p["findings"][seat]["unique"] for p in recent), "recent_panels": len(recent),
            "standout_panels": sum(1 for p in panels if _standout(p, seat)),
            "challenges": sum(1 for d in disputes if d.get("challenger") == seat),
            "wins": sum(1 for d in disputes if d.get("winner") == seat),
            "involved": sum(1 for d in disputes if seat in (d.get("proposals") or {}) or d.get("challenger") == seat),
            "calls": len(calls), "tokens_in": _total(calls, "tokens_in"), "tokens_out": _total(calls, "tokens_out"),
            "cost_usd": _total(calls, "cost_usd"),
        })
    return rows


def timeline(data):
    out = []
    prev = {}
    for p in _panels(data):
        raw = p["findings_raw"] if isinstance(p["findings_raw"], int) else sum(c["total"] or 0 for c in p["findings"].values())
        changes = []
        for seat, model in p["seats"].items():
            if seat in prev and model and prev[seat] and prev[seat] != model:
                changes.append({"seat": seat, "from": prev[seat], "to": model})
            if model:
                prev[seat] = model
        out.append({"ts": p["ts"], "target": p["target"], "raw": raw, "disputes": len(p["disputes"]),
                    "seats": list(p["seats"]), "immediate_agreement": p["immediate_agreement"], "model_changes": changes})
    return out


def disputes(data):
    return [{"ts": p["ts"], "panel_id": p["id"], "target": p["target"], "summary": d.get("summary"),
             "challenger": d.get("challenger"), "proposals": d.get("proposals") or {}, "winner": d.get("winner"),
             "reason": d.get("reason")}
            for p in sorted(data["panels"], key=lambda p: p["ts"] or "", reverse=True) for d in p["disputes"]]


def findings(data):
    return [{"panel_id": p["id"], "ts": p["ts"], "target": p["target"], "kind": p["kind"], "seats": list(p["seats"]),
             "rows": [f for f in p["findings_detail"] if isinstance(f, dict)] if isinstance(p["findings_detail"], list) else []}
            for p in sorted(data["panels"], key=lambda p: p["ts"] or "", reverse=True)]


def markers(data):
    by_id = {s.get("session_id"): s for s in data["sessions"]}
    out = []
    for e in data["events"]:
        t = e.get("type")
        if t == "session":
            s = by_id.get(e.get("session_id")) or {}
            start = e.get("phase") == "start"
            out.append({"ts": e.get("ts"), "kind": "session-start" if start else "session-end", "project": e.get("project"),
                        "lead": e.get("lead") if start else s.get("lead"), "reason": None if start else e.get("reason")})
        elif t == "lead":
            to = e.get("to")
            out.append({"ts": e.get("ts"), "kind": "lead", "project": e.get("project"),
                        "lead": to.get("lead") if isinstance(to, dict) else None, "reason": e.get("reason")})
    return sorted(out, key=lambda m: m["ts"] or "")


def watches(data):
    return [{"id": w["id"], "kind": w["kind"], "target": w["target"], "ts": w["ts"]}
            for w in data["watches"] if w["status"] == "open"]


def _minutes(s):
    try:
        a = datetime.strptime(s["start"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        b = datetime.strptime(s["end"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return 0.0
    return max(0.0, (b - a).total_seconds() / 60)


def lead_rows(data, inbox_items):
    """Per (runtime, model): sessions, lead minutes (ended sessions only), switches away on a cap, overrides,
    ratings, asks. The spec's task-based columns need `task` events and are not computed here."""
    rows, by_session = {}, {}
    for s in data["sessions"]:
        if not s.get("runtime"):
            continue
        key = (s["runtime"], s.get("model"))
        r = rows.setdefault(key, {"runtime": key[0], "model": key[1], "leads": set(), "sessions": 0, "minutes": 0.0,
                                  "cap_switches": 0, "overrides": 0, "good": 0, "bad": 0, "asks": 0})
        by_session[s["session_id"]] = r
        r["sessions"] += 1
        r["minutes"] += _minutes(s)
        r["cap_switches"] += 1 if s.get("end_reason") == "switch" else 0
        if s.get("lead"):
            r["leads"].add(s["lead"])
    # an override targets the lead that started: attribute it to that lead's (runtime, model) via its sessions
    by_lead = {}
    for s in data["sessions"]:
        if s.get("lead") and s.get("runtime"):
            by_lead[s["lead"]] = (s["runtime"], s.get("model"))
    for e in data["leads"]:
        if e.get("reason") != "override":
            continue
        to = e.get("to") if isinstance(e.get("to"), dict) else {}
        key = by_lead.get(to.get("lead"))
        if key in rows:
            rows[key]["overrides"] += 1
    for e in data["ratings"]:
        r = by_session.get(e.get("session_id"))
        if r is not None and e.get("value") in ("good", "bad"):
            r[e["value"]] += 1
    ask_ids = set()
    asks = list(data.get("asks") or []) + [i for i in (inbox_items or []) if isinstance(i, dict) and i.get("type") == "ask"]
    for a in asks:
        r = by_session.get(a.get("session_id"))
        ident = a.get("id")
        if r is None or (ident is not None and ident in ask_ids):
            continue
        ask_ids.add(ident)
        r["asks"] += 1
    out = []
    for r in rows.values():
        r["leads"] = sorted(r["leads"])
        r["minutes"] = round(r["minutes"], 1)
        out.append(r)
    return sorted(out, key=lambda r: (r["runtime"], r["model"] or ""))
