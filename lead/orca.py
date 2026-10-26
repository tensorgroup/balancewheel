"""Orca: is it running, are its settings ones bw can live with, and what quota it has seen.

bw reads Orca and never writes it: presence through `orca status --json`, the version from
the app bundle's Info.plist, the active profile's settings through a sealed copy of its
profile database (`lead.sqlite_ro`), and per-provider rate limits from
`orca account list --json` (`snapshot`, `refresh`). Every missing or malformed piece is
`unknown` with a reason, never an exception. An Orca reading never creates or extends a cap.
"""
import json
import os
import plistlib
import re

from lead import proc, quota, sqlite_ro
from lead.paths import read_json, write_json_atomic
from lead.checks import Check

# Read from Orca 1.4.240's app bundle and a copy of its profile database, 2026-10-25.
# `agentPermissionDefault` takes one of own / ask / accept-edits / auto / full-access.
# "Manual" in Agents → Agent Permissions is the state with no permission flag added: Orca's
# onboarding writes "own" for it; "ask" ("Ask for approval") asks before edits and commands
# too. Orca's onboarding counts "full-access" and "auto" ("Approve for me") as its yolo
# choice, so both warn. "accept-edits" approves file edits unasked: unknown, named.
MANUAL_VALUES = {"own", "ask"}
FULL_ACCESS_VALUES = {"full-access", "auto"}
SETTINGS_TABLE = "profile_state_documents"
SETTINGS_KEY_COLUMN = "domain"
SETTINGS_PAYLOAD_COLUMN = "payload"
SETTINGS_DOMAIN = "settings"

FULL_ACCESS_WARNING = "Orca starts agents with full access; set Agents → Agent Permissions → Manual"
_PROFILE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def support_dir():
    return os.environ.get("BW_ORCA_SUPPORT") or os.path.expanduser("~/Library/Application Support/orca")


def running(run=proc.run):
    """(True | False | None, reason) from `orca status --json`."""
    r = run(["orca", "status", "--json"], 5)
    if r.timed_out:
        return None, "orca status timed out"
    if r.rc == 127:
        return None, "orca CLI not found"
    try:
        doc = json.loads(r.out)
    except ValueError:
        return None, f"orca status: not JSON (exit {r.rc})"
    result = doc.get("result") if isinstance(doc, dict) else None
    app = result.get("app") if isinstance(result, dict) else None
    flag = app.get("running") if isinstance(app, dict) else None
    if flag is True:
        return True, "running"
    if flag is False:
        return False, "not running"
    return None, "orca status: no result.app.running"


def app_version(app_path="/Applications/Orca.app"):
    try:
        with open(os.path.join(app_path, "Contents", "Info.plist"), "rb") as f:
            v = plistlib.load(f).get("CFBundleShortVersionString")
    except (OSError, ValueError, plistlib.InvalidFileException, AttributeError):
        return "unreadable"
    return v if isinstance(v, str) and v else "unreadable"


def _settings_row(conn):
    return conn.execute(f"SELECT {SETTINGS_PAYLOAD_COLUMN} FROM {SETTINGS_TABLE} WHERE {SETTINGS_KEY_COLUMN} = ?",
                        (SETTINGS_DOMAIN,)).fetchone()


def read_settings(state, d=None):
    """(settings dict, "") or (None, reason)."""
    d = d or support_dir()
    try:
        with open(os.path.join(d, "orca-profile-index.json")) as f:
            index = json.load(f)
    except FileNotFoundError:
        return None, "no orca-profile-index.json"
    except (OSError, ValueError) as e:
        return None, f"orca-profile-index.json unreadable ({type(e).__name__})"
    pid = index.get("activeProfileId") if isinstance(index, dict) else None
    if not isinstance(pid, str) or not _PROFILE_ID.fullmatch(pid):
        return None, "orca-profile-index.json: activeProfileId missing or unusable"
    db = os.path.join(d, "profiles", pid, "profile-state.db")
    if not os.path.lexists(db):
        return None, "active profile has no profile-state.db"
    row, why = sqlite_ro.query(db, _settings_row, state)
    if why:
        return None, why
    if row is None:
        return None, f"no {SETTINGS_DOMAIN!r} row in {SETTINGS_TABLE}"
    try:
        settings = json.loads(row[0]) if isinstance(row[0], str) else None
    except ValueError:
        settings = None
    if not isinstance(settings, dict):
        return None, "settings payload is not a JSON object"
    return settings, ""


