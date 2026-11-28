"""Live context, tokens, and cost of a seat run, read from the seat CLI's own session file.

`bw live` calls `live_usage` for each open run. It reads the newest session file of that run's
worktree in the CLI's folder: under bw's seat home (`state/seats/homes/<seat>`) for a `bw` run, or
under the real home for a local seat whose config entry names a `runtime`. It is best effort and
read-only: it makes no dir, follows no link, reads at most `TAIL_BYTES` of a file, and a missing
file or a changed vendor format gives `EMPTY` values, never an error.

Event shapes, per runtime (key names only):
- codex: `{"type": "event_msg", "payload": {"type": "token_count", "info": {"last_token_usage",
  "total_token_usage", "model_context_window"}}}` in `sessions/Y/M/D/rollout-*.jsonl`, whose first
  line `{"type": "session_meta", "payload": {"cwd"}}` names the worktree.
- claude-code: `{"type": "assistant", "message": {"id", "usage"}}` in `.claude/projects/<slug>/*.jsonl`;
  one message spans several lines with the same `id` and usage, so each id counts once (its last line).
- pi: `{"type": "message", "message": {"role": "assistant", "usage": {..., "cost": {"total"}}}}` in
  `sessions/--<dir>--/*.jsonl`, whose first line `{"type": "session", "cwd"}` names the worktree.
- kimi: `{"type": "token_counting.measured", "tokens"}` in `<sessionDir>/agents/main/wire.jsonl`;
  `<kimi home>/session_index.jsonl` maps each `sessionDir` to its `workDir` (newest last).
- agy: no known session file; always `EMPTY`.

With no `session_id` (or none that names a file), a session counts only when its file was written at or
after `start - 1` and it also began then: the first record with a `timestamp` in the file's first 64 KiB,
else the file's birth time; with neither, the session does not count.
So the lead's own session in the same folder, begun before the run, is never read.

This module is not hashed into the seat key (seats/verify.py), so a format fix here resets no seat.
"""
import calendar
import json
import math
import os
import re
import stat
import time
from typing import Optional

from seats.adapters import valid_session

TAIL_BYTES = 1024 * 1024
MAX_CANDIDATES = 200
HEAD_BYTES = 64 * 1024
EMPTY = {"ctx": None, "window": None, "ctx_peak": None, "tokens_in": None, "tokens_out": None, "cost_usd": None}


def seat_home_path(state: str, seat: str) -> str:
    """The seat home's path. It makes nothing (unlike verify.safe_home, which runs mkdir and chmod)."""
    return os.path.join(state, "seats", "homes", seat)


def _env_dir(var, default):
    v = os.environ.get(var)
    return v if v else os.path.join(os.path.expanduser("~"), default)


def session_root(runtime: str, home: Optional[str], dir: str) -> Optional[str]:
    """The folder that holds `runtime`'s session files for `dir`; None when the runtime keeps none."""
    if runtime == "codex":
        return os.path.join(home if home else _env_dir("CODEX_HOME", ".codex"), "sessions")
    if runtime == "claude-code":
        base = os.path.join(home, ".claude") if home else _env_dir("CLAUDE_CONFIG_DIR", ".claude")
        return os.path.join(base, "projects", re.sub(r"[^A-Za-z0-9]", "-", dir))
    if runtime == "pi":
        base = home if home else os.path.join(os.path.expanduser("~"), ".pi", "agent")
        return os.path.join(base, "sessions", "--" + dir.lstrip("/").replace("/", "-") + "--")
    if runtime == "kimi":
        return os.path.join(home if home else _env_dir("KIMI_CODE_HOME", ".kimi-code"), "sessions")
    return None


# -- bounded, no-follow reads ----------------------------------------------------------------------

