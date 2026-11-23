"""This module keeps the seat verification state: reachable seats, seat keys, passes, and tokens.

It also writes the failures and `seats.json`. All times (`now`, `ts`) are wall-clock epoch seconds.
A pass is valid only for the exact seat key it was recorded against. Passes go to
`state_dir/seat-verify.json` (keyed by seat id, through `containment.record_pass`). The last failed
probe per seat goes to `state_dir/seats/failures.json`. A failure under the same key that is later
than the pass revokes the pass. `state_dir/seats.json` is derived from both and from the current keys.
"""
import contextlib
import fcntl
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time

from lead import containment, proc, sandbox
from lead import verify as lead_verify
from lead.locks import RuntimeLock
from lead.paths import ROOT, read_json, write_json_atomic
from seats import adapters, caps, judge

HERE = os.path.dirname(os.path.realpath(__file__))
PASS_STORE = "seat-verify.json"
ATTACK_ROOT = os.path.join("~", ".cache", "bw-seat-attack")
SCRIPT = os.path.join(ROOT, "tests", "attack", "seat_readonly.sh")
BW = os.path.join(ROOT, "bin", "bw")
# The model runs of tests/attack/seat_readonly.sh, each capped at panel.cap_min: fresh, resumed, hostile,
# hostile-covered (`bw seat`), and the positive control of each hostile case (`seats.run exec --control`).
# The script's header names this constant; change both together.
ATTACK_RUNS = 6
ATTACK_MARGIN_S = 300
PLANTED_LIST = "planted.list"  # <run>/planted.list: each path the script plants, written before it is made
MADE_SSH = "made-ssh"  # <run>/made-ssh: the script made ~/.ssh
RUN_MAX_AGE_S = 7 * 24 * 3600
INCOMPLETE = "attack-incomplete"  # the probe recorded when the script printed no FAIL line (a timeout, a crash)
# The real home dir of each lead runtime: the seat attack probes a write into each one that a lead uses.
LEAD_HOMES = {"claude-code": lambda: "~/.claude", "codex": lambda: os.environ.get("CODEX_HOME") or "~/.codex",
              "pi": lambda: "~/.pi/agent", "kimi": lambda: os.environ.get("KIMI_CODE_HOME") or "~/.kimi-code",
              "agy": lambda: "~/.gemini"}
# What a model that refused leaves behind: inconclusive, not a sandbox failure. A skipped probe is not
# here: its evidence is bw's own plain re-run, which no model can refuse.
REFUSAL_ONLY = ("control-failed:", "resume-not-exercised")


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


def changed_seats(config, state, run=proc.run) -> list:
    """The names in `seat_items(config)` whose current key has no valid pass."""
    return [n for n in seat_items(config) if not is_verified(config, state, n, run)]


def _lock_path(state):
    return os.path.join(_seats_dir(state), "verify.lock")


@contextlib.contextmanager
def hold_lock(state):
    """Hold `state_dir/seats/verify.lock` exclusively for the `with` body. Yields True when taken and
    False, at once and without waiting, when another process holds it (the body then must not run)."""
    fd = os.open(_lock_path(state), os.O_RDONLY | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True
    finally:
        os.close(fd)  # closing releases the flock


def spawn_verify(config, state, seats=None, bw=None) -> bool:
    """Start one detached `bw seat verify` child (`--changed` unless `seats` names ids) and return at
    once. The child holds `verify.lock` for its whole run. False, and no child, when the lock is held.

    Known race (documented, not closed): the parent only checks the lock, then spawns; the child takes
    it on its own. Two spawns in that gap start two children, and the second exits with status 75 on
    the child's own lock, so one verify still runs at a time. The fd is not passed down: the child's
    own `hold_lock` would then block on its parent's open file description."""
    with hold_lock(state) as free:
        if not free:
            return False
    argv = [sys.executable, bw or BW, "seat", "verify", *(seats or ["--changed"])]
    with open(os.path.join(_seats_dir(state), "verify-log.txt"), "ab") as log:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                         env={**os.environ, "BW_STATE_DIR": state})
    return True


