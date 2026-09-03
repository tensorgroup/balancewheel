#!/usr/bin/env python3
"""log.py — the metrics logger CLI. Every write to the log goes through here or store.append.

  log.py config [--json]              resolved config (JSON)
  log.py usage --seat S --mode M --dir D --duration N --ok B [--model M] [--reason R]
  log.py panel [--force] < record.json
  log.py check
  log.py watches
  log.py resolve <id> [--status resolved|dismissed|open] [--note …]
  log.py amend --panel-id <id> < patch.json
"""
import argparse
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config as cfgmod  # noqa: E402
import store  # noqa: E402
import watches as watchmod  # noqa: E402

SEVERITIES = ("critical", "major", "minor", "nit")
VERDICTS = ("confirmed", "refuted", "partial", "dropped")
DETAIL_REQUIRED = ("seat", "group", "title", "severity", "verdict")
DISPUTE_REQUIRED = ("summary", "challenger", "proposals", "winner", "reason")
REJECT = (ValueError, TypeError)  # malformed input; TypeError still covers a shape validation missed


def compute_counts(findings_detail):
    groups = {}
    for f in findings_detail:
        groups.setdefault(f["group"], set()).add(f["seat"])
    out = {}
    for f in findings_detail:
        c = out.setdefault(f["seat"], {"total": 0, "confirmed": 0, "refuted": 0, "partial": 0, "unique": 0})
        v = f["verdict"]
        if v == "dropped":
            continue
        c["total"] += 1
        c[v] += 1
        if v == "confirmed" and len(groups[f["group"]]) == 1:
            c["unique"] += 1
    return out


def _fail(msg):
    raise ValueError(msg)


def die(msg):
    """Rejections exit 2 (bad input); unexpected crashes keep Python's exit 1."""
    print(msg, file=sys.stderr)
    sys.exit(2)


def validate_detail(fd, seats):
    """Shared by panel and amend: shape-check every findings_detail entry."""
    if not isinstance(fd, list):
        _fail("findings_detail must be a list")
    for i, f in enumerate(fd):
        if not isinstance(f, dict):
            _fail(f"findings_detail[{i}] must be an object")
        for k in DETAIL_REQUIRED:
            if k not in f:
                _fail(f"findings_detail[{i}] missing {k}")
        if f["severity"] not in SEVERITIES:
            _fail(f"findings_detail[{i}].severity must be one of {SEVERITIES}")
        if f["verdict"] not in VERDICTS:
            _fail(f"findings_detail[{i}].verdict must be one of {VERDICTS}")
        if f["seat"] not in seats:
            _fail(f"findings_detail[{i}].seat {f['seat']!r} is not in seats")


def _is_count(v):
    return isinstance(v, int) and not isinstance(v, bool)


def check_counts(given, computed, seats, force):
    """Compare hand-filled per-seat counts (incl. unique) with counts computed from findings_detail.
    Returns (findings, note): findings are the reconciled counts; note is '' or the forced mismatch text."""
    if not isinstance(given, dict):
        _fail("findings must be an object keyed by seat")
    zero = {"total": 0, "confirmed": 0, "refuted": 0, "partial": 0, "unique": 0}
    mismatches = [f"findings.{s}: seat is not in seats" for s in given if s not in seats]
    findings = {}
    for s in seats:
        g = given.get(s, {})
        if not isinstance(g, dict):
            _fail(f"findings.{s} must be an object")
        c = computed.get(s, dict(zero))
        out = {}
        for k in zero:
            if k in g and not _is_count(g[k]):
                _fail(f"findings.{s}.{k} must be an integer")
            if k in g and g[k] != c[k]:
                mismatches.append(f"{s}.{k}: record says {g[k]}, findings_detail says {c[k]}")
            out[k] = c[k]  # computed is authoritative; a disagreement is either rejected or noted
        findings[s] = out
    if mismatches and not force:
        _fail("findings counts disagree with findings_detail: " + "; ".join(mismatches))
    return findings, ("count_mismatch: " + "; ".join(mismatches)) if mismatches else ""


