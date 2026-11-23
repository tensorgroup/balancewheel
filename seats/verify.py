"""Seat verification state: which seats bw can reach, each seat's key, its passes and failures,
`seats.json`, and the attack token.

A pass is valid only for the exact seat key it was recorded against. Passes go to
`state_dir/seat-verify.json` (keyed by seat id, through `containment.record_pass`). The last failed
probe per seat goes to `state_dir/seats/failures.json`. A failure under the same key that is later
than the pass revokes the pass. `state_dir/seats.json` is derived from both and from the current keys.
"""
import fcntl
import json
import os
import secrets
import stat
import tempfile
import time

from lead import containment, proc, sandbox
from lead.paths import read_json, write_json_atomic
from seats import adapters

HERE = os.path.dirname(os.path.realpath(__file__))
PASS_STORE = "seat-verify.json"


def _enabled(seats, name):
    return name in seats and seats[name].get("enabled", True)


def seat_items(config) -> list:
    """The one reachability walk, in config order: enabled seats named in a profile checkpoint,
    enabled `tier: "reserved"` seats, and every enabled fallback of a seat already in the list."""
    seats = config["seats"]
    want = set()
    for profile in (config.get("profiles") or {}).values():
        for checkpoint in (profile.get("checkpoints") or {}).values():
            want.update(n for n in checkpoint.get("seats", []) if _enabled(seats, n))
    want.update(n for n, s in seats.items() if _enabled(seats, n) and s.get("tier") == "reserved")
    todo = list(want)
    while todo:
        fallback = seats[todo.pop()].get("fallback")
        if fallback and _enabled(seats, fallback) and fallback not in want:
            want.add(fallback)
            todo.append(fallback)
    return [n for n in seats if n in want]


def seat_home(state, seat) -> str:
    """`state_dir/seats/homes/<seat>`, created 0700."""
    home = os.path.join(state, "seats", "homes", seat)
    os.makedirs(home, mode=0o700, exist_ok=True)
    os.chmod(home, 0o700)
    return home


def _manifest(path):
    try:
        return containment.manifest(path)
    except OSError:
        return "unreadable"


def seat_key(config, state, seat, run=proc.run) -> dict:
    """The seat's verification key. `runtime_version` is "unreadable" when the version command
    cannot run (Task 11 refuses on that value); a hook file that cannot be read is "unreadable" too."""
    s = config["seats"][seat]
    adapter = adapters.SEAT_ADAPTERS[s["runtime"]]
    backend = "sandbox-exec" if adapter.backend == "sandbox-exec" else sandbox.backend()
    key = containment.verification_key(s["runtime"], adapter.version_argv, backend, run,
                                       sandbox.denied_ports(config))
    del key["adapter_sha"]  # lead/runtimes.py is not seat code: a lead adapter change keeps seat passes
    home = seat_home(state, seat)
    key.update({"seat": seat, "model": s["model"], "effort": adapters.effort(s, False), "args": list(s.get("args", [])),
                "seat_adapter_sha": containment.sha_file(os.path.join(HERE, "adapters.py")),
                "hook_files": {p: _manifest(p) for p in sorted(adapter.hook_files(home))}})
    return key


def _seats_dir(state):
    d = os.path.join(state, "seats")
    os.makedirs(d, exist_ok=True)
    return d


class _flock:
    """An exclusive flock on `path` for a read-modify-write across processes and threads."""

    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDONLY | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        os.close(self.fd)


def _failures_path(state):
    return os.path.join(state, "seats", "failures.json")


def _dict(x):
    return x if isinstance(x, dict) else {}


def record_failure(state, seat, key, probe, now) -> None:
    """The last failed probe for `seat`, under the key it failed on."""
    path = os.path.join(_seats_dir(state), "failures.json")
    with _flock(path + ".lock"):
        data = _dict(read_json(path, {}))
        data[seat] = {"key": key, "probe": probe, "ts": float(now)}
        write_json_atomic(path, data)


def _status(state, seat, key):
    """(pass entry or None when not verified, failure entry, any record at all)."""
    p = _dict(_dict(read_json(os.path.join(state, PASS_STORE), {})).get(seat))
    f = _dict(_dict(read_json(_failures_path(state), {})).get(seat))
    revoked = f.get("key") == key and f.get("ts", 0) > p.get("ts", 0)
    good = p if p.get("key") == key and not revoked else None
    return good, f, bool(p or f)


def is_verified(config, state, seat, run=proc.run) -> bool:
    return _status(state, seat, seat_key(config, state, seat, run))[0] is not None


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def write_seats_json(config, state, run=proc.run, now=None) -> dict:
    """`state_dir/seats.json`: one entry per enabled seat, in config order. `now` is accepted for
    callers that pass the run's clock; the entries take their times from the stored records."""
    path = os.path.join(state, "seats.json")
    os.makedirs(state, exist_ok=True)
    with _flock(path + ".lock"):
        out = {}
        for seat, s in config["seats"].items():
            if not s.get("enabled", True):
                continue
            key = seat_key(config, state, seat, run)
            good, failure, any_record = _status(state, seat, key)
            if good is not None:
                probe = None
            elif failure.get("key") == key:
                probe = failure.get("probe")
            else:
                probe = "key changed" if any_record else "never verified"
            out[seat] = {"verified": good is not None, "runtime_version": key["runtime_version"],
                         "verified_at": _iso(good["ts"]) if good is not None else None, "failed_probe": probe}
        write_json_atomic(path, out)
    return out


def _token_path(state, seat):
    return os.path.join(state, "seats", "attack-tokens", f"{seat}.json")


def issue_token(state, seat, now, ttl_s) -> None:
    """One token file per seat, mode 0600, written atomically. Never on argv or in the env."""
    path = _token_path(state, seat)
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=f".{seat}.", suffix=".tmp")  # mkstemp makes it 0600
    try:
        with os.fdopen(fd, "w") as f:
            json.dump({"seat": seat, "token": secrets.token_hex(16), "expires": float(now) + ttl_s}, f)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def check_token(state, seat, now) -> bool:
    """True only for a regular file (never a link), mode 0600, owned by this user, that names this
    seat and has not expired."""
    try:
        fd = os.open(_token_path(state, seat), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return False
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) != 0o600 or st.st_uid != os.getuid():
            return False
        with os.fdopen(os.dup(fd)) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    finally:
        os.close(fd)
    if not isinstance(data, dict) or data.get("seat") != seat or not isinstance(data.get("token"), str):
        return False
    expires = data.get("expires")
    return isinstance(expires, (int, float)) and not isinstance(expires, bool) and now < expires


def clear_token(state, seat) -> None:
    try:
        os.unlink(_token_path(state, seat))
    except FileNotFoundError:
        pass