def seat_home(state, seat, runtime=None) -> str:
    """`state_dir/seats/homes/<seat>`, created 0700. With `runtime`, the runtime's code dirs are made
    in it too, before any seat key reads the hook manifests, so verify, run, and the attack all see
    the same key. Raises OSError when the home was tampered with (adapters.make_code_dirs)."""
    home = os.path.join(state, "seats", "homes", seat)
    os.makedirs(home, mode=0o700, exist_ok=True)
    os.chmod(home, 0o700)
    if runtime:
        adapters.make_code_dirs(runtime, home)
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
    home = seat_home(state, seat, s["runtime"])
    key.update({"seat": seat, "model": s["model"], "effort": adapters.effort(s, False), "args": list(s.get("args", [])),
                "seat_adapter_sha": containment.sha_file(os.path.join(HERE, "adapters.py")),
                "seat_grants_sha": containment.sha_file(os.path.join(HERE, "grants.py")),
                "hook_files": {p: _manifest(p) for p in sorted(adapter.hook_files(home))}})
    return key


def _private_dir(d):
    os.makedirs(d, mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def _seats_dir(state):
    return _private_dir(os.path.join(state, "seats"))


class _flock:
    """An exclusive flock on `path` for a read-modify-write across processes and threads."""

    def __init__(self, path):
        self.path = path

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX)
        except BaseException:
            os.close(self.fd)
            raise
        return self

    def __exit__(self, *exc):
        os.close(self.fd)


def _failures_path(state):
    return os.path.join(state, "seats", "failures.json")


def _dict(x):
    return x if isinstance(x, dict) else {}


def _ts(entry):
    """The entry's `ts` (epoch seconds); a missing or non-numeric value reads as 0."""
    t = entry.get("ts")
    return float(t) if isinstance(t, (int, float)) and not isinstance(t, bool) else 0.0


def record_failure(state, seat, key, probe, now) -> None:
    """The last failed probe for `seat`, under the key it failed on. `now`: epoch seconds.

    A failure later than the stored pass under the same key also removes that pass, so a later
    failure under another key cannot bring the revoked pass back (R7)."""
    path = os.path.join(_seats_dir(state), "failures.json")
    passes = os.path.join(state, PASS_STORE)
    with _flock(path + ".lock"):
        data = _dict(read_json(path, {}))
        data[seat] = {"key": key, "probe": probe, "ts": float(now)}
        write_json_atomic(path, data)
        with _flock(passes + ".lock"):  # the lock containment.record_pass takes
            stored = _dict(read_json(passes, {}))
            p = _dict(stored.get(seat))
            if p.get("key") == key and float(now) > _ts(p):
                del stored[seat]
                write_json_atomic(passes, stored)


def _status(state, seat, key):
    """(pass entry or None when not verified, failure entry, any record at all)."""
    p = _dict(_dict(read_json(os.path.join(state, PASS_STORE), {})).get(seat))
    f = _dict(_dict(read_json(_failures_path(state), {})).get(seat))
    revoked = f.get("key") == key and _ts(f) > _ts(p)
    good = p if p.get("key") == key and not revoked else None
    return good, f, bool(p or f)


def is_verified(config, state, seat, run=proc.run) -> bool:
    return _status(state, seat, seat_key(config, state, seat, run))[0] is not None