def merge_findings_patch(existing, given):
    """A findings-only amend on a panel with no findings_detail has nothing to reconcile against;
    merge the patch over the panel's existing normalised counts (per seat, per key) instead of
    replacing the whole per-seat map, so seats the patch doesn't mention keep their counts."""
    if not isinstance(given, dict):
        _fail("findings must be an object keyed by seat")
    merged = {s: dict(c) for s, c in existing.items()}
    for s, counts in given.items():
        if not isinstance(counts, dict):
            _fail(f"findings.{s} must be an object")
        target = dict(merged.get(s) or {k: None for k in store.COUNT_KEYS})
        for k, v in counts.items():
            if k not in store.COUNT_KEYS:
                _fail(f"findings.{s}.{k}: unknown key")
            if not _is_count(v):
                _fail(f"findings.{s}.{k} must be an integer")
            target[k] = v
        merged[s] = target
    return merged


def validate_panel(rec, config, force):
    if not isinstance(rec, dict):
        _fail("panel record must be a JSON object")
    for k in ("target", "kind", "seats", "immediate_agreement", "findings", "findings_detail"):
        if k not in rec or rec[k] is None:
            _fail(f"panel record missing required field: {k}")
    if not isinstance(rec["target"], str) or not rec["target"].strip():
        _fail("target must be a non-empty string")
    if rec["kind"] not in cfgmod.ROLES:
        _fail(f"kind must be one of {sorted(cfgmod.ROLES)}, got {rec['kind']!r}")
    if not isinstance(rec["immediate_agreement"], bool):
        _fail("immediate_agreement must be true or false")
    if rec.get("rounds") is not None and not _is_count(rec["rounds"]):
        _fail("rounds must be an integer")
    for k in ("findings_raw", "findings_after_triage"):
        v = rec.get(k)
        if v is not None and not (_is_count(v) and v >= 0):
            _fail(f"{k} must be a non-negative integer")
    seats_in = rec["seats"]
    if not isinstance(seats_in, (list, dict)) or not seats_in:
        _fail("seats must be a non-empty list or object")
    names = list(seats_in)
    if not all(isinstance(s, str) for s in names):
        _fail("seat names must be strings")
    unknown = [s for s in names if s not in config["seats"]]
    if unknown and not force:
        _fail(f"seats not in config: {unknown} (use --force to log an on-demand seat)")
    force_note = f"forced: seats not in config: {unknown}" if unknown else ""
    seats = {}
    for s in names:
        given = seats_in[s] if isinstance(seats_in, dict) else None
        if given is not None and not isinstance(given, str):
            _fail(f"seats.{s}: model must be a string or null")
        seats[s] = given or (config["seats"][s]["model"] if s in config["seats"] else None)
    fd = rec["findings_detail"]
    validate_detail(fd, seats)
    disputes = rec.get("disputes") or []
    if not isinstance(disputes, list):
        _fail("disputes must be a list")
    for i, d in enumerate(disputes):
        if not isinstance(d, dict):
            _fail(f"disputes[{i}] must be an object")
        for k in DISPUTE_REQUIRED:
            if k not in d:
                _fail(f"disputes[{i}] missing {k}")
        for k in ("summary", "challenger", "winner", "reason"):
            if not isinstance(d[k], str):
                _fail(f"disputes[{i}].{k} must be a string")
        if not isinstance(d["proposals"], dict) or not all(
                isinstance(pk, str) and isinstance(pv, str) for pk, pv in d["proposals"].items()):
            _fail(f"disputes[{i}].proposals must be an object of string seat -> string proposal")
    findings, mismatch_note = check_counts(rec["findings"], compute_counts(fd), seats, force)
    notes = rec.get("notes", "") or ""
    if not isinstance(notes, str):
        _fail("notes must be a string")
    for extra in (force_note, mismatch_note):
        if extra:
            notes = (notes + " | " if notes else "") + extra
    out = dict(rec)
    out.update({"type": "panel", "id": uuid.uuid4().hex[:8], "ts": store.now_ts(), "seats": seats,
                "findings": findings, "findings_detail": fd, "disputes": disputes,
                "rounds": rec.get("rounds", 1), "notes": notes})
    return out


