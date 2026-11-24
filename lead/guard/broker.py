"""broker.py — one decision per tool call, over a socket no lead can write, logged to guard.jsonl.

Wire: a client sends one JSON line (<= 1 MiB) {"v":1,"token","runtime","cwd","payload"} and gets one
line back, {"v":1,"id","decision","reason"} or {"v":1,"error":...}. In shadow the reply is always
allow while the record keeps the real decision; in acting the reply is the decision. The record keeps
the final decision (`decision`) and the rules' own (`rule_decision`) apart; `source` says who decided.
An error reply writes no record. In acting the record is written before the reply; in shadow a gray
call's record follows the reply once judged. A shadow gray call goes to one judge thread through a
queue of SHADOW_QUEUE items. A full queue, or a stop with items still queued, records them as
`judge-skipped` without a model call. Without a judge, a gray call is recorded at once as
`judge-skipped`. Every judge failure or skip records the rules' own decision (judging.final_decision).
The acting judge arrives with the bounded waits; until then an acting gray call is `judge-skipped`.
"""
import argparse
import collections
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

from lead.guard import guard_version, judging, record, rules_version  # noqa: E402
from lead.guard.judge import call_text  # noqa: E402
from lead.guard.normalise import normalise  # noqa: E402
from lead.guard.redact import digest, redact  # noqa: E402
from lead.guard.rules import RuleContext, decide  # noqa: E402

MAX_REQUEST = 1024 * 1024
MAX_PATH = 103  # sun_path is 104 bytes on macOS, NUL included
MAX_WORKERS = 8
READ_DEADLINE = 0.5
OUTCOME = {"allow": "allowed", "block": "blocked", "ask": "asked"}
SHADOW_QUEUE = 32
JUDGE_COUNT = {"judge": "judged", "judge-failed": "failed", "judge-skipped": "skipped"}


def _pct(vals, q):
    if not vals:
        return 0.0
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(round(q * (len(vals) - 1))))]


