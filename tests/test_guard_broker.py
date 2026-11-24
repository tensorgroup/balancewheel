import contextlib
import io
import json
import os
import shutil
import socket
import stat
import tempfile
import threading
import time
import unittest
from unittest import mock

from lead import proc
from lead.guard import broker as brk
from lead.guard import judging, record
from lead.guard.rules import RuleContext

store = record.store
TOKEN = "t0k3n"
SESSION = {"session_id": "s1", "project": "wt", "profile": "dev", "lead": "pi-lead",
           "runtime": "pi", "runtime_version": "1.2.3"}


def _no_network(*a, **k):
    raise AssertionError("a broker test tried to open a real connection")


_NET = mock.patch("urllib.request.build_opener", _no_network)


def setUpModule():  # every judge here is a fake, a fake run, or a `sleep` child: none reaches the network
    _NET.start()


def tearDownModule():
    _NET.stop()


def bash(cmd):
    return {"toolName": "bash", "input": {"command": cmd}}


class BrokerBase(unittest.TestCase):
    mode = "acting"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir="/tmp")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.wt = os.path.join(self.tmp, "wt")
        os.makedirs(self.wt)
        self.state = os.path.join(self.tmp, "state")
        self.gdir = os.path.join(self.tmp, "g")
        self.base_threads = threading.active_count()
        self.b = self.start()

    def ctx(self):
        return RuleContext(worktree=self.wt, git_dir=os.path.join(self.wt, ".git"),
                           common_dir=os.path.join(self.wt, ".git"), home=self.tmp,
                           protected_branches=["main", "master"], deploy_commands=["make deploy"],
                           push_dest=lambda r, s: None, is_pristine=lambda p: False)

    def start(self, mode=None, session=None, **kw):
        b = brk.Broker(self.state, self.gdir, TOKEN, mode or self.mode, session or dict(SESSION), self.ctx(), **kw)
        b.bind()
        b.serve()
        self.addCleanup(self.stop, b)
        return b

    def stop(self, b):
        b.stop()

    def tearDown(self):
        self.b.stop()
        self.assertEqual(threading.active_count(), self.base_threads)

    def raw(self, data, read=True, timeout=3):
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(timeout)
        try:
            s.connect(os.path.join(self.gdir, "sock"))
            try:
                s.sendall(data)
            except OSError:
                pass
            if not read:
                return None
            buf = b""
            try:
                while not buf.endswith(b"\n"):
                    c = s.recv(65536)
                    if not c:
                        break
                    buf += c
            except OSError:
                pass
            return json.loads(buf) if buf.strip() else None
        finally:
            s.close()

    def ask(self, payload, runtime="pi", token=TOKEN, v=1):
        req = {"v": v, "token": token, "runtime": runtime, "cwd": self.wt, "payload": payload}
        return self.raw(json.dumps(req).encode() + b"\n")

    def decisions(self):
        return record.load(self.state)["decisions"]