def _print_watches(new):
    for w in new:
        print(f"WATCH[{w['kind']}|{w['status']}] {w['detail']} (id {w['id']})")


def run_check(path, config, only_panel=None):
    """Append watches the log does not have yet. A watch that already exists but is open and is now
    computed as resolved (e.g. an amend recorded the triage) gets a watch-update instead of a duplicate."""
    new, flipped = [], []
    with store.locked(path):  # read-then-append must be atomic across concurrent `check`/`panel` runs
        data = store.load(path)
        existing = {w["id"]: w["status"] for w in data["watches"]}
        # Watches written by an OLDER logger (no panel_id — the id-formula/panel_id era postdates
        # them) carry ids from a different formula and may hold a truncated target. A panel-scoped
        # watch is the same watch if the kind matches and one target is a prefix of the other, so
        # those never get re-created. A modern watch already carries panel_id and is already keyed
        # by id, so it must NOT suppress a legitimate watch on a later panel with the same target.
        legacy = [(w["kind"], w["target"]) for w in data["watches"]
                  if w["kind"] != "redundant-seat" and w["panel_id"] is None]

        def seen_before(w):
            return any(k == w["kind"] and (t.startswith(w["target"]) or w["target"].startswith(t))
                       for k, t in legacy if t)

        candidates = watchmod.evaluate(data["panels"], config)
        if only_panel:
            candidates = [w for w in candidates if w.get("panel_id") == only_panel or w["kind"] == "redundant-seat"]
        for w in candidates:
            if w["kind"] == "redundant-seat":
                # should_refire is the gate; it already accounts for open watches and the
                # post-dismissal cooldown, so an existing exact id is the only other reason to skip.
                if not watchmod.should_refire(w, data, config):
                    continue
                if w["id"] in existing:
                    continue
                store.append(path, w)
                new.append(w)
                existing[w["id"]] = w["status"]
            elif w["id"] not in existing:
                if seen_before(w):
                    continue
                store.append(path, w)
                new.append(w)
                existing[w["id"]] = w["status"]
            elif existing[w["id"]] == "open" and w["status"] == "resolved":
                store.append(path, {"type": "watch-update", "watch_id": w["id"], "status": "resolved",
                                    "note": "re-evaluated: " + w["detail"]})
                flipped.append(w)
                existing[w["id"]] = "resolved"
    _print_watches(new)
    for w in flipped:
        print(f"WATCH[{w['kind']}|resolved] (was open) {w['detail']} (id {w['id']})")
    open_count = sum(1 for s in existing.values() if s == "open")
    return new, open_count


def cmd_config(args, config):
    print(json.dumps(config, indent=None if args.json else 2))


def cmd_usage(args, config):
    store.append(store.log_path(config), {
        "type": "usage", "seat": args.seat, "model": args.model, "mode": args.mode, "dir": args.dir,
        "duration_s": args.duration, "ok": args.ok.lower() == "true", "reason": args.reason,
    })


def cmd_panel(args, config):
    try:
        rec = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        die(f"panel record is not valid JSON: {e}")
    try:
        rec = validate_panel(rec, config, args.force)
    except REJECT as e:
        die(f"panel record rejected: {e}")
    path = store.log_path(config)
    store.append(path, rec)
    print(f"panel {rec['id']} logged")
    _, open_count = run_check(path, config, only_panel=rec["id"])
    if open_count:
        print(f"{open_count} open watch suggestion(s) — `log.py watches` to list, `log.py resolve <id>` to close")


def cmd_check(args, config):
    new, open_count = run_check(store.log_path(config), config)
    print(f"{len(new)} new watch(es); {open_count} open suggestion(s)")


