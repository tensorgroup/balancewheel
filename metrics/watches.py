"""watches.py — roster self-checks over logged panels. Pure functions; no I/O.

panel-bloat        raw >= bloat_total, or avg/seat >= bloat_per_seat with >= bloat_min_seats.
                   'resolved' (performed) when findings_raw/findings_after_triage are recorded,
                   else 'open' (suggest triage).
verification-skim  >= skim_min_seats seats, >= skim_min_findings findings, 0 refuted, 0 partial
                   (None partial on legacy panels counts as 0). Suggests re-verifying a sample.
redundant-seat     a seat with unique == 0 on >= redundant_min_panels of its panels within the
                   last redundant_window_panels panels; panels with unique None are excluded.
Watch ids are sha1(kind|target|panel_id)[:8] — no timestamp — so re-running is idempotent for
panel-scoped watches. redundant-seat's id moves with the last panel, so the caller (log.py
run_check) applies the re-fire rule: never while one is open for that seat, and after a
dismissal only once redundant_min_panels further panels have passed (see should_refire).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from store import legacy_watch_id, watch_id  # noqa: E402


def _is_count(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _n(v):
    return v if _is_count(v) else 0


def _sum(panel, key):
    return sum(_n(c.get(key)) for c in panel["findings"].values())


def _seat_count(panel):
    return max(len(panel["seats"]), len(panel["findings"]))


def _bloat(panel, w):
    seats = _seat_count(panel)
    raw = panel.get("findings_raw") if _is_count(panel.get("findings_raw")) else _sum(panel, "total")
    avg = raw / seats if seats else 0
    if not (raw >= w["bloat_total"] or (seats >= w["bloat_min_seats"] and avg >= w["bloat_per_seat"])):
        return None
    performed = _is_count(panel.get("findings_raw")) and _is_count(panel.get("findings_after_triage"))
    if performed:
        detail = (f"{seats} seats produced {raw} raw findings; triage performed → "
                  f"{panel['findings_after_triage']} verified")
    else:
        detail = f"{seats} seats produced {raw} raw findings with no triage recorded — dedupe, drop nits, re-log with findings_raw/findings_after_triage"
    return {"type": "watch", "id": watch_id("panel-bloat", panel["target"], panel["id"]),
            "kind": "panel-bloat", "target": panel["target"], "panel_id": panel["id"],
            "status": "resolved" if performed else "open", "detail": detail}


def _skim(panel, w):
    seats = _seat_count(panel)
    total = _sum(panel, "total")
    if seats < w["skim_min_seats"] or total < w["skim_min_findings"]:
        return None
    if _sum(panel, "refuted") or _sum(panel, "partial"):
        return None
    return {"type": "watch", "id": watch_id("verification-skim", panel["target"], panel["id"]),
            "kind": "verification-skim", "target": panel["target"], "panel_id": panel["id"], "status": "open",
            "detail": f"{seats} seats, {total} findings, 0 refuted and 0 partial — re-verify a sample and report which seat's findings failed"}


def _redundant(panels, config):
    w = config["watches"]
    if w["redundant_min_panels"] < 1:  # 0 would make every seat "redundant" on an empty window
        return []
    window = panels[-w["redundant_window_panels"]:] if w["redundant_window_panels"] else panels
    out = []
    seats = set(config.get("seats", {})) | {s for p in window for s in p["findings"]}
    for seat in sorted(seats):
        mine = [p for p in window if seat in p["findings"] and _is_count(p["findings"][seat].get("unique"))]
        if len(mine) < w["redundant_min_panels"]:
            continue
        recent = mine[-w["redundant_min_panels"]:]
        if any(p["findings"][seat]["unique"] > 0 for p in recent):
            continue
        last = window[-1]["id"]  # newest panel in the window, so the id keeps moving if the seat sits out later panels
        excluded = sum(1 for p in window if seat in p["findings"] and not _is_count(p["findings"][seat].get("unique")))
        note = f" ({excluded} panel(s) without a recorded unique count excluded)" if excluded else ""
        out.append({"type": "watch", "id": watch_id("redundant-seat", seat, last),
                    "kind": "redundant-seat", "target": seat, "panel_id": last, "status": "open",
                    "detail": f"seat '{seat}' has 0 unique catches over its last {len(recent)} panels{note} — consider dropping it from the default roster (your call, never automatic)"})
    return out


def should_refire(candidate, data, config):
    """For redundant-seat: False while an open watch exists for the seat; after a resolved or
    dismissed one, True only once redundant_min_panels further panels have been *logged after the
    decision* (counted in log order, so same-second timestamps cannot confuse it)."""
    if candidate["kind"] != "redundant-seat":
        return True
    same = [w for w in data["watches"] if w["kind"] == "redundant-seat" and w["target"] == candidate["target"]]
    if not same:
        return True
    if any(w["status"] == "open" for w in same):
        return False
    ids = {w["id"] for w in same}
    events = data["events"]
    last = -1
    for i, e in enumerate(events):
        if e.get("type") == "watch":
            eid = e.get("id") or legacy_watch_id(e.get("ts", ""), e.get("kind", ""), e.get("target", ""))
            if eid in ids:
                last = i
        elif e.get("type") == "watch-update" and e.get("watch_id") in ids:
            last = i
    since = sum(1 for e in events[last + 1:] if e.get("type") == "panel")
    return since >= config["watches"]["redundant_min_panels"]


def evaluate(panels, config):
    """Candidate watch events for the whole log; the caller dedupes against existing ids and
    applies should_refire() to redundant-seat candidates."""
    w = config["watches"]
    panels = sorted(panels, key=lambda p: p.get("ts") or "")
    out = []
    for p in panels:
        for fn in (_bloat, _skim):
            r = fn(p, w)
            if r:
                out.append(r)
    out.extend(_redundant(panels, config))
    return out
