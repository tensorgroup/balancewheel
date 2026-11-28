"""The `run` rows: one seat or local run as a start row, beat rows, and one end row.

`bw seat` (seats/run.py) writes a start row just before the seat CLI starts and exactly one end row
after it. `bw run` (lead/run_cli.py) writes the same rows for a local command, with beat rows while it runs.
The rows go through `store.append`, the only appender. Row shapes are in metrics/store.py. When a contained
lead cannot append, `bw run` writes each row as a request file in `<checkpoint dir>/runs/` instead
(`write_request`); the panel service checks each one (`validate_request`) and appends it (`ingest`, from
`panel/service.py` `drain`). `bw run end` and `bw live` read pending files without taking them (`read_requests`).
"""
import json
import math
import os
import re
import secrets
import stat
import time
from typing import Optional

from lead import handoff, inbox
from lead.guard.redact import redact
from lead.guard.review import safe
from metrics import store  # the appender the callers use; tests patch it here
from metrics.config import SEAT_NAME

RUN_ID = re.compile(r"r[0-9a-f]{12}")
LEAD_SESSION = re.compile(r"[0-9a-f]{12}")  # a lead Session.id (lead/runner.py)
LABEL_CHARS = 80
END_LOOKBACK_S = 7 * 86400
REQUEST_DIR = "runs"
REQUEST_LIMIT = 4096
MAX_RUN_REQUESTS = 50
MODEL_CHARS = 120
REASON_CHARS = 200
HOST_CHARS = 255
T_SKEW_S = 86400
REQUEST_NAME = re.compile(r"(r[0-9a-f]{12})\.(start|beat|end)\.json")
PHASES = ("start", "beat", "end")
PID_MAX = 2 ** 31 - 1
NUMBER_MAX = 2 ** 53  # the largest bound for a number field: exact as a float
NAME_CHARS = 80  # a lead-chosen file name, as shown in a problem line


def new_run_id() -> str:
    return "r" + secrets.token_hex(6)


def label(text: str) -> str:
    """The first line of `text`, redacted, every control character escaped, cut to LABEL_CHARS."""
    lines = str(text or "").splitlines()
    first = lines[0].strip() if lines else ""
    return safe(redact(first))[:LABEL_CHARS]


def start_row(seat, model, dir, label, source, *, pid=None, host=None, panel=None, session_id=None,
              stand_in_for=None, t=None) -> dict:
    """A start row with a new run id. An optional field that is None is left out."""
    row = {"type": "run", "phase": "start", "run_id": new_run_id(), "t": time.time() if t is None else t,
           "seat": seat, "model": model, "dir": dir, "label": label, "source": source}
    for k, v in (("pid", pid), ("host", host), ("panel", panel), ("session_id", session_id),
                 ("stand_in_for", stand_in_for)):
        if v is not None:
            row[k] = v
    return row


def end_row(run_id, seat, dir, ok, reason, duration_s, *, tokens_in=None, tokens_out=None, cost_usd=None,
            ctx_peak=None, t=None, source=None) -> dict:
    """An end row. It always holds every field; an unknown one is null. `source` (`bw run` passes "local") is
    added only when given, so `bw live` can name an end whose start is out of reach; with none it reads "bw"."""
    row = {"type": "run", "phase": "end", "run_id": run_id, "t": time.time() if t is None else t,
           "seat": seat, "dir": dir, "ok": ok, "reason": reason, "duration_s": duration_s,
           "tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": cost_usd, "ctx_peak": ctx_peak}
    if source is not None:
        row["source"] = source
    return row


def beat_row(run_id, seat, dir, t=None) -> dict:
    """A beat row (PD-13): `bw run` writes one every `run_max_s / 2` while its command runs."""
    return {"type": "run", "phase": "beat", "run_id": run_id, "t": time.time() if t is None else t,
            "seat": seat, "dir": dir}


def requests_dir(checkpoint_dir: str) -> str:
    """Where a lead's run request files go: `<checkpoint dir>/runs`."""
    return os.path.join(checkpoint_dir, REQUEST_DIR)