def cmd_watches(args, config):
    for w in store.load(store.log_path(config))["watches"]:
        note = w["updates"][-1]["note"] if w["updates"] else ""
        print(f"{w['id']}  {(w['ts'] or '')[:10]}  {w['kind']:<18} {w['status']:<9} {w['target'][:48]}{('  — ' + note) if note else ''}")


def cmd_resolve(args, config):
    path = store.log_path(config)
    with store.locked(path):  # read-then-append must be atomic across concurrent resolve/check runs
        known = {w["id"] for w in store.load(path)["watches"]}
        if args.id not in known:
            die(f"unknown watch id {args.id}")
        store.append(path, {"type": "watch-update", "watch_id": args.id, "status": args.status, "note": args.note})
    print(f"watch {args.id} → {args.status}")


def cmd_amend(args, config):
    path = store.log_path(config)
    try:
        patch = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        die(f"patch is not valid JSON: {e}")
    if not isinstance(patch, dict):
        die("patch must be a JSON object")
    allowed = set(store.PANEL_AMENDABLE) | {"note"}
    bad = set(patch) - allowed
    if bad:
        die(f"amend may only set {sorted(allowed)}; got {sorted(bad)}")
    for k in ("findings_raw", "findings_after_triage"):
        if k in patch and patch[k] is not None and not (_is_count(patch[k]) and patch[k] >= 0):
            die(f"amend {k} must be a non-negative integer")
    with store.locked(path):  # load -> validate -> append must be atomic; run_check locks separately, after
        panels = {p["id"]: p for p in store.load(path)["panels"]}
        if args.panel_id not in panels:
            die(f"unknown panel id {args.panel_id}")
        seats = list(panels[args.panel_id]["seats"])
        try:
            if "findings_detail" in patch:
                validate_detail(patch["findings_detail"], seats)
                computed = compute_counts(patch["findings_detail"])
                given = patch.get("findings", {})
                patch["findings"], note = check_counts(given, computed, seats, args.force)
                if note:
                    patch["notes"] = ((patch.get("notes") or panels[args.panel_id]["notes"] or "") + " | " + note).strip(" |")
            elif "findings" in patch and panels[args.panel_id]["findings_detail"]:
                computed = compute_counts(panels[args.panel_id]["findings_detail"])
                patch["findings"], note = check_counts(patch["findings"], computed, seats, args.force)
                if note:
                    patch["notes"] = ((patch.get("notes") or panels[args.panel_id]["notes"] or "") + " | " + note).strip(" |")
            elif "findings" in patch:
                patch["findings"] = merge_findings_patch(panels[args.panel_id]["findings"], patch["findings"])
        except REJECT as e:
            die(f"amend rejected: {e}")
        store.append(path, dict(patch, type="panel-amend", panel_id=args.panel_id))
    print(f"panel {args.panel_id} amended")
    run_check(path, config, only_panel=args.panel_id)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="log.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("config"); c.add_argument("--json", action="store_true")
    u = sub.add_parser("usage")
    u.add_argument("--seat", required=True); u.add_argument("--mode", required=True, choices=["new", "resume"])
    u.add_argument("--dir", required=True); u.add_argument("--duration", type=int, required=True)
    u.add_argument("--ok", required=True, choices=["true", "false"])
    u.add_argument("--model", default=None); u.add_argument("--reason", default=None)
    p = sub.add_parser("panel"); p.add_argument("--force", action="store_true")
    sub.add_parser("check"); sub.add_parser("watches")
    r = sub.add_parser("resolve"); r.add_argument("id")
    r.add_argument("--status", default="resolved", choices=list(store.WATCH_STATUSES)); r.add_argument("--note", default="")
    a = sub.add_parser("amend"); a.add_argument("--panel-id", required=True); a.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    try:
        config = cfgmod.load_config()
    except cfgmod.ConfigError as e:
        die(f"config error: {e}")
    {"config": cmd_config, "usage": cmd_usage, "panel": cmd_panel, "check": cmd_check,
     "watches": cmd_watches, "resolve": cmd_resolve, "amend": cmd_amend}[args.cmd](args, config)


if __name__ == "__main__":
    main()
