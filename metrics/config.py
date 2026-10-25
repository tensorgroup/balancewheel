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
V3_TOP_KEYS = {"guard"}
V3_LEAD_KEYS = {"autonomy"}
V3_PROFILE_KEYS = {"protected_branches", "deploy_commands"}
GUARD_KEYS = {"mode", "model", "on_judge_failure", "rules_on_native"}
GUARD_MODES = {"shadow", "acting"}
JUDGE_FAILURES = {"allow", "ask"}
SCHEMA_VERSIONS = (1, 2, 3)
AUTONOMY = {"native", "guard", "bypass", "manual"}
SUPPORTED_AUTONOMY = {
    "claude-code": {"native", "bypass", "manual"},
    "codex": {"native", "manual"},
    "agy": {"guard", "bypass", "manual"},
    "pi": {"guard", "bypass"},
    "kimi": {"native", "bypass", "manual"},
}
# What a lead gets when a schema-3 file does not say.
DEFAULT_AUTONOMY = {"claude-code": "native", "codex": "native", "agy": "guard", "pi": "guard", "kimi": "manual"}
# What a schema-2 lead effectively runs with today (no flags are added to its argv).
LEGACY_AUTONOMY = {"claude-code": "native", "codex": "manual", "agy": "manual", "pi": "bypass", "kimi": "manual"}
# Flags that set a runtime's permission posture; bw owns them under schema 3.
PERMISSION_ARGS = {
    "claude-code": ["--permission-mode", "--dangerously-skip-permissions", "--allow-dangerously-skip-permissions",
                    "--permission-prompts", "--permission-prompt-tool"],
    "codex": ["--approve-for-me", "-a", "--ask-for-approval", "--full-auto",
              "--dangerously-bypass-approvals-and-sandbox", "--sandbox", "-s"],
    "agy": ["--dangerously-skip-permissions", "--sandbox"],
    "pi": [],
    "kimi": ["-y", "--yolo", "--auto"],
}
DEFAULT_PROTECTED_BRANCHES = ["main", "master"]
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


def _has_permission_arg(runtime, args):
    for a in args:
        for flag in PERMISSION_ARGS[runtime]:
            if a == flag or a.startswith(flag + "="):
                return a
    return None


def _validate_leads(obj, plans, version=2):
    leads = obj.get("leads", {})
    _expect(isinstance(leads, dict), "leads: must be an object")
    out = {}
    for name, ld in leads.items():
        kp = f"leads.{name}"
        _name_ok(kp, name)
        _expect(isinstance(ld, dict), f"{kp}: must be an object")
        bad = sorted(set(ld) - {"runtime", "model", "plan", "args"} - (V3_LEAD_KEYS if version >= 3 else set()))
        if bad:
            raise ConfigError(f"{kp}.{bad[0]}: unknown key" + (" (needs schema_version 3)" if bad[0] in V3_LEAD_KEYS else ""))
        _expect(ld.get("runtime") in RUNTIMES, f"{kp}.runtime: must be one of {sorted(RUNTIMES)}, got {ld.get('runtime')!r}")
        _expect(isinstance(ld.get("model"), str) and ld["model"], f"{kp}.model: required string")
        _expect(ld.get("plan") in plans, f"{kp}.plan: must name a plan, got {ld.get('plan')!r}")
        args = ld.get("args", [])
        _expect(isinstance(args, list) and all(isinstance(a, str) for a in args), f"{kp}.args: must be a list of strings")
        rt = ld["runtime"]
        if version >= 3:
            if "autonomy" in ld:
                autonomy, source = ld["autonomy"], "config"
                _expect(autonomy in AUTONOMY, f"{kp}.autonomy: must be one of {sorted(AUTONOMY)}, got {autonomy!r}")
                _expect(autonomy in SUPPORTED_AUTONOMY[rt],
                        f"{kp}.autonomy: {autonomy} is not supported for {rt}; valid: {sorted(SUPPORTED_AUTONOMY[rt])}")
            else:
                autonomy, source = DEFAULT_AUTONOMY[rt], "default"
            owned = _has_permission_arg(rt, args)
            _expect(owned is None, f"{kp}.args: {owned!r} sets the permission posture, which autonomy owns under schema 3; remove it")
        else:
            autonomy = "custom" if _has_permission_arg(rt, args) else LEGACY_AUTONOMY[rt]
            source = "legacy"
        out[name] = {"runtime": rt, "model": ld["model"], "plan": ld["plan"], "args": args,
                     "autonomy": autonomy, "autonomy_source": source}
    return out


