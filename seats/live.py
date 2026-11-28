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
- claude-code: `{"type": "assistant", "message": {"usage"}}` in `.claude/projects/<slug>/*.jsonl`.
- pi: `{"type": "message", "message": {"role": "assistant", "usage": {..., "cost": {"total"}}}}` in
  `sessions/--<dir>--/*.jsonl`, whose first line `{"type": "session", "cwd"}` names the worktree.
- kimi: `{"type": "token_counting.measured", "tokens"}` in `<sessionDir>/agents/main/wire.jsonl`;
  `<kimi home>/session_index.jsonl` maps each `sessionDir` to its `workDir` (newest last).
- agy: no known session file; always `EMPTY`.

This module is not hashed into the seat key (seats/verify.py), so a format fix here resets no seat.
"""
import json
import math
import os
import re
import stat
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
            return data.split(b"\n", 1)[0] if b"\n" in data else (data if len(data) < HEAD_BYTES else None)
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


def _first_row(path):
    raw = _read(path, tail=False)
    obj = _json(raw) if raw else None
    return obj if isinstance(obj, dict) else {}


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

def _regular_mtime(path):
    try:
        st = os.lstat(path)
    except OSError:
        return None
    return st.st_mtime if stat.S_ISREG(st.st_mode) else None


def _newest(paths):
    """[(mtime, path, name)] of the regular files, newest first, at most MAX_CANDIDATES."""
    out = []
    for path, name in paths:
        m = _regular_mtime(path)
        if m is not None:
            out.append((m, path, name))
    out.sort(key=lambda x: x[0], reverse=True)
    return out[:MAX_CANDIDATES]


def _listdir(d):
    try:
        return sorted(os.listdir(d))
    except OSError:
        return []


def _jsonl_in(d):
    return [(os.path.join(d, n), n) for n in _listdir(d) if n.endswith(".jsonl")]


def _codex_files(root):
    out = []
    for y in _listdir(root):
        for m in _listdir(os.path.join(root, y)):
            for d in _listdir(os.path.join(root, y, m)):
                out += [(p, n) for p, n in _jsonl_in(os.path.join(root, y, m, d)) if n.startswith("rollout-")]
    return out


def _inside(path, root):
    try:
        real, base = os.path.realpath(path), os.path.realpath(root)
        return os.path.commonpath([real, base]) == base and real != base
    except ValueError:
        return False


def _kimi_files(root, dir):
    """The wire files of the index entries whose workDir is `dir` (the index sits next to `sessions/`)."""
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
            out.append((os.path.join(sdir, "agents", "main", "wire.jsonl"), sid if isinstance(sid, str) else ""))
    return out[-MAX_CANDIDATES:]


def _candidates(runtime, root, dir):
    if runtime == "codex":
        return [c for c in _newest(_codex_files(root)) if _get(_first_row(c[1]), "payload", "cwd") == dir]
    if runtime == "claude-code":
        return _newest(_jsonl_in(root))
    if runtime == "pi":
        return [c for c in _newest(_jsonl_in(root)) if _first_row(c[1]).get("cwd") == dir]
    if runtime == "kimi":
        return _newest(_kimi_files(root, dir))
    return []


def _pick(cands, session_id, start):
    if valid_session(session_id):
        for c in cands:
            if session_id in c[2]:
                return c[1]
    for m, path, _ in cands:
        if m >= start - 1:
            return path
    return None


# -- usage per runtime -----------------------------------------------------------------------------

def _usage(runtime, rows, seat_cfg):
    out = dict(EMPTY)
    seen = False
    peak = None
    out_sum = cost_sum = None
    for r in rows:
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
            o = _int(u.get("output_tokens"))
            if o is not None:
                out_sum = (out_sum or 0) + o
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
        out["tokens_out"] = out_sum
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
        path = _pick(_candidates(runtime, root, dir), session_id, start)
        data = _read(path, tail=True) if path else None
        return _usage(runtime, _rows(data), seat_cfg) if data else dict(EMPTY)
    except Exception:  # best effort: a vendor format change must never break bw live
        return dict(EMPTY)
