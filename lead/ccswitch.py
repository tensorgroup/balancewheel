"""cc-switch's settings contract.

bw reads cc-switch's usage database and nothing else of it, so everything that would make
it route, fail over, sync, or update on its own stays off. `write_settings` pre-writes that
contract before cc-switch's first launch and never overwrites a file that differs;
`check_settings` reports drift.
"""
import datetime
import decimal
import json
import os

from lead import sqlite_ro
from lead.checks import Check

REQUIRED_SETTINGS = {
    "enableLocalProxy": False,
    "enableStackMode": False,
    "enableFailoverToggle": False,
    "unifyCodexSessionHistory": False,
    "launchOnStartup": False,
    "checkToolUpdatesOnStartup": False,
    "sessionAutoSyncEnabled": True,
}


def data_dir():
    return os.environ.get("BW_CCSWITCH_DIR") or os.path.expanduser("~/.cc-switch")


def _settings_path(d):
    return os.path.join(d, "settings.json")


def _differences(current):
    """[(key, current, required)] for each required key not at its value (absent: None)."""
    return [(k, current.get(k), v) for k, v in REQUIRED_SETTINGS.items()
            if k not in current or current[k] is not v]


def _read(d):
    """(settings dict, None) or (None, reason)."""
    try:
        with open(_settings_path(d)) as f:
            cur = json.load(f)
    except FileNotFoundError:
        return None, "absent"
    except (OSError, ValueError) as e:
        return None, f"unreadable ({type(e).__name__})"
    if not isinstance(cur, dict):
        return None, "unreadable (not a JSON object)"
    return cur, None


