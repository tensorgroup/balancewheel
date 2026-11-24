"""state.py — a panel's state on disk: its paths, `panel.json`, `status.json`, and the `panel_run` event.

A contained lead writes request files next to this state, so every path here is a security boundary:
- a panel id is `p` plus 7 hex digits, and a worktree key is one plain path segment; nothing else
  builds a path;
- a panel belongs to one worktree key; a `panel.json` that names another project reads as missing;
- no symlink is followed, in a dir bw makes or in a file bw reads;
- every write is atomic (temp file, then rename), and every read-modify-write holds the worktree's
  panel lock (`state_dir/panels/<key>/.lock`).

`state_dir/panels/<key>/<id>/` is bw's own (`panel.json`, `brief.md`, `results/`, `log.txt`).
`state_dir/inbox-view/<key>/panels/<id>/` is the lead's read-only view (`status.json`, `next.md`,
`seats/`, `asks/`). `<checkpoint dir>/panel/` is where the lead writes its requests.
"""
import json
import os
import re
import secrets
import stat
import time
from typing import Callable, Optional

from lead import handoff, paths
import store

STATES = ("queued", "running", "ready", "logged", "failed")
OPEN = ("queued", "running", "ready")
MAX_OPEN = 3
MAX_ASKS = 20
PANEL_ID = re.compile(r"p[0-9a-f]{7}")
KEY = re.compile(r"[A-Za-z0-9_-]{1,64}")
ASK_OPEN = ("queued", "running")
MAX_PANEL_BYTES = 4 * 1024 * 1024
LOGGED_MARK = "logged"  # an empty file in a logged panel's work dir
# The panel.json fields a caller may set at create; the others are bw's and start empty.
INPUT_KEYS = ("worktree", "profile", "checkpoint", "target", "focus", "stakes", "paths", "tier", "roster",
              "originals", "fallbacks", "created", "session_id", "lead", "seats")


def check_key(key) -> str:
    """The worktree key when it is one plain path segment; ValueError otherwise."""
    if not isinstance(key, str) or not KEY.fullmatch(key):
        raise ValueError(f"bad worktree key: {key!r}")
    return key


_key = check_key


def _pid(pid) -> str:
    if not isinstance(pid, str) or not PANEL_ID.fullmatch(pid):
        raise ValueError(f"bad panel id: {pid!r}")
    return pid


REQUEST_LEAF = "panel"


def request_dir_parts(state, key) -> tuple:
    """(the checkpoint dir, the leaf name): the request dir is the leaf in the checkpoint dir. A caller
    that opens it one level at a time (no link followed) uses these parts, never its own path."""
    return handoff.checkpoint_dir(state, _key(key)), REQUEST_LEAF


def request_dir(state, key) -> str:
    """`$BW_PANEL`: the lead writes its requests here (inside its write grant)."""
    return os.path.join(*request_dir_parts(state, key))


def view_root(state, key) -> str:
    """`$BW_PANEL_VIEW`: the lead's read-only view of every panel of this worktree."""
    return os.path.join(state, "inbox-view", _key(key), "panels")


def view_dir(state, key, pid) -> str:
    return os.path.join(view_root(state, key), _pid(pid))


def work_dir(state, key, pid) -> str:
    return os.path.join(_work_root(state, key), _pid(pid))


def _work_root(state, key) -> str:
    return os.path.join(state, "panels", _key(key))


def ensure_dir(state, path) -> str:
    """Make `path` (a dir under `state`) one component at a time, each new one 0700.
    An existing component that is a symlink or not a dir raises OSError: no link is followed."""
    rel = os.path.relpath(path, state)
    if rel.startswith(os.pardir):
        raise OSError(f"not under the state dir: {path}")
    os.makedirs(state, mode=0o700, exist_ok=True)
    cur = state
    for part in [] if rel == os.curdir else rel.split(os.sep):
        cur = os.path.join(cur, part)
        try:
            os.mkdir(cur, 0o700)
            os.chmod(cur, 0o700)
        except FileExistsError:
            pass
        st = os.lstat(cur)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise OSError(f"not a plain dir: {cur}")
    return path


_ensure = ensure_dir


def _no_links(state, path) -> bool:
    """True when no component of `path` below `state` is a symlink (missing components are fine)."""
    cur = state
    for part in os.path.relpath(path, state).split(os.sep):
        cur = os.path.join(cur, part)
        try:
            if stat.S_ISLNK(os.lstat(cur).st_mode):
                return False
        except FileNotFoundError:
            return True
    return True


