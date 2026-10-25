"""broker.py — one decision per tool call, over a socket no lead can write, logged to guard.jsonl.

Wire: a client sends one JSON line (<= 1 MiB) {"v":1,"token","runtime","cwd","payload"} and gets one
line back, {"v":1,"id","decision","reason"} or {"v":1,"error":...}. In shadow the reply is always
allow while the record keeps the rules' real decision; in acting the reply is the decision.
An error reply writes no record. The record is written before the reply goes out.
"""
import argparse
import hmac
import json
import os
import signal
import socket
import sys
import threading
import time
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))))

from lead.guard import guard_version, record, rules_version  # noqa: E402
from lead.guard.normalise import normalise  # noqa: E402
from lead.guard.redact import digest, redact  # noqa: E402
from lead.guard.rules import RuleContext, decide  # noqa: E402

MAX_REQUEST = 1024 * 1024
MAX_PATH = 103  # sun_path is 104 bytes on macOS, NUL included
MAX_WORKERS = 8
READ_DEADLINE = 0.5
OUTCOME = {"allow": "allowed", "block": "blocked", "ask": "asked"}


def _text(call):
    """The call as text, unredacted: the command, else the tool with its paths and args."""
    if call.kind == "shell" and call.command is not None:
        return call.command
    return json.dumps({"tool": call.tool, "paths": list(call.paths), "args": call.args},
                      sort_keys=True, default=str)


def _pct(vals, q):
    if not vals:
        return 0.0
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(round(q * (len(vals) - 1))))]


