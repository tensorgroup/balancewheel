"""export.py — owns the `model-seats` catalog block: the entries bw writes between the pack's markers.

Pure functions over the resolved config and `store.load()` output. An entry is `{name, status, notes}`.
Numbers count only the panels where the seat ran its current model. Entries carry no target, finding
title, path, project, profile name, lead id, or conversation.
"""
from lead import paths  # noqa: F401  (puts metrics/ on sys.path)
from lead import quota
from seats import verify
import summary  # noqa: E402

NOT_YET = "not yet run"


class PackError(Exception):
    """Every refusal. The message is the one line the user sees."""


def exported_seats(config):
    return verify.seat_items(config)


def model_panels(panels, seat, model):
    """Every panel, sorted by `ts`, where the seat ran `model`. A model that returns keeps its earlier panels."""
    return sorted((p for p in panels if p["seats"].get(seat) == model), key=lambda p: p["ts"] or "")


def _first_lead_runtime(config):
    profile = (config.get("profiles") or {}).get(config.get("default_profile")) or {}
    order = profile.get("lead_order") or []
    lead = (config.get("leads") or {}).get(order[0]) if order else None
    return lead.get("runtime") if lead else None


def seat_role(config, seat):
    spec = config["seats"][seat]
    if spec.get("title"):
        return spec["title"]
    roles = spec.get("roles") or []
    if roles and roles[0] in ("plan", "decision"):
        return "Planning seat"
    if spec.get("tier") == "reserved":
        return "Reserved escalation seat"
    if spec["runtime"] == _first_lead_runtime(config):
        return "Blind same-vendor seat"
    return "Peer seat"


def _dates(stamps):
    return [t for t in (summary._parse(s) for s in stamps) if t is not None]


def _month(stamps):
    parsed = _dates(stamps)
    return min(parsed).strftime("%Y-%m") if parsed else NOT_YET


def _last_date(stamps):
    parsed = _dates(stamps)
    return max(parsed).strftime("%Y-%m-%d") if parsed else "an unknown date"


def seat_entry(config, data, seat):
    spec = config["seats"][seat]
    model = spec["model"]
    panels = model_panels(data["panels"], seat, model)
    stamps = [p["ts"] for p in panels] + [u.get("ts") for u in data["usage"]
                                          if u.get("seat") == seat and u.get("model") == model]
    name = f"{seat_role(config, seat)} (reference roster, {_month(stamps)}, {model} via {spec['runtime']})"
    row = next(r for r in summary.seat_rows(dict(data, panels=panels), config) if r["seat"] == seat)
    if spec.get("tier") == "reserved":
        status = "reserved"
    elif row["panels"] < config["watches"]["redundant_min_panels"] or any(
            w["kind"] == "redundant-seat" and w["target"] == seat and w["status"] == "open" for w in data["watches"]):
        status = "review-required"
    else:
        status = "preferred"
    if panels:
        record = (f"Record under this model to {_last_date([p['ts'] for p in panels])}: {row['panels']} panels, "
                  f"{row['confirmed']} confirmed, {row['partial']} partial, {row['refuted']} refuted; "
                  f"{row['unique_recent']} unique in the last {row['recent_panels']} panels; "
                  f"{row['wins']} of {row['involved']} disputes won.")
    else:
        record = "No panels under this model yet."
    return {"name": name, "status": status, "notes": spec["policy"].strip() + " " + record}


def lead_model(config, state, lead):
    return quota.lead_model(state, config["leads"][lead])[0]


def _lead_ids(config):
    profiles = config.get("profiles") or {}
    names = [n for n in [config.get("default_profile")] if n in profiles]
    names += sorted(n for n in profiles if n not in names)
    ids = []
    for n in names:
        ids += [lead for lead in profiles[n].get("lead_order") or [] if lead not in ids]
    return ids


def lead_entries(config, data, state):
    out = []
    rows = summary.lead_rows(data, [])
    for lead in _lead_ids(config):
        runtime = config["leads"][lead]["runtime"]
        model = lead_model(config, state, lead)
        starts = [s.get("start") for s in data["sessions"] if s.get("runtime") == runtime and s.get("model") == model]
        name = f"Lead (reference roster, {_month(starts)}, {model} via {runtime})"
        row = next((r for r in rows if r["runtime"] == runtime and r["model"] == model), None)
        head = "A lead in a lead order; bw does not score lead quality yet. "
        if row:
            tail = (f"Record under this model to {_last_date(starts)}: {row['sessions']} sessions, {row['minutes']} lead minutes, "
                    f"{row['good']} good and {row['bad']} bad ratings, {row['cap_switches']} switches on a cap.")
        else:
            tail = "No sessions under this model yet."
        out.append({"name": name, "status": "review-required", "notes": head + tail})
    return out


def _check_policies(config, seats):
    policy = {s: config["seats"][s]["policy"] for s in seats}
    empty = [s for s, p in policy.items() if not p.strip()]
    if empty:
        raise PackError("seats with no policy (the pack must say why a seat holds its duties): " + ", ".join(empty))
    stub = [s for s, p in policy.items() if "TODO" in p or ("<" in p and ">" in p)]
    if stub:
        raise PackError("seats whose policy is still a placeholder: " + ", ".join(stub))


def _with(name, extra):
    return name[:-1] + extra + ")"


def entries(config, data, state):
    """Seat entries, then lead entries. A name two entries share gets a distinguisher; one that still
    collides is refused, because `bump_kind` and `bullet` key by name."""
    seats = exported_seats(config)
    _check_policies(config, seats)
    items = []
    for s in seats:
        effort = config["seats"][s].get("effort")
        items.append((seat_entry(config, data, s), s, f", effort {effort}" if effort else None))
    for e, lead in zip(lead_entries(config, data, state), _lead_ids(config)):
        plan = config["leads"][lead].get("plan")
        items.append((e, None, f", {plan} plan" if plan else None))
    counts = {}
    for e, _, _ in items:
        counts[e["name"]] = counts.get(e["name"], 0) + 1
    out, owner = [], {}
    for e, seat, extra in items:
        if counts[e["name"]] > 1 and extra:
            e = dict(e, name=_with(e["name"], extra))
        if e["name"] in owner:
            first = seat or owner[e["name"]]
            hint = f"set seats.{first}.title on one" if first else "give the leads different plans or models"
            raise PackError(f"two catalog entries would be named {e['name']}; {hint}")
        owner[e["name"]] = seat
        out.append(e)
    return out