def _lock(state, key):
    return paths.locked_dir(_ensure(state, _work_root(state, key)))


def new_id(state, key, taken=None) -> str:
    """`p` + 7 hex digits, unused in this worktree and, when `taken` is given, not in `taken` (the ids
    of the metrics log, which every worktree shares). `create` takes the id by making its dir, under
    the worktree lock, so two creates never share one."""
    while True:
        pid = "p" + secrets.token_hex(4)[:7]
        if not os.path.lexists(work_dir(state, key, pid)) and pid not in (taken or ()):
            return pid


def _read(state, key, pid) -> Optional[dict]:
    """The panel, or None: bad id or key, a link anywhere, not a regular file, too big, not JSON,
    not an object, or a panel of another worktree."""
    try:
        d = work_dir(state, key, pid)
    except ValueError:
        return None
    f = os.path.join(d, "panel.json")
    if not _no_links(state, f):
        return None
    try:
        fd = os.open(f, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_PANEL_BYTES:
            return None
        with os.fdopen(fd, "rb") as fh:
            fd = None
            panel = json.loads(fh.read(MAX_PANEL_BYTES + 1).decode("utf-8"))
    except (OSError, ValueError):
        return None
    finally:
        if fd is not None:
            os.close(fd)
    if not isinstance(panel, dict) or panel.get("project") != key or panel.get("id") != pid:
        return None
    return panel


def load(state, key, pid) -> Optional[dict]:
    return _read(state, key, pid)


def _write(state, key, panel) -> None:
    pid = panel["id"]
    _ensure(state, work_dir(state, key, pid))
    view = _ensure(state, view_dir(state, key, pid))
    _ensure(state, os.path.join(view, "seats"))
    _ensure(state, os.path.join(view, "asks"))
    paths.write_json_atomic(os.path.join(work_dir(state, key, pid), "panel.json"), panel)
    paths.write_json_atomic(os.path.join(view, "status.json"), status_view(panel, view))
    if panel.get("state") == "logged":  # a scan skips a logged panel without reading it
        mark = os.path.join(work_dir(state, key, pid), LOGGED_MARK)
        if not os.path.lexists(mark):
            os.close(os.open(mark, os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600))


def create(state, key, panel: dict, check: Optional[Callable[[list], None]] = None, taken=None) -> dict:
    """A new panel in state `queued`, with its `panel.json` and derived `status.json`. `check` gets the
    open panels under the worktree lock and may raise to refuse the create."""
    created = panel.get("created")
    p = {"v": 1, "id": None, "project": _key(key)}
    for k in INPUT_KEYS:
        p[k] = panel.get(k)
    p.update({"created": float(created) if isinstance(created, (int, float)) and not isinstance(created, bool)
              else time.time(),
              "seats": dict(panel.get("seats") or {}), "state": "queued", "error": None, "short": None,
              "job": None, "asks": [], "judged": None, "record_id": None, "run_logged": False})
    with _lock(state, key):
        if check is not None:
            check([q for q in _panels(state, key) if q.get("state") in OPEN])
        while True:
            pid = new_id(state, key, taken)
            try:
                os.mkdir(work_dir(state, key, pid), 0o700)
                break
            except FileExistsError:
                continue
        p["id"] = pid
        _write(state, key, p)
    return p


def _panels(state, key, logged=False) -> list:
    """The readable panels of this worktree, oldest first. A logged panel is skipped by its mark,
    unread, unless `logged` (a worktree keeps every panel it ever ran)."""
    root = _work_root(state, _key(key))
    if not _no_links(state, root):
        return []
    try:
        names = os.listdir(root)
    except OSError:
        return []
    names = [n for n in names if PANEL_ID.fullmatch(n)
             and (logged or not os.path.lexists(os.path.join(root, n, LOGGED_MARK)))]
    out = [p for p in (_read(state, key, n) for n in names) if p is not None]
    if not logged:
        out = [p for p in out if p.get("state") != "logged"]
    return sorted(out, key=lambda p: p["created"] if isinstance(p.get("created"), (int, float)) else 0.0)


def panels(state, key, logged=False) -> list:
    """The readable panels of this worktree, oldest first; the logged ones only with `logged`."""
    return _panels(state, key, logged)


def open_panels(state, key) -> list:
    """The panels that hold one of the `MAX_OPEN` slots. A failed panel holds none."""
    return [p for p in _panels(state, key) if p.get("state") in OPEN]


def unlogged(state, key) -> list:
    """What the lead still has to finish: open panels, and failed panels with no record."""
    return [p for p in _panels(state, key)
            if p.get("state") in OPEN or (p.get("state") == "failed" and p.get("record_id") is None)]


def open_asks(panel: dict) -> list:
    return [a for a in panel.get("asks") or [] if a.get("state") in ASK_OPEN]


def rounds_used(asks, dispute) -> int:
    """The rounds a dispute has used: every ask on it that has not failed (a queued one counts)."""
    return sum(1 for a in asks or [] if isinstance(a, dict) and a.get("dispute") == dispute
               and a.get("state") != "failed")


def update(state, key, pid, fn: Callable[[dict], None]) -> dict:
    """Load, let `fn` change the panel in place, then write `panel.json` and `status.json`, all
    under the worktree lock. Every writer uses this. ValueError when the panel does not load."""
    with _lock(state, key):
        return _update_locked(state, key, pid, fn)


def _update_locked(state, key, pid, fn) -> dict:
    panel = _read(state, key, pid)
    if panel is None:
        raise ValueError(f"unknown panel: {pid}")
    fn(panel)
    panel.update(v=1, id=pid, project=key)
    _write(state, key, panel)
    return panel


def reserve_job(state, key, pid, job: dict) -> bool:
    """Take the worktree's one job slot for this panel (Design decision 3). False when any panel
    of the worktree already holds a job. ValueError when the panel does not load."""
    with _lock(state, key):
        if any(p.get("job") for p in _panels(state, key)):
            if _read(state, key, pid) is None:
                raise ValueError(f"unknown panel: {pid}")
            return False
        _update_locked(state, key, pid, lambda p: p.update(job=dict(job)))
        return True


def release_job(state, key, pid) -> None:
    """Clear this panel's job. A panel that does not load has nothing to clear."""
    with _lock(state, key):
        if _read(state, key, pid) is not None:
            _update_locked(state, key, pid, lambda p: p.update(job=None))


def has_job(state, key) -> bool:
    return any(p.get("job") for p in _panels(state, key))


def _seat_view(seats: dict) -> dict:
    out = {}
    for name, entry in (seats or {}).items():
        entry = entry if isinstance(entry, dict) else {}
        status = entry.get("status")
        if status == "ok":
            out[name] = f"stand-in:{entry['stand_in_for']}" if entry.get("stand_in_for") else "ok"
        elif status == "absent":
            out[name] = f"absent:{entry.get('reason') or 'unknown'}"
        elif status == "running":
            out[name] = "running"
        else:
            out[name] = "pending"
    return out


def status_view(panel: dict, view: Optional[str] = None) -> dict:
    """What the lead reads in `status.json`. It holds no claim text, target, or finding text.
    `view` is the panel's view dir; without it, the paths are relative to that dir."""
    view = view or ""
    asks = [a for a in panel.get("asks") or [] if isinstance(a, dict)]
    return {"v": 1, "panel": panel.get("id"), "checkpoint": panel.get("checkpoint"), "state": panel.get("state"),
            "short": panel.get("short"), "seats": _seat_view(panel.get("seats")),
            "next": os.path.join(view, "next.md") if panel.get("state") in ("ready", "failed") else None,
            "error": panel.get("error"),
            "asks": {a.get("id"): {"state": a.get("state"), "answer": os.path.join(view, "asks", f"{a.get('id')}.md")}
                     for a in asks},
            "busy": bool(open_asks({"asks": asks})), "updated": time.time()}


def run_event(panel: dict, duration_s: float, findings_raw: int = 0) -> dict:
    """The `panel_run` event that `bw improve` reads."""
    return {"type": "panel_run", "panel_id": panel.get("id"), "project": panel.get("project"),
            "checkpoint": panel.get("checkpoint"), "tier": panel.get("tier"), "seats": _seat_view(panel.get("seats")),
            "duration_s": duration_s, "findings_raw": findings_raw, "short": panel.get("short"),
            "state": panel.get("state")}


def record_run(config, state, key, pid, findings_raw, now) -> None:
    """Append this panel's `panel_run` event once; a second call does nothing."""
    with _lock(state, key):
        panel = _read(state, key, pid)
        if panel is None:
            raise ValueError(f"unknown panel: {pid}")
        if panel.get("run_logged"):
            return
        created = panel.get("created")
        duration = round(max(0.0, now - created), 1) if isinstance(created, (int, float)) else 0.0
        store.append(store.log_path(config), run_event(panel, duration, findings_raw))
        _update_locked(state, key, pid, lambda p: p.update(run_logged=True))
