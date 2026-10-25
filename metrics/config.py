"""config.py — load and validate ~/.config/balancewheel/config.json.

Resolved config has absolute paths, defaults applied, and every seat filled in
(`enabled`, `roles`, `args`, `effort`, `provider`, `policy`). Errors name the key path.
"""
import json
import os
import re
from pathlib import Path

RUNTIMES = {"claude-code", "codex", "pi", "kimi", "agy"}
ROLES = {"design", "plan", "review", "debug", "decision"}
EFFORT_LEVELS = {
    "codex": {"low", "medium", "high", "xhigh", "max"},  # "ultra" adds auto task delegation; excluded until verified read-only
    "pi": {"off", "minimal", "low", "medium", "high", "xhigh"},
    "kimi": {"low", "high", "max"},
}
SEAT_NAME = re.compile(r"^[a-z0-9-]+$")
TOP_KEYS = {"_comment", "schema_version", "seats", "state_dir", "metrics_log", "commands_dir", "bin_dir", "watches"}
V2_TOP_KEYS = {"plans", "tools", "leads", "profiles", "projects", "default_profile", "rotation", "updates", "cockpit", "team"}
SEAT_KEYS = {"runtime", "model", "effort", "roles", "args", "provider", "policy", "enabled"}
V2_SEAT_KEYS = {"plan", "fallback"}
BILLING = {"plan", "metered"}
QUOTA_SOURCES = {"statusline", "probe", "none"}
DEPTHS = {"standard", "deep", "deep-on-stakes"}
ROUTER_MODES = {"off", "shadow", "acting"}
ROTATION_DEFAULTS = {"return_above_pct": 50, "cap_expiry_hours": 5, "probe_every_min": 15}
UPDATES_DEFAULTS = {"min_interval_hours": 0}
WATCH_DEFAULTS = {
    "bloat_total": 30, "bloat_per_seat": 8, "bloat_min_seats": 3,
    "skim_min_seats": 3, "skim_min_findings": 15,
    "redundant_min_panels": 5, "redundant_window_panels": 10,
}
DEFAULT_PATH = "~/.config/balancewheel/config.json"


class ConfigError(Exception):
    pass


def config_path(path=None):
    return path or os.environ.get("BALANCEWHEEL_CONFIG") or os.path.expanduser(DEFAULT_PATH)


def _abs(p):
    return os.path.abspath(os.path.expanduser(str(p)))


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _expect(cond, msg):
    if not cond:
        raise ConfigError(msg)


def _validate_seat(name, seat, version=1):
    kp = f"seats.{name}"
    _expect(SEAT_NAME.match(name), f"{kp}: seat name must match [a-z0-9-]+")
    _expect(isinstance(seat, dict), f"{kp}: must be an object")
    unknown = sorted(set(seat) - SEAT_KEYS - (V2_SEAT_KEYS if version >= 2 else set()))
    if unknown:  # not via _expect: an f-string would index an empty list eagerly
        raise ConfigError(f"{kp}.{unknown[0]}: unknown key")
    rt = seat.get("runtime")
    _expect(rt in RUNTIMES, f"{kp}.runtime: must be one of {sorted(RUNTIMES)}, got {rt!r}")
    _expect(isinstance(seat.get("model"), str) and seat["model"], f"{kp}.model: required string")
    roles = seat.get("roles", [])
    _expect(isinstance(roles, list) and all(isinstance(r, str) for r in roles), f"{kp}.roles: must be a list of strings")
    bad = [r for r in roles if r not in ROLES]
    if bad:
        raise ConfigError(f"{kp}.roles: unknown role {bad[0]!r}; valid: {sorted(ROLES)}")
    effort = seat.get("effort")
    if effort is not None:
        _expect(rt in EFFORT_LEVELS,
                f"{kp}.effort: not accepted for runtime {rt!r} (agy encodes effort in the model id; claude-code has no effort knob)")
        _expect(effort in EFFORT_LEVELS[rt], f"{kp}.effort: {effort!r} not valid for {rt}; valid: {sorted(EFFORT_LEVELS[rt])}")
    args = seat.get("args", [])
    _expect(isinstance(args, list) and all(isinstance(a, str) for a in args), f"{kp}.args: must be a list of strings")
    provider = seat.get("provider", {})
    _expect(isinstance(provider, dict), f"{kp}.provider: must be an object")
    policy = seat.get("policy", "")
    _expect(isinstance(policy, str), f"{kp}.policy: must be a string")
    enabled = seat.get("enabled", True)
    _expect(isinstance(enabled, bool), f"{kp}.enabled: must be true or false")
    return {"runtime": rt, "model": seat["model"], "effort": effort, "roles": roles,
            "args": args, "provider": provider, "policy": policy, "enabled": enabled,
            "plan": seat.get("plan"), "fallback": seat.get("fallback")}


def _name_ok(kp, name):
    _expect(isinstance(name, str) and SEAT_NAME.match(name), f"{kp}: name must match [a-z0-9-]+")


