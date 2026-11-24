import json
import unittest
from unittest import mock

from lead import proc
from lead.guard import judge, judging
from lead.guard.normalise import Call

WT = "/tmp/bw-judging-test/wt"
HOME = "/tmp/bw-judging-test/home"
CALL = Call("pi", "bash", "shell", "jq . f.json", (), None, None, WT, WT)


def _no_network(*a, **k):
    raise AssertionError("a judging test tried to open a real connection")


_NET = mock.patch("urllib.request.build_opener", _no_network)


def setUpModule():  # the runner shells out through a fake run; nothing here may reach the network
    _NET.start()


def tearDownModule():
    _NET.stop()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def fake_run(out, rc=0, calls=None):
    def run(argv, timeout, **kw):
        if calls is not None:
            calls.append({"argv": argv, "timeout": timeout, "stdin": json.load(kw["stdin"]), "reap": kw.get("reap")})
        return proc.Result(rc, out, "", False)
    return run


def timed_out(argv, timeout, **kw):
    return proc.Result(124, "", "", True)


def raising(exc):
    def f(*a, **k):
        raise exc("boom")
    return f


def runner(model="test/judge", run=None, clock=None, notice=None):
    return judging.JudgeRunner({"model": model, "key_env": "BW_TEST_JUDGE_KEY"}, WT, HOME,
                               run=run or fake_run('{"ok": true, "decision": "allow", "reason": "r"}'),
                               clock=clock or Clock(), notice=notice)


ALLOW = '{"ok": true, "decision": "allow", "reason": "local read"}'
SKIP = judging.JudgeResult("judge-skipped", None, "no judge", None, 0)
JUDGE_ALLOW = judging.JudgeResult("judge", "allow", "local read", 5, 1)
JUDGE_ASK = judging.JudgeResult("judge", "ask", "unsure", 5, 1)
FAILED = judging.JudgeResult("judge-failed", None, "timeout", 900, 1)
OPEN = judging.JudgeResult("judge-skipped", None, "judge circuit open", None, 0)
NO_JUDGE = judging.JudgeResult("judge-skipped", None, "no judge", None, 0)
SECRET = judging.JudgeResult("judge-skipped", None, "secret in call", None, 0)
QUEUE_FULL = judging.JudgeResult("judge-skipped", None, "queue full", None, 0)