def _read(path, tail):
    """Up to TAIL_BYTES from the end (`tail`) or HEAD_BYTES from the start of a regular file that is not
    a link, as whole lines. None when the file cannot be read that way."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None
        if not tail:
            data = os.read(fd, HEAD_BYTES)
            if len(data) < HEAD_BYTES:  # the whole file
                return data
            return data[:data.rindex(b"\n") + 1] if b"\n" in data else None  # whole lines only
        start = max(0, st.st_size - TAIL_BYTES)
        os.lseek(fd, start, os.SEEK_SET)
        chunks, left = [], st.st_size - start
        while left > 0:
            b = os.read(fd, min(left, TAIL_BYTES))
            if not b:
                break
            chunks.append(b)
            left -= len(b)
        data = b"".join(chunks)
        if start > 0:  # the first line was cut by the read
            data = data.split(b"\n", 1)[1] if b"\n" in data else b""
        return data
    except OSError:
        return None
    finally:
        os.close(fd)


def _json(raw):
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None


def _rows(data):
    for raw in data.split(b"\n"):
        if raw.strip():
            obj = _json(raw)
            if isinstance(obj, dict):
                yield obj


def _head(path):
    """(first row, the rows as they parse) of the whole lines in the first HEAD_BYTES; ({}, []) when none."""
    data = _read(path, tail=False)
    if not data:
        return {}, []
    first = _json(data.split(b"\n", 1)[0])
    return (first if isinstance(first, dict) else {}), _rows(data)


def _int(x):
    return x if isinstance(x, int) and not isinstance(x, bool) else None


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


def _get(obj, *keys):
    for k in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(k)
    return obj


# -- candidates ------------------------------------------------------------------------------------

_ISO = re.compile(r"(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)(\.\d+)?(Z|[+-]\d\d:?\d\d)?")


def _time(x):
    """An epoch from a record's timestamp: an ISO 8601 string (no zone: UTC) or a number (ms when huge)."""
    n = _num(x)
    if n is not None:
        return n / 1000.0 if n > 1e11 else float(n)
    m = _ISO.fullmatch(x) if isinstance(x, str) else None
    if not m:
        return None
    try:
        t = calendar.timegm(tuple(int(g) for g in m.groups()[:6]) + (0, 0, 0))
    except (ValueError, OverflowError):
        return None
    tz = m.group(8)
    if tz and tz != "Z":
        sign = -1 if tz[0] == "-" else 1
        t -= sign * (int(tz[1:3]) * 3600 + int(tz[-2:]) * 60)
    return t + (float(m.group(7)) if m.group(7) else 0.0)


def _listdir(d):
    try:
        return sorted(os.listdir(d))
    except OSError:
        return []


def _jsonl_in(d):
    return [(os.path.join(d, n), n, None) for n in _listdir(d) if n.endswith(".jsonl")]


def _codex_files(root, floor):
    """(path, name, day) of the rollout files under `Y/M/D` folders; with `floor`, only folders of that day
    or later (a folder names the day its sessions began)."""
    out = []
    for y in _listdir(root):
        for m in _listdir(os.path.join(root, y)):
            for d in _listdir(os.path.join(root, y, m)):
                try:
                    day = (int(y), int(m), int(d))
                except ValueError:
                    continue
                if floor is None or day >= floor:
                    out += [(p, n, day) for p, n, _ in _jsonl_in(os.path.join(root, y, m, d))
                            if n.startswith("rollout-")]
    return out


def _inside(path, root):
    try:
        real, base = os.path.realpath(path), os.path.realpath(root)
        return os.path.commonpath([real, base]) == base and real != base
    except ValueError:
        return False


def _kimi_files(root, dir):
    """The wire files of the index entries whose workDir is `dir` (the index sits next to `sessions/`);
    the name is the entry's sessionId."""
    data = _read(os.path.join(os.path.dirname(root), "session_index.jsonl"), tail=True)
    if not data:
        return []
    out = []
    for row in _rows(data):
        sdir, sid = row.get("sessionDir"), row.get("sessionId")
        if row.get("workDir") != dir or not isinstance(sdir, str) or not sdir:
            continue
        sdir = os.path.join(root, sdir)  # a relative sessionDir is taken against the sessions root
        if _inside(sdir, root):
            out.append((os.path.join(sdir, "agents", "main", "wire.jsonl"), sid if isinstance(sid, str) else "", None))
    return out


def _files(runtime, root, dir, floor):
    if runtime == "codex":
        return _codex_files(root, floor)
    if runtime in ("claude-code", "pi"):
        return _jsonl_in(root)
    if runtime == "kimi":
        return _kimi_files(root, dir)
    return []


def _floor_day(start):
    """The earliest `Y/M/D` folder a session begun at `start - 1` or later can sit in, in any time zone."""
    try:
        return time.gmtime(start - 1 - 86400)[:3]
    except (OverflowError, OSError, ValueError):
        return None


def _names_session(runtime, name, sid):
    if runtime == "kimi":
        return name == sid
    stem = name[:-len(".jsonl")]
    return stem == sid or stem.endswith(("-" + sid, "_" + sid))


def _ran_in(runtime, first, dir):
    if runtime == "codex":
        return _get(first, "payload", "cwd") == dir
    if runtime == "pi":
        return first.get("cwd") == dir
    return True  # claude-code: the slug names dir; kimi: the index's workDir did


def _birth(st):
    return getattr(st, "st_birthtime", None)


def _began(rows, st):
    """When the session began: the first head record with a `timestamp` (codex: also `payload.timestamp`),
    else the file's birth time. With neither (Linux has no birth time), -inf: the session never counts as
    new, because the mtime of a file being written says nothing about when its session began."""
    for r in rows:
        t = _time(r.get("timestamp"))
        if t is None:
            t = _time(_get(r, "payload", "timestamp"))
        if t is not None:
            return t
    b = _num(_birth(st))
    return b if b else -math.inf