def write_request(dir_path: str, row: dict) -> str:
    """Write `row` as `<run_id>.<phase>.json` in `dir_path` (made 0700; its parent must exist), mode 0600,
    through a temp file and a rename, so a reader never sees half a file and a newer beat replaces the older.
    Returns the path; raises OSError."""
    name = f"{row['run_id']}.{row['phase']}"
    try:
        os.mkdir(dir_path, 0o700)
    except FileExistsError:
        pass
    tmp = os.path.join(dir_path, f".tmp-{name}")
    path = os.path.join(dir_path, f"{name}.json")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(row, f, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


# --- request files from contained leads (the panel service appends them) ---

def _number(v) -> bool:
    """A finite JSON number within +/-NUMBER_MAX (a huge JSON integer never reaches float math)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    return -NUMBER_MAX <= v <= NUMBER_MAX if isinstance(v, int) else math.isfinite(v) and abs(v) <= NUMBER_MAX


def _count(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= NUMBER_MAX


def _text(obj, key, limit):
    """An optional string field: None when absent or null; one line within `limit`, then `safe()` and cut
    to `limit` (safe() may lengthen an escape); else ValueError."""
    v = obj.get(key)
    if v is None:
        return None
    if not isinstance(v, str) or v.splitlines() not in ([], [v]) or len(v) > limit:
        raise ValueError(f"{key} must be one line of at most {limit} characters")
    return safe(v)[:limit]


def validate_request(obj, worktree: str, now: float) -> dict:
    """The row to append for one request body (run_id and phase already set from its file name), or
    ValueError. `source` is forced to "local" and `dir` to `worktree`; unknown keys are dropped (so are
    `panel`, `session_id`, and `stand_in_for`, which only `bw seat` writes)."""
    if not isinstance(obj, dict):
        raise ValueError("a run request must be a JSON object")
    phase, run_id = obj.get("phase"), obj.get("run_id")
    if phase not in PHASES:
        raise ValueError("phase must be start, beat, or end")
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise ValueError("run_id must be r and 12 hex digits")
    t = obj.get("t")
    if not _number(t) or abs(t - now) > T_SKEW_S:
        raise ValueError(f"t must be a finite epoch within {T_SKEW_S} s of now")
    seat = obj.get("seat")
    if not (seat is None and phase != "start") and not (isinstance(seat, str) and SEAT_NAME.fullmatch(seat)):
        raise ValueError(f"seat must match {SEAT_NAME.pattern}")
    row = {"type": "run", "phase": phase, "run_id": run_id, "t": t, "seat": seat}
    if phase == "start":
        pid = obj.get("pid")
        if pid is not None and not (isinstance(pid, int) and not isinstance(pid, bool) and 1 <= pid <= PID_MAX):
            raise ValueError(f"pid must be an integer from 1 to {PID_MAX}")
        row.update(model=_text(obj, "model", MODEL_CHARS), dir=worktree, label=_text(obj, "label", LABEL_CHARS),
                   source="local")
        for k, v in (("pid", pid), ("host", _text(obj, "host", HOST_CHARS))):
            if v is not None:
                row[k] = v
        return row
    row["dir"] = worktree
    if phase == "beat":
        return row
    if not isinstance(obj.get("ok"), bool):
        raise ValueError("ok must be true or false")
    for k, good in (("duration_s", lambda v: _number(v) and v >= 0), ("tokens_in", _count), ("tokens_out", _count),
                    ("cost_usd", lambda v: _number(v) and v >= 0), ("ctx_peak", _count)):
        if obj.get(k) is not None and not good(obj[k]):
            raise ValueError(f"{k} must be a non-negative number")
    return end_row(run_id, seat, worktree, obj["ok"], _text(obj, "reason", REASON_CHARS), obj.get("duration_s"),
                  tokens_in=obj.get("tokens_in"), tokens_out=obj.get("tokens_out"), cost_usd=obj.get("cost_usd"),
                  ctx_peak=obj.get("ctx_peak"), t=t, source="local")


def _open_requests(dir_path):
    """The request dir as a descriptor, opened with no link followed at either of its last two levels
    (as panel/channel.py does). None when it or its parent does not exist; OSError for anything else."""
    parent, leaf = os.path.split(os.path.normpath(dir_path))
    try:
        cp = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    try:
        return os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=cp)
    except FileNotFoundError:
        return None
    finally:
        os.close(cp)


def _parse(m, data):
    """The request body with `type`, `run_id`, and `phase` from its name (F1), or ValueError."""
    if data is None:
        return ValueError("not a regular file, or hard-linked")
    if len(data) >= REQUEST_LIMIT:
        return ValueError(f"a run request must be under {REQUEST_LIMIT} bytes")
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return ValueError("a run request must be JSON")
    if not isinstance(obj, dict):
        return ValueError("a run request must be a JSON object")
    run_id, phase = m.group(1), m.group(2)
    if obj.get("run_id", run_id) != run_id or obj.get("phase", phase) != phase:
        return ValueError("the body's run_id or phase differs from the file name")
    return {**obj, "type": "run", "run_id": run_id, "phase": phase}


def read_requests(dir_path: str, limit: int = MAX_RUN_REQUESTS, unlink: bool = True) -> tuple:
    """([(file name, body dict or ValueError)], problems): at most `limit` request files, starts first, then
    beats, then ends (by name within each). Only `REQUEST_NAME` files are read; a link, a FIFO, or a second
    hard link is a ValueError, never followed. With `unlink`, every file taken is unlinked (a refused one
    too); without it nothing on disk changes. A missing dir is ([], []). Never raises."""
    out, problems = [], []
    try:
        dfd = _open_requests(dir_path)
    except (OSError, ValueError) as e:
        return [], [f"the run request dir cannot be read ({type(e).__name__}); local runs from the lead are not tracked"]
    if dfd is None:
        return [], []
    try:
        try:
            found = [(REQUEST_NAME.fullmatch(n), n) for n in os.listdir(dfd)]
        except OSError as e:
            return [], [f"the run request dir cannot be listed ({type(e).__name__})"]
        found = sorted(((m, n) for m, n in found if m), key=lambda mn: (PHASES.index(mn[0].group(2)), mn[1]))
        for m, name in found[:limit]:
            try:
                st = os.stat(name, dir_fd=dfd, follow_symlinks=False)
                if stat.S_ISDIR(st.st_mode):
                    problems.append(f"a directory named like a run request was left in place: {safe(name)[:NAME_CHARS]}")
                    continue
                data = None
                if stat.S_ISREG(st.st_mode) and st.st_nlink == 1:
                    try:
                        data = inbox.read_at(dfd, name, REQUEST_LIMIT, single_link=True)
                    except OSError:
                        data = None  # swapped for a link or a FIFO after the stat
                if unlink:
                    os.unlink(name, dir_fd=dfd)
                out.append((name, _parse(m, data)))
            except OSError as e:
                problems.append(f"a run request was not read: {safe(name)[:NAME_CHARS]} ({type(e).__name__})")
    finally:
        os.close(dfd)
    return out, problems


def _capped(problems) -> list:
    """At most `inbox.NAMES_SHOWN` problem lines, then one line that counts the rest."""
    more = len(problems) - inbox.NAMES_SHOWN
    return problems if more <= 0 else problems[:inbox.NAMES_SHOWN] + [f"and {more} more"]


def _validated(name, obj, worktree, now):
    """(row, None) or (None, problem line) for one item: any fault in one file refuses only that file."""
    shown = safe(name)[:NAME_CHARS]
    if not isinstance(obj, ValueError):
        try:
            return validate_request(obj, worktree, now), None
        except Exception as e:  # ValueError, or a fault the checks missed (OverflowError, TypeError, ...)
            obj = e if isinstance(e, ValueError) else ValueError(f"malformed run request: {type(e).__name__}")
    return None, f"a run request was refused: {shown} ({safe(str(obj))[:REASON_CHARS]})"


def ingest(config: dict, state: str, project, now: Optional[float] = None) -> list:
    """Append the pending run requests of `project`'s checkpoint dir; returns problem lines (capped).
    The log tail is read first: when it has an error or is incomplete, every file stays for the next drain
    (the ownership checks below must not fail open). Then the files are taken (unlinked) and checked one
    by one; a fault in one file refuses only that file. Starts first, then beats, then ends. A start whose
    `run_id` is already logged is a duplicate. A beat or end whose start (logged, or in this batch) has
    `source: "bw"` or another worktree's `dir` is refused. A beat or end without `seat` takes it from its
    start; with no start found it stays null. A failed append loses that row and says so."""
    now = time.time() if now is None else now
    d = requests_dir(handoff.checkpoint_dir(state, project.worktree_key))
    pending, problems = read_requests(d, unlink=False)
    if not pending:
        return _capped(problems)
    log = store.log_path(config)
    tail = store.read_tail(log, now - END_LOOKBACK_S)
    if tail.error is not None or not tail.complete:
        why = safe(tail.error)[:REASON_CHARS] if tail.error else "the read stopped at its cap"
        return [f"run requests wait for the next drain: the metrics log cannot be read in full ({why})"]
    logged = {}  # run_id -> every logged start of it (more than one only when the log was written around bw)
    for e in tail.events:
        if e.get("type") == "run" and e.get("phase") == "start" and isinstance(e.get("run_id"), str):
            logged.setdefault(e["run_id"], []).append(e)
    items, problems = read_requests(d)
    rows = []
    for name, obj in items:
        row, problem = _validated(name, obj, project.worktree, now)
        if problem:
            problems.append(problem)
        else:
            rows.append((safe(name)[:NAME_CHARS], row))
    batch = {}
    for shown, row in sorted(rows, key=lambda r: PHASES.index(r[1]["phase"])):
        rid = row["run_id"]
        starts = logged.get(rid) or ([batch[rid]] if rid in batch else [])
        why = None
        if row["phase"] == "start":
            if starts:
                why = "a start for this run id is already logged"
        elif any(st.get("source") == "bw" for st in starts):
            why = "a bw seat run; bw seat closes it"
        elif any(st.get("dir") != project.worktree for st in starts):
            why = "a run of another worktree"
        if why:
            problems.append(f"a run request was refused: {shown} ({why})")
            continue
        if row["seat"] is None and starts:
            row["seat"] = starts[-1].get("seat")
        try:
            store.append(log, row)
        except OSError as e:
            problems.append(f"a run request was not logged: {shown} ({type(e).__name__})")
            continue
        if row["phase"] == "start":
            batch[rid] = row
    return _capped(problems)