class TestJudgeRunner(unittest.TestCase):
    def test_no_model_skips(self):
        r = runner(model=None, run=raising(AssertionError)).judge(CALL, "d", 1.0)
        self.assertEqual((r.source, r.decision, r.reason, r.attempts), ("judge-skipped", None, "no judge", 0))

    def test_prepare_refusal_skips_with_its_reason(self):
        call = CALL._replace(command="curl -H 'Authorization: x' u")
        r = runner(run=raising(AssertionError)).judge(call, "d", 1.0)
        self.assertEqual((r.source, r.reason), ("judge-skipped", "secret in call"))

    def test_child_verdict_is_used(self):
        r = runner(run=fake_run(ALLOW)).judge(CALL, "d", 1.0)
        self.assertEqual((r.source, r.decision, r.reason, r.attempts), ("judge", "allow", "local read", 1))

    def test_child_argv_stdin_and_timeouts(self):
        calls = []
        runner(run=fake_run(ALLOW, calls=calls)).judge(CALL, "d", 1.0)
        (c,) = calls
        self.assertEqual(c["argv"][1:], ["-I", judging.JUDGE_PY])
        self.assertEqual(c["timeout"], 1.0)
        self.assertTrue(c["reap"])
        self.assertAlmostEqual(c["stdin"]["timeout"], 0.95)
        self.assertEqual(c["stdin"]["config"], {"model": "test/judge", "key_env": "BW_TEST_JUDGE_KEY"})
        self.assertEqual(c["stdin"]["text"], judge.prepare(CALL, WT, HOME)[0])
        calls.clear()
        runner(run=fake_run(ALLOW, calls=calls)).judge(CALL, "d", 0.02)
        self.assertEqual(calls[0]["stdin"]["timeout"], 0.05)  # the urllib timeout has a floor (B15)

    def test_the_key_never_reaches_argv_or_stdin(self):
        calls = []
        with mock.patch.dict("os.environ", {"BW_TEST_JUDGE_KEY": "sk-or-v1-not-a-real-key-0000"}):
            runner(run=fake_run(ALLOW, calls=calls)).judge(CALL, "d", 1.0)
        self.assertNotIn("not-a-real-key", json.dumps(calls))

    def test_timeout_and_garbage_fail(self):
        self.assertEqual(runner(run=timed_out).judge(CALL, "d", 1.0).reason, "timeout")
        self.assertEqual(runner(run=fake_run("garbage")).judge(CALL, "d", 1.0).reason, "no verdict")
        for out in ("", "[]", '{"ok": true, "decision": "block", "reason": "x"}',
                    '{"ok": true, "decision": "allow"}', '{"ok": false}'):
            r = runner(run=fake_run(out)).judge(CALL, "d", 1.0)
            self.assertEqual((r.source, r.reason), ("judge-failed", "no verdict"), out)

    def test_non_zero_exit_fails(self):
        r = runner(run=fake_run(ALLOW, rc=3)).judge(CALL, "d", 1.0)
        self.assertEqual((r.source, r.decision, r.reason), ("judge-failed", None, "exit 3"))

    def test_ok_false_with_exit_0_is_judge_failed(self):  # G-j
        r = runner(run=fake_run('{"ok": false, "reason": "HTTPError"}', rc=0)).judge(CALL, "d", 1.0)
        self.assertEqual((r.source, r.reason), ("judge-failed", "HTTPError"))

    def test_a_run_that_raises_is_judge_failed(self):
        r = runner(run=raising(OSError)).judge(CALL, "d", 1.0)
        self.assertEqual((r.source, r.reason), ("judge-failed", "OSError"))

    def test_proc_run_is_looked_up_at_call_time(self):
        j = judging.JudgeRunner({"model": "test/judge", "key_env": "K"}, WT, HOME, clock=Clock())
        with mock.patch.object(judging.proc, "run", fake_run(ALLOW)):
            self.assertEqual(j.judge(CALL, "d", 1.0).source, "judge")

    def test_failing_notice_does_not_stop_judging(self):  # B6
        j = runner(run=timed_out, notice=raising(OSError))
        for i in range(5):
            j.judge(CALL, "d%d" % i, 1.0)  # no exception escapes

    def test_breaker_opens_after_three_and_notices_once(self):
        notes = []
        j = runner(run=timed_out, notice=lambda k, t: notes.append(k))
        for i in range(5):
            last = j.judge(CALL, "d%d" % i, 1.0)
        self.assertEqual((last.source, last.reason), ("judge-skipped", "judge circuit open"))
        self.assertEqual(notes, ["guard-judge-down"])

    def test_breaker_closes_after_five_minutes(self):
        clock, calls = Clock(), []
        j = runner(run=timed_out, clock=clock)
        for i in range(3):
            j.judge(CALL, "d%d" % i, 1.0)
        j.run = fake_run(ALLOW, calls=calls)
        clock.t += 299
        self.assertEqual(j.judge(CALL, "x", 1.0).reason, "judge circuit open")
        self.assertEqual(calls, [])
        clock.t += 2
        self.assertEqual(j.judge(CALL, "x", 1.0).source, "judge")
        self.assertEqual(len(calls), 1)

    def test_success_allows_a_new_notice_on_the_next_outage(self):
        clock, notes = Clock(), []
        j = runner(run=timed_out, clock=clock, notice=lambda k, t: notes.append(k))
        for i in range(3):
            j.judge(CALL, "a%d" % i, 1.0)
        clock.t += 301
        j.run = timed_out
        j.judge(CALL, "b", 1.0)  # the first try after the window fails: open again, no second notice
        self.assertEqual(notes, ["guard-judge-down"])
        clock.t += 301
        j.run = fake_run(ALLOW)
        self.assertEqual(j.judge(CALL, "c", 1.0).source, "judge")
        j.run = timed_out
        for i in range(3):
            j.judge(CALL, "e%d" % i, 1.0)
        self.assertEqual(notes, ["guard-judge-down"] * 2)

    def test_cache_hit_within_ten_minutes_only(self):
        clock, calls = Clock(), []
        j = runner(run=fake_run(ALLOW, calls=calls), clock=clock)
        first = j.judge(CALL, "d", 1.0)
        clock.t += 599
        self.assertEqual(j.judge(CALL, "d", 1.0), first)
        self.assertEqual(len(calls), 1)
        j.judge(CALL._replace(cwd=WT + "/sub"), "d", 1.0)  # another cwd is another key
        self.assertEqual(len(calls), 2)
        clock.t += 2
        j.judge(CALL, "d", 1.0)
        self.assertEqual(len(calls), 3)

    def test_cache_never_holds_a_failure(self):
        calls = []
        j = runner(run=fake_run('{"ok": false, "reason": "URLError"}', calls=calls))
        j.judge(CALL, "d", 1.0)
        j.judge(CALL, "d", 1.0)
        self.assertEqual(len(calls), 2)


class TestFinalDecision(unittest.TestCase):
    def test_final_decision_table(self):
        F = judging.final_decision  # G-f: no mode, no on_judge_failure
        self.assertEqual(F("block", False, SKIP), "block")
        self.assertEqual(F("allow", False, None), "allow")
        self.assertEqual(F("ask", True, JUDGE_ALLOW), "allow")
        self.assertEqual(F("ask", True, JUDGE_ASK), "ask")
        for r in (FAILED, OPEN, NO_JUDGE, SECRET, QUEUE_FULL, None):
            self.assertEqual(F("ask", True, r), "ask")  # the floor, recorded in shadow too

    def test_never_weaker_than_the_rules(self):
        weird = judging.JudgeResult("judge", "allow", "x", 1, 1)
        for rule in ("block", "allow"):
            for r in (JUDGE_ALLOW, JUDGE_ASK, FAILED, OPEN, weird):
                self.assertEqual(judging.final_decision(rule, False, r), rule)
        self.assertEqual(judging.final_decision("block", True, weird), "block")  # a judge never lifts a block
        bad = judging.JudgeResult("judge", "yes", "x", 1, 1)
        self.assertEqual(judging.final_decision("ask", True, bad), "ask")


if __name__ == "__main__":
    unittest.main()