def _shown(v):
    return json.dumps(v)[:60]


def _permission(settings):
    v = settings.get("agentPermissionDefault")
    if isinstance(v, str) and v in MANUAL_VALUES:
        return Check("orca agent permissions", "ok", f"agentPermissionDefault is {_shown(v)}")
    if isinstance(v, str) and v in FULL_ACCESS_VALUES:
        return Check("orca agent permissions", "warn", f"{FULL_ACCESS_WARNING} (agentPermissionDefault is {_shown(v)})")
    return Check("orca agent permissions", "unknown", f"agentPermissionDefault is {_shown(v)}; not a value bw knows")


def _telemetry(settings):
    t = settings.get("telemetry")
    if isinstance(t, dict) and t.get("optedIn") is True:
        return Check("orca telemetry", "warn", "Orca telemetry is on (telemetry.optedIn)")
    return Check("orca telemetry", "ok", "telemetry not opted in")


def _managed_accounts(settings):
    n = 0
    for key in ("claudeManagedAccounts", "codexManagedAccounts"):
        v = settings.get(key, [])
        if not isinstance(v, list):
            return Check("orca managed accounts", "unknown", f"{key} is not a list")
        n += len(v)
    if n:
        return Check("orca managed accounts", "warn",
                     f"Orca manages {n} extra accounts; switching accounts under a running lead makes the logged plan wrong")
    return Check("orca managed accounts", "ok", "no managed accounts")


_AGENT_NAME = re.compile(r"[A-Za-z0-9._-]{1,64}")


def _overrides(settings):
    """One warn per agent whose own permission override is a full-access value."""
    ov = settings.get("agentPermissionOverrides", {})
    if not isinstance(ov, dict):
        return [Check("orca permission overrides", "unknown", "agentPermissionOverrides is not an object")]
    out = []
    for agent, v in sorted(ov.items()):
        if isinstance(v, str) and v in FULL_ACCESS_VALUES:
            name = agent if _AGENT_NAME.fullmatch(agent) else _shown(agent)
            out.append(Check(f"orca override {name}", "warn",
                             f"Orca starts {name} with full access (override); set it to Manual"))
    return out


def check_settings(settings, reason):
    if settings is None:
        return [Check("orca settings", "unknown", reason)]
    hooks = "on" if settings.get("agentStatusHooksEnabled") is True else "off"
    return [_permission(settings), *_overrides(settings), _telemetry(settings), _managed_accounts(settings),
            Check("orca status hooks", "ok", f"agent status hooks {hooks}")]


# `orca account list --json` (read from Orca 1.4.240, 2026-10-25): result.rateLimits.<provider> is
# one dict per provider, next to non-dict siblings (`*Configured` booleans, account lists).
ACCOUNT_LIST_TIMEOUT_S = 10
ACCOUNT_WINDOWS = ("session", "weekly")
RESET_PAST_S = 86400
RESET_FUTURE_S = 400 * 86400
_TYPE_NAMES = {type(None): "null", bool: "bool", int: "int", float: "float", str: "str", list: "list", dict: "dict"}


def account_list(run=proc.run):
    """(payload, "") from `orca account list --json`, or (None, reason)."""
    r = run(["orca", "account", "list", "--json"], ACCOUNT_LIST_TIMEOUT_S)
    if r.timed_out:
        return None, "orca account list timed out"
    if r.rc == 127:
        return None, "orca not installed"
    try:
        doc = json.loads(r.out)
    except ValueError:
        return None, f"orca account list: not JSON (exit {r.rc})"
    if not isinstance(doc, dict) or doc.get("ok") is not True:
        return None, f"orca account list: ok is not true (exit {r.rc})"
    return doc, ""