def _string_list(kp, value):
    _expect(isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value),
            f"{kp}: must be a list of non-empty strings")
    return list(value)


def _validate_profiles(obj, leads, seats, version=2):
    profiles = obj.get("profiles", {})
    _expect(isinstance(profiles, dict), "profiles: must be an object")
    out = {}
    for name, pr in profiles.items():
        kp = f"profiles.{name}"
        _name_ok(kp, name)
        _expect(isinstance(pr, dict), f"{kp}: must be an object")
        bad = sorted(set(pr) - {"lead_order", "checkpoints", "depth", "router"} - (V3_PROFILE_KEYS if version >= 3 else set()))
        if bad:
            raise ConfigError(f"{kp}.{bad[0]}: unknown key" + (" (needs schema_version 3)" if bad[0] in V3_PROFILE_KEYS else ""))
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
        protected = _string_list(f"{kp}.protected_branches", pr.get("protected_branches", DEFAULT_PROTECTED_BRANCHES))
        deploy = _string_list(f"{kp}.deploy_commands", pr.get("deploy_commands", []))
        out[name] = {"lead_order": order, "checkpoints": cps, "depth": depth, "router": router,
                     "protected_branches": protected, "deploy_commands": deploy}
    return out


def _validate_guard(obj, profiles, leads, version):
    given = obj.get("guard", {})
    _expect(isinstance(given, dict), "guard: must be an object")
    bad = sorted(set(given) - GUARD_KEYS)
    if bad:
        raise ConfigError(f"guard.{bad[0]}: unknown key")
    mode = {p: "shadow" for p in profiles}
    m_in = given.get("mode", {})
    _expect(isinstance(m_in, dict), "guard.mode: must be an object")
    for prof, m in m_in.items():
        kp = f"guard.mode.{prof}"
        _expect(prof in profiles, f"{kp}: unknown profile {prof!r}")
        _expect(m in GUARD_MODES, f"{kp}: must be one of {sorted(GUARD_MODES)}, got {m!r}")
        _expect(m != "acting", f"{kp}: acting arrives with build step 4 (needs `bw guard eval`)")
        mode[prof] = m
    model = given.get("model")
    if model is not None:
        _expect(isinstance(model, str) and model.strip(), "guard.model: must be a non-empty string")
        for name, ld in leads.items():
            _expect(not (ld["autonomy"] == "guard" and ld["model"] == model),
                    f"guard.model: must differ from the lead's own model ({model!r}, lead {name!r})")
    failure = given.get("on_judge_failure", "ask")
    _expect(failure in JUDGE_FAILURES, f"guard.on_judge_failure: must be one of {sorted(JUDGE_FAILURES)}, got {failure!r}")
    rules = given.get("rules_on_native", False)
    _expect(isinstance(rules, bool), "guard.rules_on_native: must be true or false")
    _expect(not rules, "guard.rules_on_native: not built yet")
    return {"mode": mode, "model": model, "on_judge_failure": failure, "rules_on_native": False}


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
    _expect(_is_int(version) and version in SCHEMA_VERSIONS,
            f"schema_version: unsupported {version!r} (this version reads 1, 2 and 3)")
    unknown = sorted(set(obj) - TOP_KEYS - (V2_TOP_KEYS if version >= 2 else set()) - (V3_TOP_KEYS if version >= 3 else set()))
    if unknown:
        raise ConfigError(f"{unknown[0]}: unknown key" + (" (needs schema_version 3)" if unknown[0] in V3_TOP_KEYS
                          else " (needs schema_version 2)" if unknown[0] in V2_TOP_KEYS else ""))
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
    leads = _validate_leads(obj, plans, version)
    profiles = _validate_profiles(obj, leads, seats, version)
    if not profiles:
        profiles = {"default": {"lead_order": [], "checkpoints": {}, "depth": "standard", "router": "off",
                                "protected_branches": list(DEFAULT_PROTECTED_BRANCHES), "deploy_commands": []}}
    default_profile = obj.get("default_profile", "default" if "default" in profiles else sorted(profiles)[0])
    _expect(default_profile in profiles, f"default_profile: unknown profile {default_profile!r}")
    projects = obj.get("projects", {})
    _expect(isinstance(projects, dict), "projects: must be an object")
    for path_key, prof in projects.items():
        _expect(prof in profiles, f"projects.{path_key}: unknown profile {prof!r}")
    guard = _validate_guard(obj, profiles, leads, version)
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
        "guard": guard,
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
