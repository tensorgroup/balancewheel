"""Per-seat, per-directory session ids, and the one-run-at-a-time lock."""
import contextlib
import fcntl
import hashlib
import os

from lead.paths import read_json, write_json_atomic


class Busy(Exception):
    """A run for this seat and directory already holds the lock."""


def _file(state, seat):
    return os.path.join(state, "seats", "sessions", f"{seat}.json")


def get(state, seat, target, model):
    data = read_json(_file(state, seat), {})
    entry = data.get(target) if isinstance(data, dict) else None
    if not isinstance(entry, dict) or entry.get("model") != model:
        return None
    sid = entry.get("session_id")
    return sid if isinstance(sid, str) and sid else None


def put(state, seat, target, session_id, model, now):
    path = _file(state, seat)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path + ".lock", os.O_RDONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        data = read_json(path, {})
        if not isinstance(data, dict):
            data = {}
        data[target] = {"session_id": session_id, "model": model, "ts": float(now)}
        write_json_atomic(path, data)
    finally:
        os.close(fd)


@contextlib.contextmanager
def run_lock(state, seat, target):
    """Exclusive and non-blocking: a held lock raises Busy. A run never waits for another."""
    d = os.path.join(state, "seats", "locks")
    os.makedirs(d, exist_ok=True)
    name = f"{seat}-{hashlib.sha256(target.encode()).hexdigest()[:12]}.lock"
    fd = os.open(os.path.join(d, name), os.O_RDONLY | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Busy(f"seat {seat} is already running on {target}")
        yield
    finally:
        os.close(fd)