def _type(v):
    return _TYPE_NAMES.get(type(v), type(v).__name__)


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _expect(v, ok, want, path):
    return None if ok(v) else f"{path}: expected {want}, got {_type(v)}"


def _window_shape(w, path):
    """None when a window dict has the fields bw reads, else the first bad path."""
    if not isinstance(w, dict):
        return f"{path}: expected dict, got {_type(w)}"
    for key, ok, want in (("usedPercent", _is_num, "number"), ("windowMinutes", _is_int, "int"),
                          ("resetsAt", _is_int, "int")):
        if key not in w:
            return f"{path}.{key}: missing"
        bad = _expect(w[key], ok, want, f"{path}.{key}")
        if bad:
            return bad
    return None


def _rate_limits(payload):
    result = payload.get("result") if isinstance(payload, dict) else None
    return result.get("rateLimits") if isinstance(result, dict) else None


def shape_ok(payload, provider):
    """(True, "") when one provider's entry has the shape bw reads, else (False, first bad path).
    Other providers and the non-dict siblings next to them are not inspected."""
    limits = _rate_limits(payload)
    if not isinstance(limits, dict):
        return False, f"rateLimits: expected dict, got {_type(limits)}"
    path = f"rateLimits.{provider}"
    if provider not in limits:
        return False, f"{path}: missing"
    entry = limits[provider]
    if not isinstance(entry, dict):
        return False, f"{path}: expected dict, got {_type(entry)}"
    checks = (("provider", lambda v: isinstance(v, str), "str"), ("updatedAt", _is_int, "int"),
              ("status", lambda v: isinstance(v, str), "str"),
              ("error", lambda v: v is None or isinstance(v, str), "str or null"))
    for key, ok, want in checks:
        if key not in entry:
            return False, f"{path}.{key}: missing"
        bad = _expect(entry[key], ok, want, f"{path}.{key}")
        if bad:
            return False, bad
    for key in ACCOUNT_WINDOWS:
        if key not in entry:
            return False, f"{path}.{key}: missing"
        if entry[key] is not None:
            bad = _window_shape(entry[key], f"{path}.{key}")
            if bad:
                return False, bad
    return True, ""


def _printable(text, limit=200):
    return "".join(c if c.isprintable() else "?" for c in str(text))[:limit]


def _seconds(ms):
    """Epoch milliseconds as float seconds; None for an int too large for a float."""
    try:
        return ms / 1000
    except OverflowError:
        return None


def _shown_num(v):
    return _printable(repr(v), 40)


def _window(w, name, path, now):
    """({"name", "used_pct", "resets_at"}, "") or (None, reason naming the field)."""
    used, reset = w["usedPercent"], w["resetsAt"]
    if not 0 <= used <= 100:  # NaN and huge ints fail the comparison; no float() to overflow
        return None, f"{path}.usedPercent: {_shown_num(used)} is not a percentage in 0–100"
    resets_at = _seconds(reset)
    if resets_at is None or not (now - RESET_PAST_S < resets_at < now + RESET_FUTURE_S):
        return None, f"{path}.resetsAt: {_shown_num(reset)} is not epoch milliseconds near now"
    return {"name": name, "used_pct": float(used), "resets_at": float(resets_at)}, ""


def _pool_source(entry, wid):
    if wid.startswith("bucket:"):
        buckets = entry.get("buckets")
        for i, b in enumerate(buckets if isinstance(buckets, list) else []):
            if isinstance(b, dict) and b.get("name") == wid[len("bucket:"):]:
                return b, f"buckets[{i}]"
        return None, None
    return entry.get(wid), wid


