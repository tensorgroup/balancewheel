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
    "codex": {"low", "medium", "high"},
    "pi": {"off", "minimal", "low", "medium", "high", "xhigh"},
    "kimi": {"low", "high", "max"},
}
SEAT_NAME = re.compile(r"^[a-z0-9-]+$")
TOP_KEYS = {"_comment", "schema_version", "seats", "state_dir", "metrics_log", "commands_dir", "bin_dir", "watches"}
SEAT_KEYS = {"runtime", "model", "effort", "roles", "args", "provider", "policy", "enabled"}
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


def _validate_seat(name, seat):
    kp = f"seats.{name}"
    _expect(SEAT_NAME.match(name), f"{kp}: seat name must match [a-z0-9-]+")
    _expect(isinstance(seat, dict), f"{kp}: must be an object")
    unknown = sorted(set(seat) - SEAT_KEYS)
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
            "args": args, "provider": provider, "policy": policy, "enabled": enabled}


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
    unknown = sorted(set(obj) - TOP_KEYS)
    if unknown:
        raise ConfigError(f"{unknown[0]}: unknown key")
    version = obj.get("schema_version", 1)
    _expect(_is_int(version) and version == 1, f"schema_version: unsupported {version!r} (this version reads 1)")
    seats_in = obj.get("seats")
    _expect(isinstance(seats_in, dict) and seats_in, "seats: required non-empty object")
    seats = {name: _validate_seat(name, s) for name, s in seats_in.items()}
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
        "schema_version": 1,
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
