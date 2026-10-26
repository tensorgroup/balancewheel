"""Read a third-party SQLite database without touching it: copy, then read the copy.

A direct read-only open of a WAL database still writes its `-shm` (and creates `-wal`/`-shm`
when absent), and a direct read-only open of a live app's database can fail outright. So
`query` copies `db` and `db-wal` (never `-shm`, which SQLite rebuilds from the WAL) into a
fresh 0700 directory under `state_dir/sealed/` — a path every lead sandbox denies reading —
opens the copy with `PRAGMA query_only = ON`, runs the callback, and removes the copy in a
`finally`. Nothing is retried: a copy taken while the app was writing is `unknown`.
"""
import os
import shutil
import sqlite3
import stat
import tempfile

MAX_BYTES = 256 * 2**20
INCONSISTENT = "snapshot inconsistent; the app was writing — refresh again"


def _copy(src, dst):
    shutil.copyfile(src, dst)


def _regular_size(path):
    """Size of a regular file, None when absent; raises ValueError when not a regular file."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(st.st_mode):
        raise ValueError(f"{os.path.basename(path)} is not a regular file")
    return st.st_size


def _sealed_dir(state):
    d = os.path.join(state, "sealed")
    os.makedirs(d, mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def query(db_path, fn, state, max_bytes=MAX_BYTES):
    """(fn(conn), None) on a sealed copy of `db_path`, or (None, reason)."""
    name = os.path.basename(db_path)
    try:
        db_size = _regular_size(db_path)
        wal_size = _regular_size(db_path + "-wal")
    except (OSError, ValueError) as e:
        return None, f"{name}: {e}" if isinstance(e, ValueError) else f"{name}: unreadable ({type(e).__name__})"
    if db_size is None:
        return None, f"{name} absent"
    if db_size + (wal_size or 0) > max_bytes:
        return None, f"{name} too large to snapshot"
    try:
        tmp = tempfile.mkdtemp(dir=_sealed_dir(state))
    except OSError as e:
        return None, f"cannot make a sealed snapshot dir ({type(e).__name__})"
    conn = None
    try:
        copy = os.path.join(tmp, "snapshot.db")
        try:
            _copy(db_path, copy)
            if wal_size is not None:
                _copy(db_path + "-wal", copy + "-wal")
        except FileNotFoundError:
            return None, INCONSISTENT  # the app checkpointed or removed a file mid-copy
        except OSError as e:
            return None, f"{name}: unreadable ({type(e).__name__})"
        try:
            conn = sqlite3.connect(copy)
            conn.execute("PRAGMA query_only = ON")
            return fn(conn), None
        except sqlite3.OperationalError as e:
            msg = str(e)
            if msg.startswith(("no such table", "no such column")):
                return None, f"{name}: schema changed ({msg})"
            return None, INCONSISTENT
        except sqlite3.DatabaseError:
            return None, INCONSISTENT
    finally:
        if conn is not None:
            conn.close()
        shutil.rmtree(tmp, ignore_errors=True)