def write_settings(d=None):
    d = d or data_dir()
    path = _settings_path(d)
    if os.path.lexists(path):
        cur, why = _read(d)
        if cur is None:
            return 1, f"refusing to overwrite {path}; {why}"
        diff = _differences(cur)
        if not diff:
            return 0, "already set"
        shown = ", ".join(f"{k}: {json.dumps(c) if c is None else c} → {r}" for k, c, r in diff)
        return 1, f"refusing to overwrite {path}; differs: {shown}"
    os.makedirs(d, mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(REQUIRED_SETTINGS, f, indent=2)
        f.write("\n")
    return 0, f"wrote {path}"


def check_settings(d=None):
    d = d or data_dir()
    if not os.path.isdir(d):
        return [Check("cc-switch", "unknown", "not installed (Usage view off)")]
    cur, why = _read(d)
    if cur is None:
        return [Check("cc-switch settings", "unknown", f"settings.json {why}")]
    warns = [Check(k, "warn", f"{k} is {json.dumps(c)}, should be {json.dumps(r)}") for k, c, r in _differences(cur)]
    return warns or [Check("cc-switch settings", "ok", "")]


# --- usage (read by sealed copy; bw never writes cc-switch's files) ---

LOG_COLUMNS = ("data_source", "app_type", "model", "input_tokens", "output_tokens",
               "cache_read_tokens", "cache_creation_tokens", "total_cost_usd", "created_at", "status_code")
ROLLUP_COLUMNS = ("date", "app_type", "model", "request_count", "success_count", "input_tokens",
                  "output_tokens", "cache_read_tokens", "cache_creation_tokens", "total_cost_usd")
PRICING_COLUMNS = ("model_id", "input_cost_per_million", "output_cost_per_million")

DATA_SOURCE_TOOL = {"session_log": "claude", "codex_session": "codex", "gemini_session": "gemini",
                    "pi_session": "pi", "kimi_session": "kimi", "agy_session": "agy"}


def _db(d):
    return os.path.join(d or data_dir(), "cc-switch.db")


class _Schema(Exception):
    pass


def _require(conn, table, columns):
    have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    if not have:
        raise _Schema(f"table {table} missing")
    missing = [c for c in columns if c not in have]
    if missing:
        raise _Schema(f"{table}.{missing[0]} missing")


def _cost(text):
    """Decimal of a TEXT cost, None when it is not a finite number."""
    try:
        v = decimal.Decimal(str(text).strip())
    except (decimal.InvalidOperation, ValueError):
        return None
    return v if v.is_finite() else None


def _count(v):
    """An int from a stored count (int, integral float, numeric string); None when unreadable."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, (float, str)):
        d = _cost(v)
        if d is not None and d == d.to_integral_value():
            return int(d)
    return None


class _Group:
    FIELDS = (("requests", None), ("errors", None), ("tokens_in", 1), ("tokens_out", 2),
              ("cache_read", 3), ("cache_write", 4))

    def __init__(self, day, tool, model, origin, unmapped):
        self.row = {"day": day, "tool": tool, "model": model, "requests": 0, "errors": 0,
                    "tokens_in": 0, "tokens_out": 0, "cache_read": 0, "cache_write": 0,
                    "origin": origin, "unmapped": unmapped}
        self.cost = decimal.Decimal(0)

    def add(self, requests, errors, tin, tout, cr, cw, cost):
        r = self.row
        for name, v in zip(("requests", "errors", "tokens_in", "tokens_out", "cache_read", "cache_write"),
                           (requests, errors, tin, tout, cr, cw)):
            v = _count(v)
            r[name] = None if (v is None or r[name] is None) else r[name] + v
        c = _cost(cost)
        self.cost = None if (c is None or self.cost is None) else self.cost + c

    def done(self):
        self.row["cost_usd"] = None if self.cost is None else format(self.cost, "f")
        return self.row


def _local_day(epoch):
    """YYYY-MM-DD in local time, None when `epoch` is not a representable timestamp."""
    if isinstance(epoch, bool) or not isinstance(epoch, (int, float)):
        return None
    try:
        return datetime.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d")
    except (ValueError, OSError, OverflowError):
        return None


def _is_local_midnight(epoch):
    t = datetime.datetime.fromtimestamp(epoch)
    return t.time() == datetime.time(0)


def _read_usage(conn, since, until):
    _require(conn, "proxy_request_logs", LOG_COLUMNS)
    _require(conn, "usage_daily_rollups", ROLLUP_COLUMNS)
    groups = {}
    skipped = 0
    sql = f"SELECT {', '.join(LOG_COLUMNS)} FROM proxy_request_logs WHERE data_source != 'proxy' AND created_at >= ?"
    args = [since]
    if until is not None:
        sql += " AND created_at < ?"
        args.append(until)
    for src, _app, model, tin, tout, cr, cw, cost, at, status in conn.execute(sql, args):
        day = _local_day(at)
        if day is None:
            skipped += 1
            continue
        tool = DATA_SOURCE_TOOL.get(src)
        key = (day, tool or src, model, "log")
        g = groups.setdefault(key, _Group(day, key[1], model, "log", tool is None))
        err = 1 if isinstance(status, int) and not isinstance(status, bool) and not 200 <= status <= 299 else 0
        g.add(1, err, tin, tout, cr, cw, cost)
    sql = f"SELECT {', '.join(ROLLUP_COLUMNS)} FROM usage_daily_rollups WHERE date >= ?"
    args = [_local_day(since)]
    if until is not None:
        # Consistent with `created_at < until`: at local midnight the until day holds nothing before it.
        sql += " AND date < ?" if _is_local_midnight(until) else " AND date <= ?"
        args.append(_local_day(until))
    for day, app, model, n, ok, tin, tout, cr, cw, cost in conn.execute(sql, args):
        key = (day, app, model, "rollup")
        g = groups.setdefault(key, _Group(day, app, model, "rollup", False))
        n, ok = _count(n), _count(ok)
        g.add(n, None if n is None or ok is None else max(n - ok, 0), tin, tout, cr, cw, cost)
    return [g.done() for _k, g in sorted(groups.items())], skipped


def usage(state, since, until=None, d=None):
    """Usage groups from cc-switch's database (`since`/`until` epoch seconds; `until` exclusive).

    Session-log rows (every `data_source` but `proxy`) by local day, tool and model, plus the
    daily rollups cc-switch keeps after it prunes the rows. Unknown on any schema change.
    """
    for name, bound in (("since", since), ("until", until)):
        if (bound is not None or name == "since") and _local_day(bound) is None:
            return {"available": False, "reason": f"invalid {name} bound ({bound!r})", "groups": [],
                    "skipped_rows": 0}
    path = _db(d)
    if not os.path.exists(path):
        return {"available": False, "reason": f"cc-switch not installed ({path} absent)", "groups": [], "skipped_rows": 0}

    def fn(conn):
        try:
            return _read_usage(conn, since, until)
        except _Schema as e:
            return e

    out, why = sqlite_ro.query(path, fn, state)
    if why is not None:
        return {"available": False, "reason": why, "groups": [], "skipped_rows": 0}
    if isinstance(out, _Schema):
        return {"available": False, "reason": f"cc-switch schema changed ({out})", "groups": [], "skipped_rows": 0}
    return {"available": True, "reason": "", "groups": out[0], "skipped_rows": out[1]}


def prices(state, d=None):
    """`model_pricing` as {model_id: {input, output}} per million (strings); {} when unavailable."""
    path = _db(d)
    if not os.path.exists(path):
        return {}

    def fn(conn):
        try:
            _require(conn, "model_pricing", PRICING_COLUMNS)
        except _Schema:
            return {}
        return {m: {"input": str(i), "output": str(o)} for m, i, o in
                conn.execute(f"SELECT {', '.join(PRICING_COLUMNS)} FROM model_pricing")}

    out, why = sqlite_ro.query(path, fn, state)
    return out if why is None and out else {}