class TestBroker(BrokerBase):
    def test_block_in_acting_logged(self):
        r = self.ask(bash("cat ~/.ssh/id_ed25519"))
        self.assertEqual(r["v"], 1)
        self.assertEqual(r["decision"], "block")
        self.assertTrue(r["reason"])
        d = self.decisions()
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["decision"], "block")
        self.assertEqual(d[0]["outcome"], "blocked")
        self.assertEqual(d[0]["id"], r["id"])
        self.assertEqual(d[0]["mode"], "acting")
        for k in ("type", "ts", "session_id", "project", "profile", "lead", "runtime", "runtime_version",
                  "tool", "kind", "call", "digest", "source", "rule", "reason", "latency_ms",
                  "rules_version", "guard_version", "policy_digest"):
            self.assertIn(k, d[0])
        self.assertIsNone(d[0]["judge_model"])
        self.assertIsNone(d[0]["prompt_hash"])
        self.assertEqual(len(d[0]["id"]), 12)
        self.assertEqual(len(d[0]["policy_digest"]), 12)
        self.assertFalse(d[0]["self_test"])

    def test_allow_outcome_in_acting(self):
        r = self.ask(bash("ls"))
        self.assertEqual(r["decision"], "allow")
        self.assertEqual(self.decisions()[0]["outcome"], "allowed")

    def test_ask_outcome_in_acting(self):
        r = self.ask({"toolName": "mystery", "input": {}})
        self.assertEqual(r["decision"], "ask")
        self.assertEqual(self.decisions()[0]["outcome"], "asked")

    def test_bad_requests_get_errors_and_no_record(self):
        for r in (self.ask(bash("ls"), token="nope"),
                  self.raw(b"{not json\n"),
                  self.ask(bash("ls"), v=2),
                  self.ask(bash("ls"), runtime="agy")):
            self.assertIn("error", r)
            self.assertEqual(r["v"], 1)
        self.assertEqual(self.decisions(), [])
        self.assertEqual(self.b.counts()["calls"], 0)

    def test_300kib_payload_is_oversized_other(self):
        r = self.ask({"toolName": "bash", "input": {"command": "x" * (300 * 1024)}})
        self.assertIn("decision", r)
        d = self.decisions()[0]
        self.assertEqual((d["kind"], d["tool"]), ("other", "oversized"))

    def test_2mib_request_refused(self):
        req = {"v": 1, "token": TOKEN, "runtime": "pi", "cwd": self.wt,
               "payload": bash("x" * (2 * 1024 * 1024))}
        r = self.raw(json.dumps(req).encode() + b"\n")
        self.assertEqual(r, {"v": 1, "error": "request too large"})
        self.assertEqual(self.decisions(), [])

    def test_long_socket_path_raises_before_binding(self):
        long_dir = os.path.join(self.tmp, "d" * 100)
        b = brk.Broker(self.state, long_dir, TOKEN, "acting", dict(SESSION), self.ctx())
        with self.assertRaises(ValueError) as cm:
            b.bind()
        self.assertIn("sock", str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(long_dir, "sock")))

    def test_stale_socket_replaced(self):
        self.b.stop()
        os.makedirs(self.gdir, exist_ok=True)
        stale = socket.socket(socket.AF_UNIX)
        stale.bind(os.path.join(self.gdir, "sock"))
        stale.close()
        self.b = self.start()
        self.assertEqual(self.ask(bash("ls"))["decision"], "allow")

    def test_modes_0600(self):
        self.ask(bash("ls"))
        for p in (os.path.join(self.gdir, "sock"), record.path(self.state)):
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600, p)

    def test_stalled_clients_do_not_starve(self):
        socks = []
        for _ in range(2):
            s = socket.socket(socket.AF_UNIX)
            s.connect(os.path.join(self.gdir, "sock"))
            socks.append(s)
        try:
            t = time.time()
            r = self.ask(bash("ls"))
            self.assertLess(time.time() - t, 1.0)
            self.assertEqual(r["decision"], "allow")
        finally:
            for s in socks:
                s.close()

    def test_twenty_concurrent(self):
        out = []
        lock = threading.Lock()

        def go(i):
            r = self.ask(bash("echo %d" % i))
            with lock:
                out.append(r)
        ts = [threading.Thread(target=go, args=(i,)) for i in range(20)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(len(out), 20)
        self.assertTrue(all(r and r.get("decision") == "allow" for r in out))
        loaded = record.load(self.state)
        self.assertEqual(len(loaded["decisions"]), 20)
        self.assertEqual(loaded["warnings"], [])

    def test_stop_during_inflight_request(self):
        real = brk.decide
        started = threading.Event()

        def slow(call, ctx):
            started.set()
            time.sleep(0.4)
            return real(call, ctx)
        out = []
        with mock.patch.object(brk, "decide", slow):
            t = threading.Thread(target=lambda: out.append(self.ask(bash("ls"))))
            t.start()
            self.assertTrue(started.wait(2))
            self.b.stop()
            t.join()
        self.assertEqual(len(self.decisions()), 1)
        self.assertEqual(self.b.counts()["calls"], 1)
        self.assertEqual(out[0]["decision"], "allow")
        self.assertFalse(os.path.exists(os.path.join(self.gdir, "sock")))

    def test_redaction_and_digest(self):
        self.ask(bash("echo TOKEN=abc123"))
        d = self.decisions()[0]
        self.assertNotIn("abc123", json.dumps(d))
        from lead.guard.normalise import normalise
        from lead.guard.redact import digest
        self.assertEqual(d["digest"], digest(normalise("pi", bash("echo TOKEN=abc123"), self.wt, self.wt)))
        self.assertNotEqual(d["digest"], digest(normalise("pi", bash(d["call"]), self.wt, self.wt)))

    def test_counts(self):
        self.ask(bash("ls"))
        self.ask(bash("cat ~/.ssh/id_ed25519"))
        self.ask({"toolName": "mystery", "input": {}})
        self.b.stop()
        c = self.b.counts()
        self.assertEqual(c["calls"], 3)
        self.assertEqual(c["decisions"], {"allow": 1, "block": 1, "ask": 1})
        self.assertEqual(c["gray"], 1)
        self.assertLessEqual(c["latency_ms"]["p50"], c["latency_ms"]["p95"])

    def test_self_test_marker(self):
        self.b.arm_self_test("MARKER-9f2")
        self.ask({"toolName": "mystery", "input": {"x": "MARKER-9f2"}})  # the non-shell text form matches too
        self.ask(bash("ls"))
        self.b.stop()
        d = self.decisions()
        self.assertEqual([x["self_test"] for x in d], [True, False])
        self.assertEqual(self.b.counts()["calls"], 1)
        self.assertEqual(self.b.counts()["decisions"], {"allow": 1})


class TestPauseAndSelfTestReply(BrokerBase):
    mode = "shadow"

    def test_pause_keeps_the_socket_and_resume_answers_a_waiting_connection(self):
        self.b.pause()
        self.assertEqual(threading.active_count(), self.base_threads)
        self.assertTrue(os.path.exists(os.path.join(self.gdir, "sock")))
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(5)
        s.connect(os.path.join(self.gdir, "sock"))
        req = {"v": 1, "token": TOKEN, "runtime": "pi", "cwd": self.wt, "payload": bash("ls")}
        s.sendall(json.dumps(req).encode() + b"\n")
        time.sleep(0.3)
        self.assertEqual(self.decisions(), [])  # nobody is serving while paused
        self.b.serve()
        buf = b""
        while not buf.endswith(b"\n"):
            buf += s.recv(65536)
        s.close()
        self.assertEqual(json.loads(buf)["decision"], "allow")
        self.assertEqual(len(self.decisions()), 1)

    def test_self_test_call_gets_the_real_decision_in_shadow(self):
        self.b.arm_self_test("bw-self-test-abc")
        r = self.ask(bash("herdr pane send bw-self-test-abc"))
        self.assertEqual(r["decision"], "block")
        r = self.ask(bash("herdr pane send other"))
        self.assertEqual(r["decision"], "allow")  # shadow still allows everything else

    def test_self_test_marker_is_one_shot(self):
        self.b.arm_self_test("bw-self-test-abc")
        self.assertEqual(self.ask(bash("herdr pane send bw-self-test-abc"))["decision"], "block")
        r = self.ask(bash("herdr pane send bw-self-test-abc"))
        self.assertEqual(r["decision"], "allow")  # an ordinary call in shadow
        d = self.decisions()
        self.assertEqual([x["self_test"] for x in d], [True, False])
        self.assertEqual(self.b.counts()["calls"], 1)
        self.assertEqual(self.b.counts()["decisions"], {"block": 1})


class TestWorkerErrors(BrokerBase):
    def ctx(self):
        def boom(remote, refspec):
            raise RuntimeError("push_dest exploded TOKEN=abc123")
        return super().ctx()._replace(push_dest=boom)

    def test_decide_error_replies_and_records(self):
        r = self.ask(bash("git push"))
        self.assertEqual(r, {"v": 1, "error": "internal error"})
        loaded = record.load(self.state)
        self.assertEqual(loaded["decisions"], [])
        self.assertEqual(len(loaded["errors"]), 1)
        e = loaded["errors"][0]
        self.assertEqual((e["type"], e["where"], e["session_id"]), ("error", "decide", "s1"))
        self.assertTrue(e["error"].startswith("RuntimeError: push_dest exploded"))
        self.assertNotIn("abc123", e["error"])
        self.assertNotIn("git push", json.dumps(e))

    def test_record_error_replies_and_records_where_record(self):
        with mock.patch.object(brk.record, "append", side_effect=[OSError("disk"), None]) as ap:
            r = self.ask(bash("ls"))
        self.assertEqual(r, {"v": 1, "error": "internal error"})
        self.assertEqual(ap.call_args_list[1][0][1]["where"], "record")

    def test_error_record_failure_swallowed(self):
        with mock.patch.object(brk.record, "append", side_effect=OSError("disk")):
            r = self.ask(bash("ls"))
        self.assertEqual(r, {"v": 1, "error": "internal error"})


class TestShadow(BrokerBase):
    mode = "shadow"

    def test_shadow_allows_but_logs_real_decision(self):
        r = self.ask(bash("cat ~/.ssh/id_ed25519"))
        self.assertEqual(r["decision"], "allow")
        d = self.decisions()[0]
        self.assertEqual(d["decision"], "block")
        self.assertEqual(d["outcome"], "shadow-allowed")
        self.assertEqual(d["mode"], "shadow")

    def test_gray_call_records_judge_skipped_without_judge(self):
        r = self.ask(bash("jq ."))
        self.assertEqual(r["decision"], "allow")
        d = self.decisions()[0]
        self.assertEqual(d["source"], "judge-skipped")
        self.assertEqual((d["rule_decision"], d["decision"]), ("ask", "ask"))
        self.assertEqual(d["judge_reason"], "no judge")
        self.assertIsNone(d["judge_ms"])
        self.assertIsNone(d["judge_model"])
        self.assertTrue(d["rule"].startswith("gray:"))
        self.assertEqual(d["outcome"], "shadow-allowed")

    def test_rule_call_records_rule_source(self):
        self.ask(bash("herdr pane send x"))
        d = self.decisions()[0]
        self.assertEqual(d["source"], "rule")
        self.assertEqual((d["rule_decision"], d["decision"]), ("block", "block"))
        self.assertIsNone(d["judge_reason"])
        self.assertIsNone(d["judge_ms"])

    def test_configured_judge_model_is_on_every_row(self):
        self.b.stop()
        self.b = brk.Broker(self.state, self.gdir, TOKEN, "shadow", dict(SESSION), self.ctx(),
                            judge_model="test/judge", prompt_hash="h1")
        self.b.bind()
        self.b.serve()
        self.ask(bash("herdr pane send x"))
        self.ask(bash("jq ."))
        self.assertEqual([(d["judge_model"], d["prompt_hash"]) for d in self.decisions()],
                         [("test/judge", "h1")] * 2)


JUDGE_ALLOW = judging.JudgeResult("judge", "allow", "local read", 7, 1)


class FakeJudge:
    """Stands in for a JudgeRunner: the broker reads cfg and prompt_hash, and sets notice."""

    def __init__(self, result=JUDGE_ALLOW, sleep=0.0):
        self.cfg = {"model": "test/judge", "key_env": "BW_TEST_JUDGE_KEY"}
        self.prompt_hash = "ph1"
        self.notice = None
        self.result, self.sleep = result, sleep
        self.calls, self.budgets, self.rules = 0, [], []

    def judge(self, call, digest, budget_s, rule=None):
        self.calls += 1
        self.rules.append(rule)
        self.budgets.append(budget_s)
        time.sleep(self.sleep)
        return self.result


def timed_out(argv, timeout, **kw):
    return proc.Result(124, "", "", True)


def wait_for(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


class ShadowJudgeTests(BrokerBase):
    mode = "shadow"

    def use(self, judge, **kw):
        self.b.stop()
        self.b = self.start(judge=judge, **kw)
        return self.b

    def runner(self, **kw):
        return judging.JudgeRunner({"model": "test/judge", "key_env": "BW_TEST_JUDGE_KEY"}, self.wt, self.tmp, **kw)

    def judged(self, n=1):
        """Wait until the judge thread has recorded n rows, so stop() finds nothing queued."""
        self.assertTrue(wait_for(lambda: len(self.decisions()) == n))

    def assert_floor(self, rows):
        """final_decision is never weaker than rule_decision: only a judge verdict may differ from it."""
        for d in rows:
            if d["source"] != "judge":
                self.assertEqual(d["decision"], d["rule_decision"], d)

    def test_shadow_replies_before_judging(self):
        self.use(FakeJudge(sleep=1.0))
        t0 = time.monotonic()
        r = self.ask(bash("jq . f.json"))
        self.assertLess(time.monotonic() - t0, 0.3)
        self.assertEqual(r["decision"], "allow")
        self.assertEqual(self.decisions(), [])  # the record follows the judge

    def test_record_follows_with_judge_fields(self):
        j = FakeJudge()
        self.use(j)
        r = self.ask(bash("jq . f.json"))
        self.judged()
        self.b.stop()
        (d,) = self.decisions()
        self.assertEqual(d["id"], r["id"])
        self.assertEqual((d["source"], d["decision"], d["rule_decision"]), ("judge", "allow", "ask"))
        self.assertEqual((d["judge_model"], d["prompt_hash"]), ("test/judge", "ph1"))
        self.assertEqual((d["judge_reason"], d["judge_ms"]), ("local read", 7))
        self.assertEqual((d["outcome"], d["mode"]), ("shadow-allowed", "shadow"))
        self.assertTrue(d["rule"].startswith("gray:"))
        self.assertEqual(len(j.budgets), 1)  # the acting budget, as if this call had been judged inline
        self.assertAlmostEqual(j.budgets[0], judging.acting_budget(d["latency_ms"] / 1000), places=6)

    def test_judge_ask_is_recorded_as_ask(self):
        self.use(FakeJudge(result=judging.JudgeResult("judge", "ask", "unsure", 9, 1)))
        self.ask(bash("jq . f.json"))
        self.judged()
        self.b.stop()
        (d,) = self.decisions()
        self.assertEqual((d["source"], d["decision"], d["judge_reason"]), ("judge", "ask", "unsure"))

    def test_judge_failure_records_the_floor(self):
        self.use(FakeJudge(result=judging.JudgeResult("judge-failed", None, "timeout", 1350, 1)))
        self.ask(bash("jq . f.json"))
        self.judged()
        self.b.stop()
        (d,) = self.decisions()
        self.assertEqual((d["source"], d["judge_reason"], d["judge_ms"]), ("judge-failed", "timeout", 1350))
        self.assert_floor([d])

    def test_a_judge_that_raises_records_the_floor_and_an_error(self):
        class Boom(FakeJudge):
            def judge(self, call, digest, budget_s, rule=None):
                raise RuntimeError("judge bug")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.use(Boom())
            self.ask(bash("jq . f.json"))
            self.judged()
            self.b.stop()
        self.assertEqual(err.getvalue(), "")
        (d,) = self.decisions()
        self.assertEqual((d["source"], d["judge_reason"]), ("judge-failed", "RuntimeError"))
        self.assert_floor([d])

    def test_rule_calls_never_reach_the_judge(self):
        j = FakeJudge()
        self.use(j)
        r = self.ask(bash("herdr pane send x"))
        self.assertEqual(r["decision"], "allow")
        (d,) = self.decisions()  # a rule call's record is written before the reply, as before
        self.assertEqual((d["source"], d["decision"]), ("rule", "block"))
        self.b.stop()
        self.assertEqual(j.calls, 0)

    def test_burst_fills_queue_and_never_delays_replies(self):
        self.use(FakeJudge(sleep=0.2))
        t0 = time.monotonic()
        ids = [self.ask(bash("jq . f%d.json" % i))["id"] for i in range(40)]
        self.assertLess(time.monotonic() - t0, 1.0)
        self.b.stop()
        rows = self.decisions()
        self.assertEqual(sorted(d["id"] for d in rows), sorted(ids))
        reasons = [d["judge_reason"] for d in rows]
        self.assertIn("queue full", reasons)
        self.assertIn("session ended", reasons)
        self.assert_floor(rows)
        for d in rows:
            if d["judge_reason"] in ("queue full", "session ended"):
                self.assertEqual((d["source"], d["decision"]), ("judge-skipped", "ask"))

    def test_stop_records_queued_items_as_session_ended_and_joins(self):
        j = FakeJudge(sleep=0.5)
        self.use(j)
        for i in range(3):
            self.ask(bash("jq . f%d.json" % i))
        self.assertTrue(wait_for(lambda: j.calls == 1))
        self.b.stop()
        self.assertEqual(threading.active_count(), self.base_threads)
        rows = self.decisions()
        self.assertEqual(sorted(d["judge_reason"] for d in rows), ["local read", "session ended", "session ended"])
        self.assertEqual(j.calls, 1)  # no model call for an item still queued
        self.assert_floor(rows)

    def test_stop_waits_for_a_real_child_within_its_timeout(self):  # B6
        jr = self.runner()
        jr.argv = ["sleep", "5"]  # a stalled child; the real proc.run kills its group at the budget
        with mock.patch.object(judging, "acting_budget", lambda rules_s: 0.5):  # one attempt, no time to retry
            self.use(jr)
            self.ask(bash("jq . f.json"))
            self.assertTrue(wait_for(lambda: not self.b._queue))  # the judge thread has taken the item
            time.sleep(0.05)
            t0 = time.monotonic()
            self.b.stop()
            elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 1.5)
        self.assertEqual(threading.active_count(), self.base_threads)
        (d,) = self.decisions()
        self.assertEqual((d["source"], d["judge_reason"]), ("judge-failed", "timeout"))
        self.assert_floor([d])

    def test_no_judge_records_synchronously(self):  # B15
        r = self.ask(bash("jq . f.json"))
        (d,) = self.decisions()  # the row exists when the reply arrives
        self.assertEqual((d["id"], d["source"], d["judge_reason"]), (r["id"], "judge-skipped", "no judge"))
        self.assert_floor([d])

    def test_pause_joins_the_judge_thread(self):
        j = FakeJudge()
        self.use(j)
        self.b.pause()
        self.assertEqual(threading.active_count(), self.base_threads)
        self.b.serve()
        self.ask(bash("jq . f.json"))
        self.assertTrue(wait_for(lambda: j.calls == 1))
        self.b.pause()
        self.assertEqual(threading.active_count(), self.base_threads)
        self.b.serve()

    def test_pause_keeps_queued_items_for_the_next_serve(self):
        j = FakeJudge(sleep=0.3)
        self.use(j)
        for i in range(3):
            self.ask(bash("jq . f%d.json" % i))
        self.assertTrue(wait_for(lambda: j.calls == 1))
        self.b.pause()  # the item in flight finishes; the two still queued stay queued
        self.assertEqual(threading.active_count(), self.base_threads)
        self.assertEqual(j.calls, 1)
        self.b.serve()
        self.assertTrue(wait_for(lambda: len(self.decisions()) == 3))
        self.assertEqual([d["source"] for d in self.decisions()], ["judge"] * 3)

    def test_notice_failure_is_an_error_record_not_output(self):
        def notice(kind, text):
            raise OSError("inbox gone")

        with contextlib.redirect_stderr(io.StringIO()) as err, contextlib.redirect_stdout(io.StringIO()) as out:
            self.use(self.runner(run=timed_out), notice=notice)
            for i in range(4):
                self.ask(bash("jq . f%d.json" % i))
            self.judged(4)
            self.b.stop()
        self.assertEqual((err.getvalue(), out.getvalue()), ("", ""))
        errors = record.load(self.state)["errors"]
        self.assertTrue(any(e["type"] == "error" and e["where"] == "notice" for e in errors))
        rows = self.decisions()
        self.assertEqual(len(rows), 4)
        self.assertEqual([d["judge_reason"] for d in rows], ["timeout"] * 3 + ["judge circuit open"])
        self.assert_floor(rows)

    def test_notice_reaches_the_callback_once(self):
        notes = []
        jr = self.runner(run=timed_out)
        self.use(jr, notice=lambda kind, text: notes.append(kind))
        for i in range(5):
            self.ask(bash("jq . f%d.json" % i))
        self.judged(5)
        self.b.stop()
        self.assertEqual(notes, ["guard-judge-down"])

    def test_counts_have_judge_counts(self):
        self.ask(bash("jq . f.json"))
        self.assertEqual(self.b.counts()["judge"], {"judged": 0, "allowed": 0, "failed": 0, "skipped": 1})
        self.use(FakeJudge())
        self.ask(bash("jq . f.json"))
        self.ask(bash("herdr pane send x"))
        self.judged(3)  # the first broker's row, then this broker's two
        self.b.stop()
        c = self.b.counts()
        self.assertEqual(c["judge"], {"judged": 1, "allowed": 1, "failed": 0, "skipped": 0})
        self.assertEqual((c["calls"], c["decisions"]), (2, {"allow": 1, "block": 1}))

    def test_judge_model_and_prompt_hash_come_from_the_judge(self):
        self.use(self.runner(run=timed_out))
        self.assertEqual((self.b.judge_model, self.b.prompt_hash), ("test/judge", judging.judge.prompt_hash()))
        self.ask(bash("herdr pane send x"))
        (d,) = self.decisions()
        self.assertEqual((d["judge_model"], d["prompt_hash"]), ("test/judge", judging.judge.prompt_hash()))


ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
FAILED_TIMEOUT = judging.JudgeResult("judge-failed", None, "timeout", 1350, 1)
FAILED_EXIT = judging.JudgeResult("judge-failed", None, "exit 3", 20, 2)
OPEN = judging.JudgeResult("judge-skipped", None, "judge circuit open", None, 0)
NO_TIME = judging.JudgeResult("judge-skipped", None, "no time left", None, 0)
NO_JUDGE = judging.JudgeResult("judge-skipped", None, "no judge", None, 0)
SECRET = judging.JudgeResult("judge-skipped", None, "secret in call", None, 0)
OVERSIZED = judging.JudgeResult("judge-skipped", None, "call over 4 KiB", None, 0)


class ActingJudgeTests(BrokerBase):
    mode = "acting"

    def use(self, judge, **kw):
        self.b.stop()
        self.b = self.start(judge=judge, **kw)
        return self.b

    def ask_with(self, result, cmd="jq . f.json"):
        self.use(FakeJudge(result))
        return self.ask(bash(cmd))

    def assert_floor(self):
        """Never weaker than the rules: only a judge verdict may differ from rule_decision."""
        for d in self.decisions():
            if d["source"] != "judge":
                self.assertEqual(d["decision"], d["rule_decision"], d)

    def test_judge_allow_is_the_reply_in_acting(self):
        j = FakeJudge()
        self.use(j)
        r = self.ask(bash("jq . f.json"))
        self.assertEqual(r["decision"], "allow")
        (d,) = self.decisions()
        self.assertEqual(d["id"], r["id"])
        self.assertEqual((d["source"], d["decision"], d["rule_decision"], d["outcome"]),
                         ("judge", "allow", "ask", "allowed"))
        # g11: the rules' reason first; the judge's (lead-steerable) reason only as a labeled second line
        self.assertEqual(r["reason"], "jq is not in the allow grammar\njudge: local read")
        self.assertEqual(j.rules, ["gray:unknown-command"])  # g12: the judge is told the gray rule
        self.assertEqual(len(j.budgets), 1)
        self.assertLessEqual(j.budgets[0], judging.acting_budget(0))
        self.assertGreater(j.budgets[0], judging.acting_budget(0) - 0.2)

    def test_judge_ask_is_an_ask(self):
        r = self.ask_with(judging.JudgeResult("judge", "ask", "unsure", 9, 1))
        self.assertEqual((r["decision"], r["reason"]), ("ask", "jq is not in the allow grammar\njudge: unsure"))
        self.assertEqual(self.decisions()[0]["outcome"], "asked")

    def test_floor_on_every_judge_failure(self):
        for result in (FAILED_TIMEOUT, FAILED_EXIT, OPEN, NO_TIME, NO_JUDGE, SECRET, OVERSIZED):
            r = self.ask_with(result)
            self.assertEqual(r["decision"], "ask", result)
            d = self.decisions()[-1]
            self.assertEqual((d["source"], d["decision"], d["outcome"]), (result.source, "ask", "asked"))
            self.assertNotEqual(r["reason"], result.reason)  # the rules' reason, not the judge's
        self.assert_floor()

    def test_floor_without_a_judge_and_with_a_judge_that_raises(self):
        r = self.ask(bash("jq . f.json"))  # the base broker has no judge
        self.assertEqual(r["decision"], "ask")
        self.assertEqual(self.decisions()[-1]["source"], "judge-skipped")

        class Raising(FakeJudge):
            def judge(self, *a):
                raise RuntimeError("bug")
        self.use(Raising())
        r = self.ask(bash("jq . f.json"))
        self.assertEqual(r["decision"], "ask")
        d = self.decisions()[-1]
        self.assertEqual((d["source"], d["judge_reason"]), ("judge-failed", "RuntimeError"))
        self.assert_floor()

    def test_a_real_runner_that_times_out_keeps_the_floor(self):
        self.use(judging.JudgeRunner({"model": "test/judge", "key_env": "BW_TEST_JUDGE_KEY"}, self.wt, self.tmp,
                                     run=timed_out))
        r = self.ask(bash("jq . f.json"))
        self.assertEqual(r["decision"], "ask")
        self.assertEqual(self.decisions()[-1]["source"], "judge-failed")

    def test_judge_never_lifts_a_rule_block_or_rule_ask(self):
        j = FakeJudge()
        self.use(j)
        self.assertEqual(self.ask(bash("herdr pane send x"))["decision"], "block")
        self.assertEqual(self.ask(bash("gh release create v0"))["decision"], "ask")
        self.assertEqual(j.calls, 0)
        self.assertEqual([(d["source"], d["decision"]) for d in self.decisions()], [("rule", "block"), ("rule", "ask")])
        self.assert_floor()

    def test_record_before_reply_in_acting(self):
        self.use(FakeJudge(sleep=0.1))
        seen = []
        real = brk.Broker._send

        def spy(conn, obj):
            seen.append(len(self.decisions()))
            return real(conn, obj)
        with mock.patch.object(brk.Broker, "_send", staticmethod(spy)):
            r = self.ask(bash("jq . f.json"))
        self.assertEqual(r["decision"], "allow")
        self.assertEqual(seen, [1])

    def test_slow_judge_records_client_timeout(self):
        self.use(FakeJudge(sleep=2.2))
        self.assertIsNone(self.raw(json.dumps({"v": 1, "token": TOKEN, "runtime": "pi", "cwd": self.wt,
                                               "payload": bash("jq . f.json")}).encode() + b"\n",
                                   timeout=judging.HOOK_DEADLINE))  # the hook gives up at its deadline
        self.assertTrue(wait_for(lambda: [d.get("outcome") for d in self.decisions()] == ["client-timeout"]))
        d = self.decisions()[-1]
        self.assertEqual((d["outcome"], d["outcome_first"], d["decision"]), ("client-timeout", "allowed", "allow"))
        self.b.stop()

    def test_send_failure_after_the_record_appends_an_outcome(self):
        with mock.patch.object(brk.Broker, "_send", staticmethod(lambda conn, obj: False)):
            self.ask(bash("herdr pane send x"))
        self.assertTrue(wait_for(lambda: len(record.load(self.state)["decisions"]) == 1
                                 and self.decisions()[-1]["outcome"] == "client-timeout"))
        events = store.read_events(record.path(self.state))[0]
        self.assertEqual([e["type"] for e in events][-2:], ["decision", "outcome"])
        self.assertEqual(events[-1]["decision_id"], events[-2]["id"])
        self.assertEqual(self.decisions()[-1]["outcome"], "client-timeout")
        self.assertEqual(self.decisions()[-1]["outcome_first"], "blocked")

    def test_a_prompt_reply_appends_no_outcome(self):
        self.ask(bash("herdr pane send x"))
        events = store.read_events(record.path(self.state))[0]
        self.assertEqual([e["type"] for e in events], ["decision"])

    def test_outcome_for_unknown_decision_is_an_orphan(self):
        record.append(self.state, {"type": "outcome", "decision_id": "zzzzzzzzzzzz", "outcome": "client-timeout"})
        r = record.load(self.state)
        self.assertEqual([o["decision_id"] for o in r["orphan_outcomes"]], ["zzzzzzzzzzzz"])
        self.assertEqual(r["decisions"], [])

    def test_reply_reason_is_cleaned(self):
        r = self.ask_with(judging.JudgeResult("judge", "allow", "fine\x1b[2J API_TOKEN=sekrit1 ok", 5, 1))
        self.assertEqual(r["decision"], "allow")
        self.assertNotIn("\x1b", r["reason"])
        self.assertNotIn("sekrit1", r["reason"])
        self.assertNotIn("sekrit1", json.dumps(self.decisions()))

    def test_counts_in_acting(self):
        self.ask_with(JUDGE_ALLOW)
        self.assertEqual(self.b.counts()["judge"], {"judged": 1, "allowed": 1, "failed": 0, "skipped": 0})

    def test_hook_deadlines_match(self):
        from lead.guard import hook
        self.assertEqual(hook.DEADLINE, judging.HOOK_DEADLINE)
        self.assertEqual(brk.READ_DEADLINE, judging.READ_DEADLINE)
        with open(os.path.join(ROOT, "lead", "guard", "pi_guard.ts")) as f:
            ts = f.read()
        self.assertIn("const DEADLINE_MS = %d;" % int(judging.HOOK_DEADLINE * 1000), ts)


class TestRecord(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(dir="/tmp")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_load_skips_truncated_folds_marks_orphans(self):
        record.append(self.tmp, {"type": "decision", "id": "aaaaaaaaaaaa", "decision": "allow"})
        record.append(self.tmp, {"type": "mark", "decision_id": "aaaaaaaaaaaa", "verdict": "wrong", "sampled": True})
        record.append(self.tmp, {"type": "mark", "decision_id": "zzzzzzzzzzzz", "verdict": "right", "sampled": False})
        with open(record.path(self.tmp), "ab") as f:
            f.write(b'{"type": "decision", "id": "bbb')
        r = record.load(self.tmp)
        self.assertEqual(len(r["decisions"]), 1)
        m = r["decisions"][0]["mark"]
        self.assertEqual((m["verdict"], m["sampled"]), ("wrong", True))
        self.assertRegex(m["ts"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")  # store.append's ts; the gate counts marks since a demotion
        self.assertEqual(len(r["marks"]), 2)
        self.assertEqual([m["decision_id"] for m in r["orphan_marks"]], ["zzzzzzzzzzzz"])
        self.assertEqual(len(r["warnings"]), 1)
        self.assertIn("truncated", r["warnings"][0])

    def test_load_missing_file(self):
        r = record.load(os.path.join(self.tmp, "none"))
        self.assertEqual((r["decisions"], r["marks"], r["orphan_marks"], r["warnings"]), ([], [], [], []))

    def test_evidence_key(self):
        base = {"profile": "p", "runtime": "pi", "runtime_version": "1", "rules_version": "r", "policy_digest": "d",
                "judge_model": "test/judge", "prompt_hash": "h"}
        k = record.evidence_key(base)
        self.assertEqual(k, ("p", "pi", "r", "d", "test/judge", "h"))
        self.assertEqual(k, record.evidence_key(dict(base, runtime_version="2")))
        self.assertNotEqual(k, record.evidence_key(dict(base, policy_digest="e")))
        self.assertNotEqual(k, record.evidence_key(dict(base, judge_model="test/other")))
        self.assertNotEqual(k, record.evidence_key(dict(base, prompt_hash="i")))

    def test_policy_digest(self):
        a = record.policy_digest(["main"], ["make deploy"])
        self.assertEqual(len(a), 12)
        self.assertEqual(a, record.policy_digest(["main"], ["make deploy"]))
        self.assertNotEqual(a, record.policy_digest(["main", "dev"], ["make deploy"]))
        self.assertNotEqual(a, record.policy_digest(["main"], []))


class TestBindFailure(BrokerBase):
    def test_a_failed_chmod_after_bind_leaves_no_socket_behind(self):
        b = brk.Broker(self.state, os.path.join(self.tmp, "g2"), TOKEN, "shadow", dict(SESSION), self.ctx())
        with mock.patch("lead.guard.broker.os.chmod", side_effect=OSError("no")):
            with self.assertRaises(OSError):
                b.bind()
        self.assertFalse(os.path.lexists(b.sock_path))


class StandaloneServeTests(unittest.TestCase):
    def ctx_from(self, *extra):
        return self.seen_from(*extra)["ctx"]

    def seen_from(self, *extra):
        seen = {}

        class Stop(Exception):
            pass

        def fake_broker(state, gdir, token, mode, session, ctx, reply_garbage=False, judge=None):
            seen["ctx"], seen["judge"] = ctx, judge
            raise Stop()

        tmp = tempfile.mkdtemp(dir="/tmp")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        argv = ["serve", "--state-dir", tmp, "--guard-dir", tmp, "--worktree", tmp, "--runtime", "pi",
                "--mode", "shadow", "--token-env", "BW_TEST_TOKEN", *extra]
        with mock.patch.dict(os.environ, {"BW_TEST_TOKEN": "x"}), mock.patch.object(brk, "Broker", fake_broker), \
                self.assertRaises(Stop):
            brk.main(argv)
        return seen

    def test_checkpoint_dir_flag_reaches_the_rules(self):
        self.assertEqual(self.ctx_from("--checkpoint-dir", "/tmp/cp").checkpoint_dir, "/tmp/cp")
        self.assertIsNone(self.ctx_from().checkpoint_dir)

    def test_judge_flags_build_a_runner(self):
        self.assertIsNone(self.seen_from()["judge"])
        j = self.seen_from("--judge-model", "test/judge", "--judge-key-env", "BW_TEST_NO_KEY")["judge"]
        self.assertIsInstance(j, judging.JudgeRunner)
        self.assertEqual(j.cfg, {"model": "test/judge", "key_env": "BW_TEST_NO_KEY"})
        self.assertEqual(self.seen_from("--judge-model", "test/judge")["judge"].cfg["key_env"], "OPENROUTER_API_KEY")

    def test_there_is_no_on_judge_failure_flag(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.seen_from("--on-judge-failure", "ask")


if __name__ == "__main__":
    unittest.main()