def _validate_plans(obj):
    plans = obj.get("plans", {})
    _expect(isinstance(plans, dict), "plans: must be an object")
    out = {}
    for name, p in plans.items():
        kp = f"plans.{name}"
        _name_ok(kp, name)
        _expect(isinstance(p, dict), f"{kp}: must be an object")
        bad = sorted(set(p) - {"vendor", "billing", "quota", "budget_usd_month"})
        if bad:
            raise ConfigError(f"{kp}.{bad[0]}: unknown key")
        _expect(p.get("billing") in BILLING, f"{kp}.billing: must be one of {sorted(BILLING)}")
        quota = p.get("quota", "none")
        _expect(quota in QUOTA_SOURCES, f"{kp}.quota: must be one of {sorted(QUOTA_SOURCES)}")
        if "budget_usd_month" in p:
            _expect(p["billing"] == "metered", f"{kp}.budget_usd_month: only on metered plans")
            b = p["budget_usd_month"]
            _expect(isinstance(b, (int, float)) and not isinstance(b, bool) and b >= 0,
                    f"{kp}.budget_usd_month: must be a non-negative number")
        out[name] = {"vendor": p.get("vendor", ""), "billing": p["billing"], "quota": quota,
                     "budget_usd_month": p.get("budget_usd_month")}
    return out


def _validate_tools(obj):
    tools = obj.get("tools", {})
    _expect(isinstance(tools, dict), "tools: must be an object")
    for name, t in tools.items():
        kp = f"tools.{name}"
        _expect(isinstance(t, dict), f"{kp}: must be an object")
        if "update" in t:
            u = t["update"]
            _expect(isinstance(u, list) and u and all(isinstance(a, str) for a in u),
                    f"{kp}.update: must be a non-empty argv list of strings")
    return tools


def _validate_leads(obj, plans):
    leads = obj.get("leads", {})
    _expect(isinstance(leads, dict), "leads: must be an object")
    out = {}
    for name, ld in leads.items():
        kp = f"leads.{name}"
        _name_ok(kp, name)
        _expect(isinstance(ld, dict), f"{kp}: must be an object")
        bad = sorted(set(ld) - {"runtime", "model", "plan", "args"})
        if bad:
            raise ConfigError(f"{kp}.{bad[0]}: unknown key")
        _expect(ld.get("runtime") in RUNTIMES, f"{kp}.runtime: must be one of {sorted(RUNTIMES)}, got {ld.get('runtime')!r}")
        _expect(isinstance(ld.get("model"), str) and ld["model"], f"{kp}.model: required string")
        _expect(ld.get("plan") in plans, f"{kp}.plan: must name a plan, got {ld.get('plan')!r}")
        args = ld.get("args", [])
        _expect(isinstance(args, list) and all(isinstance(a, str) for a in args), f"{kp}.args: must be a list of strings")
        out[name] = {"runtime": ld["runtime"], "model": ld["model"], "plan": ld["plan"], "args": args}
    return out


def _validate_profiles(obj, leads, seats):
    profiles = obj.get("profiles", {})
    _expect(isinstance(profiles, dict), "profiles: must be an object")
    out = {}
    for name, pr in profiles.items():
        kp = f"profiles.{name}"
        _name_ok(kp, name)
        _expect(isinstance(pr, dict), f"{kp}: must be an object")
        bad = sorted(set(pr) - {"lead_order", "checkpoints", "depth", "router"})
        if bad:
            raise ConfigError(f"{kp}.{bad[0]}: unknown key")
        order = pr.get("lead_order", [])
        _expect(isinstance(order, list), f"{kp}.lead_order: must be a list")
        for ld in order:
            _expect(ld in leads, f"{kp}.lead_order: unknown lead {ld!r}")
        cps_in = pr.get("checkpoints", {})
        _expect(isinstance(cps_in, dict), f"{kp}.checkpoints: must be an object")
        cps = {}
        for cp, spec in cps_in.items():
            ckp = f"{kp}.checkpoints.{cp}"
            _expect(cp in ROLES, f"{ckp}: unknown checkpoint; valid: {sorted(ROLES)}")
            _expect(isinstance(spec, dict), f"{ckp}: must be an object")
            bad = sorted(set(spec) - {"seats", "required", "min_seats"})
            if bad:
                raise ConfigError(f"{ckp}.{bad[0]}: unknown key")
            cs = spec.get("seats", [])
            _expect(isinstance(cs, list) and cs, f"{ckp}.seats: required non-empty list")
            for s in cs:
                _expect(s in seats, f"{ckp}.seats: unknown seat {s!r}")
            req = spec.get("required", [])
            _expect(isinstance(req, list), f"{ckp}.required: must be a list")
            for s in req:
                _expect(s in cs, f"{ckp}.required: {s!r} is not in this checkpoint's seats")
            mn = spec.get("min_seats", min(2, len(cs)))
            _expect(_is_int(mn) and 1 <= mn <= len(cs), f"{ckp}.min_seats: must be between 1 and {len(cs)}")
            cps[cp] = {"seats": cs, "required": req, "min_seats": mn}
        depth = pr.get("depth", "standard")
        _expect(depth in DEPTHS, f"{kp}.depth: must be one of {sorted(DEPTHS)}")
        router = pr.get("router", "off")
        _expect(router in ROUTER_MODES, f"{kp}.router: must be one of {sorted(ROUTER_MODES)}")
        out[name] = {"lead_order": order, "checkpoints": cps, "depth": depth, "router": router}
    return out