def _iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def write_seats_json(config, state, run=proc.run, now=None) -> dict:
    """`state_dir/seats.json`: one entry per enabled seat, in config order.

    `now` is reserved (epoch seconds) and not read: `verified_at` is the stored pass `ts`. The version
    commands run before the lock is taken, so a slow CLI never holds it."""
    path = os.path.join(state, "seats.json")
    os.makedirs(state, exist_ok=True)
    keys = {seat: seat_key(config, state, seat, run) for seat, s in config["seats"].items() if s.get("enabled", True)}
    with _flock(path + ".lock"):
        out = {}
        for seat, key in keys.items():
            good, failure, any_record = _status(state, seat, key)
            if good is not None:
                probe = None
            elif failure.get("key") == key:
                probe = failure.get("probe")
            else:
                probe = "key changed" if any_record else "never verified"
            out[seat] = {"verified": good is not None, "runtime_version": key["runtime_version"],
                         "verified_at": _iso(_ts(good)) if good is not None else None, "failed_probe": probe}
        write_json_atomic(path, out)
    return out


def _token_path(state, seat):
    return os.path.join(state, "seats", "attack-tokens", f"{seat}.json")


def issue_token(state, seat, now, ttl_s) -> None:
    """One token file per seat, mode 0600, written atomically. Never on argv or in the env.
    `now`: epoch seconds; the token expires at `now + ttl_s`."""
    _seats_dir(state)
    path = _token_path(state, seat)
    d = _private_dir(os.path.dirname(path))
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
    seat and has not expired at `now` (epoch seconds)."""
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


# ---- the attack: `bw seat verify` ----

def attack_timeout(config) -> int:
    """Seconds for the whole attack: the script's worst case (controller ruling, fix round 1). Each of
    the ATTACK_RUNS model runs can take the cap the attack uses (`panel.cap_min`: the script runs no
    deep tier), plus a fixed margin for the setup, the plain probes, and the judge."""
    return ATTACK_RUNS * config["panel"]["cap_min"] * 60 + ATTACK_MARGIN_S


def run_tag(run_dir) -> str:
    """The run dir's own suffix (`run.XXXXXX` -> `XXXXXX`). The script puts it in the name of every
    file it plants outside the run dir, so a sweep can find them."""
    return os.path.basename(run_dir).split(".", 1)[-1]


def _inside(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def sweep(run_dir) -> None:
    """Remove what the script planted, when its own trap did not run (the attack timed out and was
    killed). The script lists each path in `<run>/planted.list` before it makes it. Only a path in the
    run dir, or one whose name holds `-<tag>`, is removed. `~/.ssh` goes only when the script made it
    (`<run>/made-ssh`) and it is now empty."""
    tag = run_tag(run_dir)
    try:
        with open(os.path.join(run_dir, PLANTED_LIST), encoding="utf-8", errors="replace") as f:
            paths = [line.rstrip("\n") for line in f]
    except OSError:
        paths = []
    for p in paths:
        if not os.path.isabs(p) or not (_inside(p, run_dir) or f"-{tag}" in os.path.basename(p)):
            continue
        try:
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p)
            else:
                os.unlink(p)
        except OSError:
            pass
    if os.path.exists(os.path.join(run_dir, MADE_SSH)):
        try:
            os.rmdir(os.path.join(os.path.expanduser("~"), ".ssh"))
        except OSError:
            pass


def prune_runs(root, now) -> None:
    """Remove the attack run dirs under `root` older than RUN_MAX_AGE_S (`now`: epoch seconds)."""
    try:
        names = os.listdir(root)
    except OSError:
        return
    for name in names:
        p = os.path.join(root, name)
        try:
            st = os.lstat(p)
        except OSError:
            continue
        if name.startswith("run.") and stat.S_ISDIR(st.st_mode) and now - st.st_mtime > RUN_MAX_AGE_S:
            sweep(p)
            shutil.rmtree(p, ignore_errors=True)


def lead_homes(config) -> list:
    """The real home dir of each lead runtime the config uses, once each, when it exists."""
    out = []
    for runtime in lead_verify.lead_runtimes(config):
        home = LEAD_HOMES.get(runtime)
        if home is None:
            continue
        real = os.path.realpath(os.path.expanduser(home()))
        if os.path.isdir(real) and real not in out:
            out.append(real)
    return out


def failure_message(stdout) -> str:
    """What `bw seat verify` says when the attack script did not print PASS."""
    names = judge.failure_names(stdout)
    stopped = judge.stopped_before_write(stdout)
    if names and all(n.startswith(REFUSAL_ONLY) or n in stopped for n in names):
        if stopped:
            return ("not recorded — a probe's tool could not start under the sandbox, so the probe stopped "
                    f"before its write ({' '.join(stopped)}); a re-run does not change this; see the evidence")
        return "not recorded — a refusal by the model is inconclusive; re-run"
    return "not recorded — the seat attack failed; see the evidence"


def verify_seat(config, state, seat, run=proc.run, now=time.time, _seats_json=True):
    """Attack `seat` in an isolated run dir and record a pass on the key attacked. Returns (ok, message).

    The run dir is `~/.cache/bw-seat-attack/run.XXXXXX/` and its state dir `<run>/state/`, where the
    attack token lives (never on argv). A failure is recorded with the first failed probe. The token
    is cleared and `seats.json` rewritten on every exit path (`_seats_json=False`: the caller writes it)."""
    runtime = config["seats"][seat]["runtime"]
    root = os.path.expanduser(ATTACK_ROOT)
    os.makedirs(root, exist_ok=True)
    prune_runs(root, now())
    run_dir = os.path.realpath(tempfile.mkdtemp(prefix="run.", dir=root))
    attack_state = _private_dir(os.path.join(run_dir, "state"))
    timeout = attack_timeout(config)
    argv = [SCRIPT, "--seat", seat, "--bw", BW, "--run", run_dir]
    argv += [a for home in lead_homes(config) for a in ("--lead-home", home)]
    argv += ["--deny-read", os.path.realpath(state)]  # the attack's runs use RUN/state: deny the real one too

    def key_fn():
        return seat_key(config, state, seat, run)

    def record(key, evidence):
        containment.record_pass(state, seat, key, evidence, store=PASS_STORE)

    try:
        issue_token(attack_state, seat, now(), timeout)
        ok, msg = lead_verify._attack(state, run, argv, key_fn, record, failure_message, "seat", runtime,
                                      f"seat {seat}", timeout=timeout)
        if not ok and not msg.endswith(lead_verify.KEY_CHANGED):  # a changed key was never attacked
            record_failure(state, seat, key_fn(), judge.failure_probe(msg) or INCOMPLETE, now())
    finally:
        clear_token(attack_state, seat)
        sweep(run_dir)
        if _seats_json:
            write_seats_json(config, state, run=run)
    return ok, msg


def _reverify_one(config, state, runtime, name, now, run, allow_run, version_ok):
    kind = f"seat {name}"
    key = seat_key(config, state, name, run)
    if _status(state, name, key)[0] is not None:
        return lead_verify.Item(runtime, kind, "verified", "key matches the recorded pass")
    if not allow_run:
        return lead_verify.Item(runtime, kind, "pending", "--no-verify")
    if not version_ok or key.get("runtime_version") == "unreadable":
        return lead_verify.Item(runtime, kind, "pending", "version unreadable")
    why = caps.capped(config, state, name, now)
    if why:
        return lead_verify.Item(runtime, kind, "pending", why)
    ok, msg = verify_seat(config, state, name, run=run, _seats_json=False)
    return lead_verify.Item(runtime, kind, "passed" if ok else "failed", msg)


def reverify_seats(config, state, runtime, now, run=proc.run, allow_run=True, version_ok=True) -> list:
    """One `lead.verify.Item` per reachable seat on `runtime`: verified, pending, passed, or failed.
    `now`: epoch seconds. The shared runtime lock is held from the first key read to the end."""
    names = [n for n in seat_items(config) if config["seats"][n]["runtime"] == runtime]
    if not names:
        return []
    lock = RuntimeLock(state, runtime)
    try:
        lock.shared()
        items = [_reverify_one(config, state, runtime, n, now, run, allow_run, version_ok) for n in names]
        write_seats_json(config, state, run=run)
    finally:
        lock.release()
    return items