class Broker:
    def __init__(self, state_dir, guard_dir, token, mode, session, ctx, reply_garbage=False):
        self.state_dir, self.guard_dir, self.token = state_dir, guard_dir, token
        self.mode, self.session, self.ctx = mode, dict(session), ctx
        self.reply_garbage = reply_garbage
        self.sock_path = os.path.join(guard_dir, "sock")
        self.policy = record.policy_digest(ctx.protected_branches, ctx.deploy_commands)
        self.rules_v, self.guard_v = rules_version(), guard_version()
        self._listener = None
        self._accept = None
        self._workers = []
        self._stopped = threading.Event()
        self._lock = threading.Lock()
        self._sem = threading.Semaphore(MAX_WORKERS)
        self._marker = None
        self._calls = 0
        self._by_decision = {}
        self._gray = 0
        self._latency = []

    def arm_self_test(self, marker):
        self._marker = marker

    def bind(self):
        if len(os.fsencode(self.sock_path)) > MAX_PATH:
            raise ValueError("guard socket path is %d bytes, over the %d-byte limit: %s"
                             % (len(os.fsencode(self.sock_path)), MAX_PATH, self.sock_path))
        os.makedirs(self.guard_dir, mode=0o700, exist_ok=True)
        try:
            os.unlink(self.sock_path)
        except FileNotFoundError:
            pass
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.bind(self.sock_path)
            os.chmod(self.sock_path, 0o600)
            s.listen(32)
            s.settimeout(0.1)
        except BaseException:
            s.close()
            raise
        self._listener = s

    def serve(self):
        self._accept = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept.start()

    def stop(self):
        self._stopped.set()
        if self._accept is not None:
            self._accept.join()
            self._accept = None
        with self._lock:
            workers, self._workers = self._workers, []
        for w in workers:
            w.join()
        if self._listener is not None:
            self._listener.close()
            self._listener = None
            try:
                os.unlink(self.sock_path)
            except FileNotFoundError:
                pass

    def counts(self):
        with self._lock:
            return {"calls": self._calls, "decisions": dict(self._by_decision), "gray": self._gray,
                    "latency_ms": {"p50": _pct(self._latency, 0.5), "p95": _pct(self._latency, 0.95)}}

    def _accept_loop(self):
        while not self._stopped.is_set():
            if not self._sem.acquire(timeout=0.1):
                continue
            try:
                conn, _ = self._listener.accept()
            except socket.timeout:
                self._sem.release()
                continue
            except OSError:
                self._sem.release()
                return
            t = threading.Thread(target=self._work, args=(conn,), daemon=True)
            with self._lock:
                self._workers = [w for w in self._workers if w.is_alive()]
                self._workers.append(t)
            t.start()

    def _work(self, conn):
        try:
            self._handle(conn)
        except Exception:
            pass
        finally:
            conn.close()
            self._sem.release()

    def _read_line(self, conn):
        """Returns (line_bytes, None) or (None, error)."""
        end = time.monotonic() + READ_DEADLINE
        buf = b""
        while b"\n" not in buf:
            if len(buf) > MAX_REQUEST:
                self._drain(conn, end)
                return None, "request too large"
            left = end - time.monotonic()
            if left <= 0:
                return None, "request timed out"
            conn.settimeout(left)
            try:
                chunk = conn.recv(65536)
            except (socket.timeout, OSError):
                return None, "request timed out"
            if not chunk:
                return None, "no request"
            buf += chunk
        line = buf.split(b"\n", 1)[0]
        if len(line) > MAX_REQUEST:
            return None, "request too large"
        return line, None

    @staticmethod
    def _drain(conn, end):
        """Let an oversized sender finish writing so it can read the refusal."""
        while time.monotonic() < end:
            conn.settimeout(max(0.01, end - time.monotonic()))
            try:
                if not conn.recv(1 << 20):
                    return
            except (socket.timeout, OSError):
                return

    @staticmethod
    def _send(conn, obj):
        conn.settimeout(1.0)
        data = obj if isinstance(obj, bytes) else json.dumps(obj).encode() + b"\n"
        try:
            conn.sendall(data)
        except OSError:
            pass

    def _handle(self, conn):
        line, err = self._read_line(conn)
        if err:
            return self._send(conn, {"v": 1, "error": err})
        try:
            req = json.loads(line)
        except ValueError:
            return self._send(conn, {"v": 1, "error": "malformed request"})
        if not isinstance(req, dict) or req.get("v") != 1:
            return self._send(conn, {"v": 1, "error": "unsupported version"})
        tok = req.get("token")
        if not isinstance(tok, str) or not hmac.compare_digest(tok.encode(), self.token.encode()):
            return self._send(conn, {"v": 1, "error": "bad token"})
        if req.get("runtime") != self.session["runtime"]:
            return self._send(conn, {"v": 1, "error": "runtime mismatch"})
        cwd = req.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            return self._send(conn, {"v": 1, "error": "bad cwd"})
        t0 = time.monotonic()
        call = normalise(self.session["runtime"], req.get("payload"), cwd, self.ctx.worktree)
        d = decide(call, self.ctx)
        latency = round((time.monotonic() - t0) * 1000, 3)
        text = _text(call)
        self_test = bool(self._marker) and self._marker in text
        did = uuid.uuid4().hex[:12]
        outcome = "shadow-allowed" if self.mode == "shadow" else OUTCOME.get(d.decision, "asked")
        s = self.session
        ev = {"type": "decision", "id": did, "session_id": s["session_id"], "project": s["project"],
              "profile": s["profile"], "lead": s["lead"], "runtime": s["runtime"],
              "runtime_version": s["runtime_version"], "tool": call.tool, "kind": call.kind,
              "call": redact(text), "digest": digest(call), "source": d.source, "rule": d.rule,
              "decision": d.decision, "reason": redact(d.reason or ""), "latency_ms": latency,
              "rules_version": self.rules_v, "guard_version": self.guard_v, "policy_digest": self.policy,
              "mode": self.mode, "outcome": outcome, "judge_model": None, "prompt_hash": None,
              "self_test": self_test}
        record.append(self.state_dir, ev)
        if not self_test:
            with self._lock:
                self._calls += 1
                self._by_decision[d.decision] = self._by_decision.get(d.decision, 0) + 1
                self._gray += d.source == "gray"
                self._latency.append(latency)
        if self.reply_garbage:
            return self._send(conn, b"garbage, not json\n")
        shown = "allow" if self.mode == "shadow" else d.decision
        self._send(conn, {"v": 1, "id": did, "decision": shown, "reason": d.reason})


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m lead.guard.broker")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("serve")
    sv.add_argument("--state-dir", required=True)
    sv.add_argument("--guard-dir", required=True)
    sv.add_argument("--worktree", required=True)
    sv.add_argument("--runtime", required=True)
    sv.add_argument("--mode", required=True, choices=("shadow", "acting"))
    sv.add_argument("--token-env", required=True)
    sv.add_argument("--reply-garbage", action="store_true")
    a = ap.parse_args(argv)
    token = os.environ.get(a.token_env)
    if not token:
        sys.exit("token env %s is empty" % a.token_env)
    wt = os.path.realpath(a.worktree)
    ctx = RuleContext(worktree=wt, git_dir=os.path.join(wt, ".git"), common_dir=os.path.join(wt, ".git"),
                      home=os.path.expanduser("~"), protected_branches=["main", "master"],
                      deploy_commands=[], push_dest=lambda remote, refspec: None,
                      is_pristine=lambda p: False)
    session = {"session_id": "standalone", "project": wt, "profile": "standalone", "lead": "standalone",
               "runtime": a.runtime, "runtime_version": "standalone"}
    b = Broker(a.state_dir, a.guard_dir, token, a.mode, session, ctx, reply_garbage=a.reply_garbage)
    b.bind()
    done = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: done.set())
    b.serve()
    print("ready", flush=True)
    done.wait()
    b.stop()


if __name__ == "__main__":
    main()
