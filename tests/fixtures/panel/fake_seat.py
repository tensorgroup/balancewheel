"""fake_seat.py — a synthetic stand-in for `bw seat`, for the panel worker's tests.

It reads the `bw seat` flags the worker passes, records its argv in `$FAKE_OUT/seat-<seat>-<n>.json`
and its pid, times, and BW_STATE_DIR in `$FAKE_OUT/meta-<seat>-<n>.json`, then acts on
`FAKE_SEAT_<seat>` (default `ok:clean-a`). `FAKE_DELAY` (seconds) delays the act.

Modes: ok:<fixture>, nosession, echo, cap, precap, timeout, unverified, hostile, ratelimit,
reason:<text> (a refusal with that reason), sleep (60 s with one child in its own session; on
SIGTERM it kills the child, writes no result file, and exits 143, as `bw seat` does).
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time

OUTPUTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")


def _parse(argv):
    p = argparse.ArgumentParser(prog="fake_seat", allow_abbrev=False)
    p.add_argument("seat")
    p.add_argument("--no-fallback", action="store_true")
    p.add_argument("-d", dest="dir")
    p.add_argument("--out")
    p.add_argument("--result")
    p.add_argument("--brief-file")
    p.add_argument("--session")
    p.add_argument("--deep", action="store_true")
    p.add_argument("prompt")
    return p.parse_args(argv)


def _claim(out_dir, prefix, seat, body):
    """Write `body` to the first free `<prefix>-<seat>-<n>.json`; return n."""
    n = 1
    while True:
        path = os.path.join(out_dir, f"{prefix}-{seat}-{n}.json")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            n += 1
            continue
        with os.fdopen(fd, "w") as f:
            json.dump(body, f)
        return n


def _atomic(path, text):
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def main(argv):
    args = _parse(argv)
    out_dir = os.environ["FAKE_OUT"]
    n = _claim(out_dir, "seat", args.seat, argv)
    meta = {"pid": os.getpid(), "t0": time.time(), "t1": None, "child": None,
            "state_dir": os.environ.get("BW_STATE_DIR")}
    meta_path = os.path.join(out_dir, f"meta-{args.seat}-{n}.json")
    _atomic(meta_path, json.dumps(meta))
    mode = os.environ.get(f"FAKE_SEAT_{args.seat}", "ok:clean-a")
    time.sleep(float(os.environ.get("FAKE_DELAY", "0")))

    def result(ok, reason, exit_code, ran=True, session=None):
        _atomic(args.result, json.dumps({
            "v": 1, "seat": args.seat, "requested": args.seat, "stand_in_for": None, "runtime": "fake",
            "model": f"model-{args.seat}", "ran": ran, "ok": ok, "reason": reason, "exit_code": exit_code,
            "session_id": session, "mode": ("resume" if args.session else "new") if ran else None,
            "dir": args.dir, "duration_s": 0.1 if ran else 0.0}))
        meta["t1"] = time.time()
        _atomic(meta_path, json.dumps(meta))
        return exit_code

    if args.brief_file:
        with open(args.brief_file) as f:  # as `bw seat` does: an unreadable brief is a traceback
            f.read()
    if mode.startswith("ok:") or mode == "nosession":
        fixture = mode[3:] if mode.startswith("ok:") else "clean-a"
        with open(os.path.join(OUTPUTS, fixture + ".md")) as f:
            _atomic(args.out, f.read())
        return result(True, "ok", 0, session=None if mode == "nosession" else f"s-{args.seat}")
    if mode == "echo":
        _atomic(args.out, args.prompt + "\n")
        return result(True, "ok", 0, session=args.session or f"s-{args.seat}")
    if mode == "cap":
        _atomic(args.out, "")
        return result(False, "usage cap", 1)
    if mode == "precap":
        return result(False, "usage cap", 75, ran=False)
    if mode == "timeout":
        _atomic(args.out, "partial answer\n")
        return result(False, "timeout", 124)
    if mode == "unverified":
        return result(False, "unverified", 75, ran=False)
    if mode == "hostile":
        return result(False, "hostile: .mcp.json loads code for claude-code", 75, ran=False)
    if mode == "ratelimit":
        _atomic(args.out, "")
        return result(False, "error: 429 too many requests", 1)
    if mode.startswith("reason:"):
        return result(False, mode[len("reason:"):], 75, ran=False)
    if mode == "sleep":
        children = []

        def on_term(signum, frame):
            for child in children:
                try:
                    os.killpg(child.pid, signal.SIGKILL)  # its mask may block SIGTERM
                except ProcessLookupError:
                    pass
                child.wait()
            os._exit(143)

        signal.signal(signal.SIGTERM, on_term)  # before the child starts, so no child outlives us
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
        children.append(child)
        signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM})
        meta["child"] = child.pid
        _atomic(meta_path, json.dumps(meta))
        time.sleep(60)
        child.kill()
        child.wait()
        return result(True, "ok", 0, session=f"s-{args.seat}")
    raise SystemExit(f"fake_seat: unknown mode {mode!r}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