def _validate_defaults(obj, key, defaults):
    vals = dict(defaults)
    given = obj.get(key, {})
    _expect(isinstance(given, dict), f"{key}: must be an object")
    for k, v in given.items():
        _expect(k in defaults, f"{key}.{k}: unknown key; valid: {sorted(defaults)}")
        _expect(_is_int(v) and v >= 0, f"{key}.{k}: must be a non-negative integer")
        vals[k] = v
    return vals


def load_config(path=None):
    p = config_path(path)
    try:
        raw = Path(p).read_text()
    except OSError as e:
        raise ConfigError(f"{p}: {e.strerror or e}") from e
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ConfigError(f"{p}: invalid JSON at line {e.lineno} column {e.colno}: {e.msg}") from e
    _expect(isinstance(obj, dict), "top level must be an object")
    version = obj.get("schema_version", 1)
    _expect(_is_int(version) and version in (1, 2), f"schema_version: unsupported {version!r} (this version reads 1 and 2)")
    unknown = sorted(set(obj) - TOP_KEYS - (V2_TOP_KEYS if version >= 2 else set()))
    if unknown:
        raise ConfigError(f"{unknown[0]}: unknown key" + (" (needs schema_version 2)" if unknown[0] in V2_TOP_KEYS else ""))
    seats_in = obj.get("seats")
    _expect(isinstance(seats_in, dict) and seats_in, "seats: required non-empty object")
    seats = {name: _validate_seat(name, s, version) for name, s in seats_in.items()}
    plans = _validate_plans(obj)
    for name, s in seats.items():
        if s["plan"] is not None:
            _expect(s["plan"] in plans, f"seats.{name}.plan: must name a plan, got {s['plan']!r}")
        if s["fallback"] is not None:
            _expect(s["fallback"] in seats and s["fallback"] != name, f"seats.{name}.fallback: must name another seat")
    tools = _validate_tools(obj)
    leads = _validate_leads(obj, plans)
    profiles = _validate_profiles(obj, leads, seats)
    if not profiles:
        profiles = {"default": {"lead_order": [], "checkpoints": {}, "depth": "standard", "router": "off"}}
    default_profile = obj.get("default_profile", "default" if "default" in profiles else sorted(profiles)[0])
    _expect(default_profile in profiles, f"default_profile: unknown profile {default_profile!r}")
    projects = obj.get("projects", {})
    _expect(isinstance(projects, dict), "projects: must be an object")
    for path_key, prof in projects.items():
        _expect(prof in profiles, f"projects.{path_key}: unknown profile {prof!r}")
    rotation = _validate_defaults(obj, "rotation", ROTATION_DEFAULTS)
    updates = _validate_defaults(obj, "updates", UPDATES_DEFAULTS)
    cockpit = obj.get("cockpit", {})
    _expect(isinstance(cockpit, dict), "cockpit: must be an object")
    for k in ("state_dir", "metrics_log", "commands_dir", "bin_dir"):
        if k in obj:
            _expect(isinstance(obj[k], str) and obj[k].strip(), f"{k}: must be a non-empty string path")
    state_dir = _abs(obj.get("state_dir", "~/.local/state/balancewheel"))
    metrics_log = _abs(obj["metrics_log"]) if obj.get("metrics_log") else os.path.join(state_dir, "metrics.jsonl")
    watches = dict(WATCH_DEFAULTS)
    w_in = obj.get("watches", {})
    _expect(isinstance(w_in, dict), "watches: must be an object")
    for k, v in w_in.items():
        _expect(k in WATCH_DEFAULTS, f"watches.{k}: unknown key; valid: {sorted(WATCH_DEFAULTS)}")
        _expect(_is_int(v) and v >= 0, f"watches.{k}: must be a non-negative integer")
        watches[k] = v
    _expect(watches["redundant_min_panels"] >= 1, "watches.redundant_min_panels: must be at least 1")
    return {
        "schema_version": version,
        "plans": plans,
        "tools": tools,
        "leads": leads,
        "profiles": profiles,
        "projects": {_abs(k): v for k, v in projects.items()},
        "default_profile": default_profile,
        "rotation": rotation,
        "updates": updates,
        "cockpit": cockpit,
        "path": p,
        "seats": seats,
        "state_dir": state_dir,
        "metrics_log": metrics_log,
        "commands_dir": _abs(obj.get("commands_dir", "~/.claude/commands")),
        "bin_dir": _abs(obj.get("bin_dir", "~/.local/bin")),
        "watches": watches,
    }


if __name__ == "__main__":
    import sys
    try:
        print(json.dumps(load_config(sys.argv[1] if len(sys.argv) > 1 else None), indent=2))
    except ConfigError as e:
        sys.exit(f"config error: {e}")