def _lstat_reg(path):
    try:
        st = os.lstat(path)
    except OSError:
        return None
    return st if stat.S_ISREG(st.st_mode) else None


def _pick(runtime, root, dir, session_id, start):
    """The file named by `session_id` when one holds it (and ran in `dir`). Else the newest file written
    at or after `start - 1` whose session also began then (so an older session in the same folder, such as
    the lead's own, never counts), checked newest first; files older than that are never opened."""
    by_id = valid_session(session_id)
    files = _files(runtime, root, dir, None if by_id else _floor_day(start))
    if by_id:
        for path, name, _ in files:
            if _names_session(runtime, name, session_id) and _lstat_reg(path) and \
                    _ran_in(runtime, _head(path)[0], dir):
                return path
        floor = _floor_day(start)
        files = [f for f in files if f[2] is None or floor is None or f[2] >= floor]
    recent = []
    for path, _, _ in files:
        st = _lstat_reg(path)
        if st is not None and st.st_mtime >= start - 1:
            recent.append((st.st_mtime, path, st))
    recent.sort(key=lambda x: x[0], reverse=True)
    for _, path, st in recent[:MAX_CANDIDATES]:
        first, rows = _head(path)
        if _ran_in(runtime, first, dir) and _began(rows, st) >= start - 1:
            return path
    return None


# -- usage per runtime -----------------------------------------------------------------------------

def _usage(runtime, rows, seat_cfg):
    out = dict(EMPTY)
    seen = False
    peak = None
    cost_sum = None
    msgs = {}  # claude-code: the last (ctx, output_tokens) per message.id; a line with no id stands alone
    for n, r in enumerate(rows):
        ctx = None
        if runtime == "codex":
            info = _get(r, "payload", "info") if r.get("type") == "event_msg" and \
                _get(r, "payload", "type") == "token_count" else None
            ctx = _int(_get(info, "last_token_usage", "input_tokens"))
            if ctx is None:
                continue
            out["window"] = _int(_get(info, "model_context_window"))
            out["tokens_in"] = _int(_get(info, "total_token_usage", "input_tokens"))
            out["tokens_out"] = _int(_get(info, "total_token_usage", "output_tokens"))
        elif runtime == "claude-code":
            u = _get(r, "message", "usage") if r.get("type") == "assistant" else None
            parts = [_int(_get(u, k)) for k in
                     ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")]
            if not isinstance(u, dict) or parts[0] is None:
                continue
            ctx = sum(p or 0 for p in parts)
            mid = _get(r, "message", "id")
            # one message is written over several lines that repeat its usage: count each id once
            msgs[mid if isinstance(mid, str) and mid else n] = (ctx, _int(u.get("output_tokens")))
        elif runtime == "pi":
            m = r.get("message") if r.get("type") == "message" else None
            u = _get(m, "usage") if _get(m, "role") == "assistant" else None
            parts = [_int(_get(u, k)) for k in ("input", "cacheRead", "cacheWrite")]
            if not isinstance(u, dict) or parts[0] is None:
                continue
            ctx = sum(p or 0 for p in parts)
            c = _num(_get(u, "cost", "total"))
            if c is not None:
                cost_sum = (cost_sum or 0) + c
        elif runtime == "kimi":
            ctx = _int(r.get("tokens")) if r.get("type") == "token_counting.measured" else None
            if ctx is None:
                continue
        seen = True
        out["ctx"] = ctx
        peak = ctx if peak is None else max(peak, ctx)
    if not seen:
        return dict(EMPTY)
    out["ctx_peak"] = peak
    if runtime == "claude-code":
        outs = [o for _, o in msgs.values() if o is not None]
        out["tokens_out"] = sum(outs) if outs else None
        out["ctx_peak"] = max(c for c, _ in msgs.values())
    if runtime == "pi":
        out["cost_usd"] = cost_sum
        out["window"] = _int(_get(seat_cfg, "provider", "contextWindow"))
    return out


def live_usage(runtime: str, home: Optional[str], session_id: Optional[str], dir: str, start: float,
               seat_cfg: Optional[dict] = None) -> dict:
    """The keys of EMPTY for `runtime`'s session of `dir`: the file named by `session_id` when one holds
    it, else the newest with mtime >= start - 1. Never raises; anything unreadable gives EMPTY values."""
    try:
        root = session_root(runtime, home, dir) if isinstance(dir, str) else None
        if root is None:
            return dict(EMPTY)
        path = _pick(runtime, root, dir, session_id, start)
        data = _read(path, tail=True) if path else None
        return _usage(runtime, _rows(data), seat_cfg) if data else dict(EMPTY)
    except Exception:  # best effort: a vendor format change must never break bw live
        return dict(EMPTY)
