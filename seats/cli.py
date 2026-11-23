"""`bw seat <id>`, `bw seat list`, and `bw seat verify`: the command line over `seats.run.run_seat` and
`seats.verify.verify_seat`.

stdout holds the answer text only. stderr holds `bw seat:` lines. Exit statuses: the CLI's own status,
124 on the wall-clock cap, 75 when bw refuses or cannot start the seat, 143 after SIGTERM, 2 on a usage
error or an unknown seat id.
"""
import argparse
import json
import signal
import sys
import time

from lead import proc
from lead.paths import write_json_atomic
from seats import caps, verify
from seats import run as seat_run

USAGE_ERROR = 2
BUSY = 75
PREFIX = "bw seat: "


class _Usage(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _Usage(message)


def _parser():
    p = _Parser(prog="bw seat", allow_abbrev=False, description="Run one read-only seat. The answer goes to stdout.")
    p.add_argument("seat", metavar="id")
    b = p.add_mutually_exclusive_group()
    b.add_argument("-b", dest="brief", action="store_true", help="build the brief for this worktree (new session)")
    b.add_argument("--brief-file", metavar="F", help="read the brief from F (new session)")
    r = p.add_mutually_exclusive_group()
    r.add_argument("-r", dest="resume", action="store_true", help="resume the stored session")
    r.add_argument("--session", metavar="ID", help="resume exactly this session")
    p.add_argument("-d", dest="target", metavar="DIR", default=None, help="the directory (default: here)")
    p.add_argument("--deep", action="store_true", help="the deep tier: longer cap, more effort")
    p.add_argument("--no-fallback", action="store_true", help="no stand-in when the seat is capped")
    p.add_argument("--out", metavar="F", help="write the answer to F")
    p.add_argument("--result", metavar="F", help="write the run result to F as JSON")
    p.add_argument("--transcript", metavar="F", help="write the raw stdout and stderr to F")
    p.add_argument("--attack", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("prompt")
    return p


def _line(err, text):
    print(PREFIX + text, file=err)


def _list(argv, config, state, run, out, err):
    p = _Parser(prog="bw seat list", allow_abbrev=False)
    p.add_argument("--json", action="store_true")
    try:
        a = p.parse_args(argv)
    except _Usage as e:
        _line(err, f"usage error: {e}")
        return USAGE_ERROR
    now = time.time()
    status = verify.write_seats_json(config, state, run=run)
    reachable = set(verify.seat_items(config))
    rows = []
    for name, s in config["seats"].items():
        enabled = s.get("enabled", True)
        st = status.get(name) or {}
        rows.append({"id": name, "runtime": s["runtime"], "model": s["model"], "plan": s.get("plan"),
                     "fallback": s.get("fallback"), "tier": s.get("tier", "default"), "enabled": enabled,
                     "reachable": name in reachable, "verified": bool(st.get("verified")),
                     "failed_probe": st.get("failed_probe"),
                     "capped": caps.capped(config, state, name, now) if enabled else None})
    if a.json:
        print(json.dumps(rows, indent=2), file=out)
        return 0
    for r in rows:
        verified = "yes" if r["verified"] else f"no ({r['failed_probe'] or 'never verified'})"
        print(f"{r['id']}  {r['runtime']}  {r['model']}  plan={r['plan'] or '-'}  fallback={r['fallback'] or '-'}  "
              f"tier={r['tier']}  verified={verified}  capped={r['capped'] or 'no'}", file=out)
    return 0


def _verify(argv, config, state, run, out, err):
    """Attack each named seat (default: every reachable seat) and print its result. Exit 0 when every
    seat passed, else 1; 2 on a usage error or an unknown seat id."""
    p = _Parser(prog="bw seat verify", allow_abbrev=False)
    p.add_argument("ids", nargs="*", metavar="id")
    p.add_argument("--all", action="store_true")
    p.add_argument("--changed", action="store_true", help="only the seats whose key has no valid pass")
    try:
        a = p.parse_args(argv)
    except _Usage as e:
        _line(err, f"usage error: {e}")
        return USAGE_ERROR
    if sum(map(bool, (a.ids, a.all, a.changed))) > 1:
        _line(err, "usage error: give seat ids, --all, or --changed, not a mix")
        return USAGE_ERROR
    unknown = [n for n in a.ids if n not in config["seats"]]
    if unknown:
        _line(err, f"unknown seat {unknown[0]!r}; configured: {', '.join(config['seats'])}")
        return USAGE_ERROR
    with verify.hold_lock(state) as free:  # one verify at a time; spawn_verify sees this lock
        if not free:
            _line(err, "another seat verify is running")
            return BUSY
        if a.changed:
            return _verify_changed(config, state, run, out)
        passed = True
        for name in a.ids or verify.seat_items(config):
            ok, msg = verify.verify_seat(config, state, name, run=run)
            print(msg, file=out)
            passed = passed and ok
        return 0 if passed else 1


def _verify_changed(config, state, run, out):
    """Attack the seats without a valid pass, one runtime at a time (pending rules: reverify_seats)."""
    changed = verify.changed_seats(config, state, run=run)
    runtimes = []
    for name in changed:
        if config["seats"][name]["runtime"] not in runtimes:
            runtimes.append(config["seats"][name]["runtime"])
    failed = False
    for runtime in runtimes:
        for item in verify.reverify_seats(config, state, runtime, time.time(), run=run):
            if item.state == "verified":
                continue
            lines = [ln for ln in (item.reason or "").splitlines() if ln.strip()]
            print(f"{item.kind}: {item.state}" + (f" — {lines[-1].strip()}" if lines else ""), file=out)
            failed = failed or item.state == "failed"
    return 1 if failed else 0


def _on_sigterm(signum, frame):
    raise SystemExit(seat_run.INTERRUPTED)


def _install_sigterm():
    """Returns the previous handler (SIG_DFL when it was not set from Python), or None when no handler
    could be set (not the main thread)."""
    try:
        previous = signal.signal(signal.SIGTERM, _on_sigterm)
    except ValueError:
        return None
    return signal.SIG_DFL if previous is None else previous


def main(argv, config, state, run=proc.run, out=sys.stdout, err=sys.stderr) -> int:
    argv = list(argv)
    if argv and argv[0] == "list":
        return _list(argv[1:], config, state, run, out, err)
    if argv and argv[0] == "verify":
        return _verify(argv[1:], config, state, run, out, err)
    try:
        a = _parser().parse_args(argv)
    except _Usage as e:
        _line(err, f"usage error: {e}")
        return USAGE_ERROR
    except SystemExit as e:  # -h / --help
        return e.code or 0
    if not a.prompt.strip():
        _line(err, "usage error: the prompt is empty")
        return USAGE_ERROR
    if a.seat not in config["seats"] or a.seat in ("list", "verify"):
        _line(err, f"unknown seat {a.seat!r}; configured: {', '.join(config['seats'])}")
        return USAGE_ERROR

    def say(text):
        _line(err, text[len(PREFIX):] if text.startswith(PREFIX) else text)

    previous = _install_sigterm()
    try:
        result = seat_run.run_seat(
            config, state, a.seat, a.prompt, target=a.target, brief=a.brief, brief_file=a.brief_file,
            resume=a.resume, session_id=a.session, deep=a.deep, fallback=not a.no_fallback, out=a.out,
            transcript=a.transcript, attack=a.attack, run=run, say=say)
    except SystemExit as e:
        if e.code == seat_run.INTERRUPTED:
            _line(err, f"{a.seat} interrupted")
            return seat_run.INTERRUPTED
        raise
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)
    if result.answer:
        out.write(result.answer)
    if a.result:
        try:  # atomic; a symlink at the path is replaced, never followed
            write_json_atomic(a.result, seat_run.result_json(result))
        except OSError as e:
            _line(err, f"cannot write --result: {e.strerror or e}")
    if not result.ok:
        who = result.seat if result.seat == a.seat else f"{result.seat} (for {a.seat})"
        _line(err, f"{who} {'failed' if result.ran else 'refused'}: {result.reason}")
    return result.exit_code
