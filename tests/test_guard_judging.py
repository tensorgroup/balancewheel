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
            calls.append({"argv": argv, "timeout": timeout, "stdin": json.load(kw["stdin"]), "reap": kw.get("reap"),
                          "env": kw.get("env")})
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


def fake_clock_advancing(step):
    """A clock that moves on by `step` each time it is read. The runner reads it once at the start
    and once after each attempt, so each attempt seems to take `step` seconds."""
    t = [1000.0]

    def clock():
        now = t[0]
        t[0] += step
        return now
    return clock


def fail_fast_then_ok(calls):
    """The first child fails at once (exit 0, ok false); every later child gives a verdict."""
    def run(argv, timeout, **kw):
        calls.append(timeout)
        out = '{"ok": false, "reason": "URLError"}' if len(calls) == 1 else ALLOW
        return proc.Result(0, out, "", False)
    return run


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
        r = runner(run=fake_run(ALLOW, calls=calls)).judge(CALL, "d", 0.02)
        self.assertEqual((r.source, r.reason, calls), ("judge-skipped", "no time left", []))  # no child at all

    def test_the_child_env_holds_only_the_key_and_basics(self):
        calls = []
        env = {"BW_TEST_JUDGE_KEY": "k-test", "PATH": "/usr/bin", "HOME": "/tmp/h", "TMPDIR": "/tmp/t",
               "LANG": "C.UTF-8", "LC_ALL": "C", "HTTPS_PROXY": "http://proxy.test:1", "no_proxy": "localhost",
               "SSL_CERT_FILE": "/tmp/ca.pem", "OTHER_API_KEY": "must-not-pass", "GITHUB_TOKEN": "must-not-pass",
               "BW_STATE_DIR": "/tmp/state"}
        with mock.patch.dict("os.environ", env, clear=True):
            runner(run=fake_run(ALLOW, calls=calls)).judge(CALL, "d", 1.0)
        self.assertEqual(calls[0]["env"], {k: v for k, v in env.items()
                                           if k not in ("OTHER_API_KEY", "GITHUB_TOKEN", "BW_STATE_DIR")})

    def test_the_key_never_reaches_argv_or_stdin(self):
        calls = []
        with mock.patch.dict("os.environ", {"BW_TEST_JUDGE_KEY": "sk-or-v1-not-a-real-key-0000"}):
            runner(run=fake_run(ALLOW, calls=calls)).judge(CALL, "d", 1.0)
        self.assertNotIn("not-a-real-key", json.dumps([dict(c, env=None) for c in calls]))
        self.assertEqual(calls[0]["env"]["BW_TEST_JUDGE_KEY"], "sk-or-v1-not-a-real-key-0000")  # env only

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
        self.assertEqual(j.judge(CALL, "d", 1.0), first._replace(ms=0, attempts=0))
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
        self.assertEqual(len(calls), 4)  # each judge() call: a failure, then its one retry; nothing cached


class TestActingBudget(unittest.TestCase):
    def test_deadline_constants(self):
        self.assertEqual((judging.HOOK_DEADLINE, judging.READ_DEADLINE, judging.JUDGE_MARGIN, judging.MIN_ATTEMPT_S),
                         (2.0, 0.5, 0.15, 0.3))

    def test_acting_budget(self):
        self.assertAlmostEqual(judging.acting_budget(0.1), 2.0 - 0.5 - 0.1 - 0.15)
        self.assertEqual(judging.acting_budget(5.0), 0.0)

    def test_one_retry_only_if_time_remains(self):
        calls = []
        j = runner(run=fail_fast_then_ok(calls), clock=fake_clock_advancing(0.2))
        r = j.judge(CALL, "d", 1.0)
        self.assertEqual((r.attempts, r.source, r.decision), (2, "judge", "allow"))
        j2 = runner(run=fail_fast_then_ok([]), clock=fake_clock_advancing(0.8))
        r2 = j2.judge(CALL, "d", 1.0)
        self.assertEqual((r2.attempts, r2.source, r2.reason), (1, "judge-failed", "URLError"))

    def test_a_success_is_never_retried(self):
        calls = []
        r = runner(run=fake_run(ALLOW, calls=calls), clock=fake_clock_advancing(0.1)).judge(CALL, "d", 1.0)
        self.assertEqual((r.attempts, len(calls)), (1, 1))

    def test_two_failures_count_once_for_the_breaker(self):
        j = runner(run=fake_run('{"ok": false, "reason": "URLError"}'), clock=fake_clock_advancing(0.01))
        for i in range(2):
            self.assertEqual(j.judge(CALL, "d%d" % i, 1.0).attempts, 2)
        self.assertEqual(j.judge(CALL, "d2", 1.0).source, "judge-failed")  # the third call opens it
        self.assertEqual(j.judge(CALL, "d3", 1.0).reason, "judge circuit open")

    def test_no_time_left_skips(self):
        r = runner(run=raising(AssertionError)).judge(CALL, "d", 0.1)
        self.assertEqual((r.source, r.reason, r.attempts), ("judge-skipped", "no time left", 0))

    def test_a_cache_hit_needs_no_time(self):
        j = runner(run=fake_run(ALLOW), clock=Clock())
        first = j.judge(CALL, "d", 1.0)
        self.assertEqual(j.judge(CALL, "d", 0.0), first._replace(ms=0, attempts=0))

    def test_a_cache_hit_reports_no_time_and_no_attempts(self):
        j = runner(run=fake_run(ALLOW), clock=fake_clock_advancing(0.2))
        first = j.judge(CALL, "d", 1.0)
        self.assertEqual((first.attempts, first.ms > 0), (1, True))
        hit = j.judge(CALL, "d", 1.0)
        self.assertEqual((hit.source, hit.decision, hit.reason, hit.ms, hit.attempts),
                         ("judge", "allow", "local read", 0, 0))

    def test_ms_counts_the_prepare_step(self):
        clock = Clock()
        real = judge.prepare

        def slow_prepare(*a):
            clock.t += 0.25
            return real(*a)
        with mock.patch.object(judge, "prepare", slow_prepare):
            r = runner(run=fake_run(ALLOW), clock=clock).judge(CALL, "d", 1.0)
        self.assertAlmostEqual(r.ms, 250.0)

    def test_child_timeout_is_the_remaining_budget(self):
        calls = []
        runner(run=fail_fast_then_ok(calls), clock=fake_clock_advancing(0.2)).judge(CALL, "d", 1.0)
        self.assertEqual(len(calls), 2)
        self.assertAlmostEqual(calls[0], 1.0)
        self.assertAlmostEqual(calls[1], 0.8)

    def test_ms_covers_both_attempts(self):
        r = runner(run=fail_fast_then_ok([]), clock=fake_clock_advancing(0.2)).judge(CALL, "d", 1.0)
        self.assertAlmostEqual(r.ms, 400.0)

    def test_a_verdict_reason_is_cleaned(self):
        out = json.dumps({"ok": True, "decision": "allow", "reason": "ok\x1b[31m API_TOKEN=abc123 done"})
        r = runner(run=fake_run(out)).judge(CALL, "d", 1.0)
        self.assertNotIn("\x1b", r.reason)
        self.assertNotIn("abc123", r.reason)


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