class Broker:
    def __init__(self, state_dir, guard_dir, token, mode, session, ctx, reply_garbage=False,
                 judge_model=None, prompt_hash=None, judge=None, notice=None):
        self.state_dir, self.guard_dir, self.token = state_dir, guard_dir, token
        self.mode, self.session, self.ctx = mode, dict(session), ctx
        self.reply_garbage = reply_garbage
        self.judge, self._raw_notice = judge, notice
        if judge is not None:  # B15: the judge names its own model and prompt; B6: it notices through us
            judge_model = judge.cfg.get("model") if judge_model is None else judge_model
            prompt_hash = judge.prompt_hash if prompt_hash is None else prompt_hash
            judge.notice = self._notice
        self.judge_model, self.prompt_hash = judge_model, prompt_hash  # on every row, judged or not
        self.sock_path = os.path.join(guard_dir, "sock")
        self.policy = record.policy_digest(ctx.protected_branches, ctx.deploy_commands)
        self.rules_v, self.guard_v = rules_version(), guard_version()
        self._listener = None
        self._accept = None
        self._workers = []
        self._stopped = threading.Event()
        self._paused = threading.Event()
        self._lock = threading.Lock()
        self._sem = threading.Semaphore(MAX_WORKERS)
        self._marker = None
        self._calls = 0
        self._by_decision = {}
        self._gray = 0
        self._latency = []
        self._judge_counts = {"judged": 0, "allowed": 0, "failed": 0, "skipped": 0}
        self._queue = collections.deque()
        self._qcond = threading.Condition()
        self._judge_thread = None

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
            try:
                os.unlink(self.sock_path)  # bound but not usable: do not leave it for the next start to trip on
            except OSError:
                pass
            raise
        self._listener = s

    def serve(self):
        self._paused.clear()
        if self.judge is not None and self.mode == "shadow":
            self._judge_thread = threading.Thread(target=self._judge_loop, daemon=True)
            self._judge_thread.start()
        self._accept = threading.Thread(target=self._accept_loop, daemon=True)
        self._accept.start()

    def pause(self):
        """Stop serving but keep the socket listening: no thread of ours is alive (bw forks the
        lead now), and a connection made meanwhile waits in the backlog until serve() resumes."""
        self._paused.set()
        self._join_threads()

    def _join_threads(self):
        if self._accept is not None:
            self._accept.join()
            self._accept = None
        with self._lock:
            workers, self._workers = self._workers, []
        for w in workers:
            w.join()
        with self._qcond:  # no worker is left to queue more; wake the judge thread to see the halt
            self._qcond.notify_all()
        if self._judge_thread is not None:
            self._judge_thread.join()  # an in-flight child is bounded by its own timeout
            self._judge_thread = None

    def stop(self):
        self._stopped.set()
        self._join_threads()
        while True:  # still queued at the end: recorded without a model call
            with self._qcond:
                if not self._queue:
                    break
                item = self._queue.popleft()
            self._judged(item, judging.JudgeResult("judge-skipped", None, "session ended", None, 0))
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
                    "latency_ms": {"p50": _pct(self._latency, 0.5), "p95": _pct(self._latency, 0.95)},
                    "judge": dict(self._judge_counts)}

    def _halted(self):
        return self._stopped.is_set() or self._paused.is_set()

    def _notice(self, kind, text):
        """The runner's notice callback, made safe for a judge thread: never raises, never prints."""
        try:
            if self._raw_notice is not None:
                self._raw_notice(kind, text)
        except Exception as exc:
            self._error("notice", exc)

    def _error(self, where, exc):
        try:
            record.append(self.state_dir, {
                "type": "error", "session_id": self.session["session_id"], "where": where,
                "error": "%s: %s" % (type(exc).__name__, redact(str(exc))[:300])})
        except Exception:
            pass

    def _judge_loop(self):
        """One thread: judge queued shadow calls in order and record each. It stops on pause() or
        stop() between items; items it has not taken stay queued."""
        while True:
            with self._qcond:
                while not self._queue and not self._halted():
                    self._qcond.wait(0.1)
                if self._halted():
                    return
                item = self._queue.popleft()
            try:
                result = self.judge.judge(item[1], item[0]["digest"], judging.SHADOW_BUDGET_S)
            except Exception as exc:  # the judge is untrusted code: its bug keeps the rules' decision
                result = judging.JudgeResult("judge-failed", None, type(exc).__name__, None, 0)
            self._judged(item, result)

    def _judged(self, item, result):
        try:
            self._record(*item, result)
        except Exception as exc:
            self._error("record", exc)

    def _accept_loop(self):
        while not (self._stopped.is_set() or self._paused.is_set()):
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
        except Exception:  # _handle reports its own failures; this is the last resort
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
        where = "decide"
        try:
            t0 = time.monotonic()
            call = normalise(self.session["runtime"], req.get("payload"), cwd, self.ctx.worktree)
            d = decide(call, self.ctx)
            where = "record"
            self._finish(conn, call, d, t0)
        except Exception as exc:
            self._fail(conn, where, exc)

    def _fail(self, conn, where, exc):
        """Reply with an error and leave one error record (no call text, no stderr)."""
        self._error(where, exc)
        self._send(conn, {"v": 1, "error": "internal error"})

    def _finish(self, conn, call, d, t0):
        latency = round((time.monotonic() - t0) * 1000, 3)
        text = call_text(call)
        with self._lock:  # one-shot: the first matching call spends the marker
            self_test = bool(self._marker) and self._marker in text
            if self_test:
                self._marker = None
        did = uuid.uuid4().hex[:12]
        s = self.session
        ev = {"type": "decision", "id": did, "session_id": s["session_id"], "project": s["project"],
              "profile": s["profile"], "lead": s["lead"], "runtime": s["runtime"],
              "runtime_version": s["runtime_version"], "tool": call.tool, "kind": call.kind,
              "call": redact(text), "digest": digest(call), "rule": d.rule,
              "rule_decision": d.decision, "reason": redact(d.reason or ""),
              "latency_ms": latency, "rules_version": self.rules_v, "guard_version": self.guard_v,
              "policy_digest": self.policy, "mode": self.mode,
              "judge_model": self.judge_model, "prompt_hash": self.prompt_hash, "self_test": self_test}
        # rules.decide gives source "gray" (always with decision "ask" and a gray:* rule) or "rule".
        gray = d.source == "gray"
        if gray and self.mode == "shadow" and self.judge is not None and not self_test:
            with self._qcond:
                queued = len(self._queue) < SHADOW_QUEUE
                if queued:
                    self._queue.append((ev, call, d))
                    self._qcond.notify()
            if not queued:
                self._record(ev, call, d, judging.JudgeResult("judge-skipped", None, "queue full", None, 0))
        else:
            self._record(ev, call, d, judging.JudgeResult("judge-skipped", None, "no judge", None, 0) if gray else None)
        if self.reply_garbage:
            return self._send(conn, b"garbage, not json\n")
        # the self-test must see what the rules decided, whatever the mode; a queued call is shadow
        shown = d.decision if self_test else "allow" if self.mode == "shadow" else ev["decision"]
        self._send(conn, {"v": 1, "id": did, "decision": shown, "reason": d.reason})

    def _record(self, ev, call, d, result):
        """Fill in who decided and the final decision, append the row, and count it."""
        gray = d.source == "gray"
        final = judging.final_decision(d.decision, gray, result)
        ev.update({"source": result.source if gray else "rule", "decision": final,
                   "outcome": "shadow-allowed" if self.mode == "shadow" else OUTCOME.get(final, "asked"),
                   "judge_reason": redact(result.reason) if gray and result.reason else None,
                   "judge_ms": result.ms if gray else None})
        record.append(self.state_dir, ev)
        if ev["self_test"]:
            return
        with self._lock:
            self._calls += 1
            self._by_decision[final] = self._by_decision.get(final, 0) + 1
            self._gray += record.is_gray(ev)
            self._latency.append(ev["latency_ms"])
            if gray:
                self._judge_counts[JUDGE_COUNT[result.source]] += 1
                self._judge_counts["allowed"] += result.source == "judge" and final == "allow"


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
    sv.add_argument("--checkpoint-dir", default=None)
    a = ap.parse_args(argv)
    token = os.environ.get(a.token_env)
    if not token:
        sys.exit("token env %s is empty" % a.token_env)
    from lead.guard import launch  # lazy: launch imports the rules, which the broker module does not need
    wt = os.path.realpath(a.worktree)
    head = launch.head_of(wt)  # "unchanged" means unchanged since the broker started
    ctx = RuleContext(worktree=wt, git_dir=os.path.join(wt, ".git"), common_dir=os.path.join(wt, ".git"),
                      home=os.path.expanduser("~"), protected_branches=["main", "master"],
                      deploy_commands=[], push_dest=lambda remote, refspec: None,
                      is_pristine=launch.pristine_checker(wt, head), checkpoint_dir=a.checkpoint_dir)
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