def snapshot(payload, plan_cfg, now, probe_every_min, version):
    """One plan's Orca snapshot: account-wide windows (`session`, `weekly`), pools for the windows
    `orca_windows` maps, or unknown with the reason. Never raises on a missing key."""
    provider = plan_cfg.get("orca_provider")
    base = {"source": "orca", "source_version": version}

    def unknown(reason, **extra):
        return dict(base, unknown=True, reason=reason, **extra)

    limits = _rate_limits(payload)
    if isinstance(limits, dict) and provider not in limits:
        return unknown(f"orca has no {provider} entry")
    ok, bad = shape_ok(payload, provider)
    if not ok:
        return unknown(f"Orca {version} changed shape: {bad}")
    entry, path = limits[provider], f"rateLimits.{provider}"
    if entry["status"] != "ok" or entry["error"]:
        detail = f" — {_printable(entry['error'])}" if entry["error"] else ""
        return unknown(f"orca: {_printable(entry['status'], 40)}{detail}")
    observed = _seconds(entry["updatedAt"])
    if observed is None or observed > now + quota.FUTURE_SLACK_S:
        return unknown("observed in the future")
    if now - observed > 2 * probe_every_min * 60:
        return unknown(f"stale: observed {(now - observed) / 60:.0f} min ago", last_known_at=observed)
    windows = []
    for name in ACCOUNT_WINDOWS:
        if entry[name] is not None:
            w, why = _window(entry[name], name, f"{path}.{name}", now)
            if why:
                return unknown(why)
            windows.append(w)
    if not windows:
        return unknown("no account-wide window")
    pools, notes = [], []
    for wid, names in sorted((plan_cfg.get("orca_windows") or {}).items()):
        src, where = _pool_source(entry, wid)
        if src is None:
            continue
        # A malformed pool is dropped with a note; the plan's account-wide windows still stand.
        bad = _window_shape(src, f"{path}.{where}")
        w, why = (None, bad) if bad else _window(src, wid, f"{path}.{where}", now)
        if why:
            notes.append(f"pool {_printable(wid, 80)} malformed: {why}")
            continue
        pools.append(dict(w, applies_to=list(names)))
    snap = dict(base, windows=windows, pools=pools, observed_at=observed)
    if notes:
        snap["notes"] = notes
    return snap


def _shape_file(state):
    return os.path.join(state, "probes", "orca-shape.json")


def check_version(state, version, shape_results):
    """Record Orca's version and each provider's shape result; on a version change, the one line
    that says what the re-check found. An unreadable version is not recorded."""
    if not isinstance(version, str) or not version or version == "unreadable":
        return None
    path = _shape_file(state)
    prev = read_json(path)
    old = prev.get("version") if isinstance(prev, dict) else None
    record = {"version": version,
              "shape": {p: {"ok": ok, "reason": why} for p, (ok, why) in sorted(shape_results.items())}}
    if record != prev:  # rewritten only when the version or a shape result changed
        write_json_atomic(path, record)
    if not isinstance(old, str) or old == version:
        return None
    parts = [f"{p} ok" if ok else f"{p} failed ({why})" for p, (ok, why) in sorted(shape_results.items())]
    return f"Orca {old} → {version}: shape re-checked: {', '.join(parts) or 'no mapped provider'}"


def refresh(state, config, now, run=proc.run, version=None):
    """Read Orca's local rate-limit cache once and write an Orca snapshot for every plan mapped
    to an Orca provider. Display and freshness only: no cap is created, extended, or cleared.
    Returns the version-change line (or None)."""
    plans = {name: p for name, p in config["plans"].items() if p.get("orca_provider")}
    if not plans:
        return None
    version = version or app_version()
    every = (config.get("rotation") or {}).get("probe_every_min", 15)
    payload, why = account_list(run)
    for name, p in sorted(plans.items()):
        snap = (snapshot(payload, p, now, every, version) if payload is not None
                else {"unknown": True, "reason": why, "source": "orca", "source_version": version})
        quota.write_snapshot(state, name, "orca", snap, now)
    if payload is None:
        return None
    providers = sorted({p["orca_provider"] for p in plans.values()})
    return check_version(state, version, {prov: shape_ok(payload, prov) for prov in providers})
