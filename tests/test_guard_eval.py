import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lead import proc  # noqa: E402
from lead.guard import AGY_AWAITS, evaluate, guard_version, judge, judging, rules_version  # noqa: E402


def _no_network(*a, **k):
    raise AssertionError("a guard eval test tried to open a real connection")


_NET = mock.patch("urllib.request.build_opener", _no_network)


def setUpModule():
    _NET.start()


def tearDownModule():
    _NET.stop()


def always(out):
    def run(argv, timeout, **kw):
        return proc.Result(0, out, "", False)
    return run


def timed_out(argv, timeout, **kw):
    return proc.Result(124, "", "", True)


ALLOW = '{"ok": true, "decision": "allow", "reason": "ok"}'
ASK = '{"ok": true, "decision": "ask", "reason": "no"}'


def runner(model="test/judge", run=None):
    return judging.JudgeRunner({"model": model, "key_env": "BW_TEST_JUDGE_KEY"}, None, None,
                               run=run or always(ALLOW))


def write_set(directory, per_class=15, **change):
    """A set of `per_class` cases in each class; `change` maps a file name to a function on its list."""
    os.makedirs(directory, exist_ok=True)
    for cls in evaluate.CLASSES:
        cases = [{"id": f"{cls}-{i:02d}", "class": cls, "runtime": "pi", "cwd": "{worktree}",
                  "payload": {"toolName": "bash", "input": {"command": "ls"}},
                  "expect": "allow" if cls == "benign" else "stop"} for i in range(per_class)]
        if cls in change:
            cases = change[cls](cases)
        with open(os.path.join(directory, cls + ".json"), "w") as f:
            json.dump(cases, f)


class Set(unittest.TestCase):
    def test_committed_set_loads_with_15_per_class(self):
        cases, h = evaluate.load_set()
        self.assertEqual(len(h), 12)
        for c in evaluate.CLASSES:
            self.assertGreaterEqual(sum(x["class"] == c for x in cases), 15)

    def test_set_is_synthetic(self):
        home = os.path.expanduser("~")
        for name in os.listdir(evaluate.EVAL_DIR):
            text = (Path(evaluate.EVAL_DIR) / name).read_text()
            for bad in ("/Users/", "/home/", home):
                self.assertNotIn(bad, text, name)

    def test_loader_refuses_bad_sets(self):
        with tempfile.TemporaryDirectory() as d:
            ok = os.path.join(d, "ok")
            write_set(ok)
            self.assertEqual(len(evaluate.load_set(ok)[0]), 60)
            dup = os.path.join(d, "dup")
            write_set(dup, benign=lambda cs: cs[:-1] + [dict(cs[0])])
            with self.assertRaisesRegex(ValueError, "duplicate"):
                evaluate.load_set(dup)
            weird = os.path.join(d, "weird")
            write_set(weird, benign=lambda cs: [dict(cs[0], **{"class": "weird"})] + cs[1:])
            with self.assertRaisesRegex(ValueError, "unknown class"):
                evaluate.load_set(weird)
            wrong = os.path.join(d, "wrong")
            write_set(wrong, benign=lambda cs: [dict(cs[0], expect="stop")] + cs[1:])
            with self.assertRaisesRegex(ValueError, "wrong expect"):
                evaluate.load_set(wrong)
            few = os.path.join(d, "few")
            write_set(few, injected=lambda cs: cs[:14])
            with self.assertRaisesRegex(ValueError, "injected has 14"):
                evaluate.load_set(few)

    def test_set_hash_follows_the_files(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, "a"), os.path.join(d, "b")
            write_set(a)
            shutil.copytree(a, b)
            self.assertEqual(evaluate.load_set(a)[1], evaluate.load_set(b)[1])
            write_set(b, dangerous=lambda cs: cs + [dict(cs[0], id="dangerous-extra")])
            self.assertNotEqual(evaluate.load_set(a)[1], evaluate.load_set(b)[1])


class Run(unittest.TestCase):
    def setUp(self):
        self.cases, self.hash = evaluate.load_set()

    def test_rules_alone_pass(self):
        r = evaluate.run(self.cases, "pi", runner(model=None))
        self.assertEqual(r["status"], "pass", r["allowed_non_benign"])
        self.assertEqual(r["allowed_non_benign"], [])
        self.assertEqual(r["counts"]["judged"], 0)

    def test_run_without_a_runner_is_the_rules_alone(self):
        self.assertEqual(evaluate.run(self.cases, "pi", None)["status"], "pass")

    def test_allow_everything_judge_fails(self):
        r = evaluate.run(self.cases, "pi", runner(run=always(ALLOW)))
        self.assertEqual(r["status"], "fail")
        self.assertTrue(r["allowed_non_benign"])
        self.assertGreater(r["counts"]["judge_allowed"], 0)
        self.assertTrue(all(not i.startswith("benign") for i in r["allowed_non_benign"]))

    def test_ask_everything_judge_passes(self):
        r = evaluate.run(self.cases, "pi", runner(run=always(ASK)))
        self.assertEqual(r["status"], "pass")
        n = r["counts"]
        self.assertEqual((n["judged"] + n["judge_skipped"], n["judge_failed"]), (n["gray"], 0))
        self.assertGreater(n["judged"], 0)

    def test_judge_failure_is_incomplete(self):
        r = evaluate.run(self.cases, "pi", runner(run=timed_out))
        self.assertEqual(r["status"], "incomplete")
        self.assertGreater(r["counts"]["judge_failed"], 0)
        self.assertEqual(r["allowed_non_benign"], [])

    def test_benign_false_stop_rate_is_reported(self):
        r = evaluate.run(self.cases, "pi", runner(model=None))
        b = r["counts"]["by_class"]["benign"]
        self.assertGreater(b["stopped"], 0)  # a gray benign call stays asked without a judge
        self.assertAlmostEqual(r["benign_false_stop_rate"], b["stopped"] / b["total"], places=3)
        r = evaluate.run(self.cases, "pi", runner(run=always(ALLOW)))
        self.assertLess(r["benign_false_stop_rate"], b["stopped"] / b["total"])

    def test_the_judge_sees_the_synthetic_worktree_as_a_placeholder(self):
        seen = []

        def run(argv, timeout, **kw):
            seen.append(json.load(kw["stdin"])["text"])
            return proc.Result(0, ASK, "", False)
        evaluate.run(self.cases, "pi", runner(run=run))
        self.assertTrue(seen)
        self.assertTrue(any("{worktree}" in t for t in seen))
        self.assertFalse(any("bw-guard-eval" in t for t in seen))

    def test_the_throwaway_repo_is_removed(self):
        made = []
        real = tempfile.TemporaryDirectory

        def spy(*a, **k):
            d = real(*a, **k)
            made.append(d.name)
            return d
        with mock.patch.object(tempfile, "TemporaryDirectory", spy):
            evaluate.run(self.cases, "pi", None)
        self.assertEqual(len(made), 1)
        self.assertFalse(os.path.exists(made[0]))

    def test_an_agy_run_stops_everything(self):
        r = evaluate.run(self.cases, "agy", None)  # no agy shapes yet: every call is gray, the rules ask
        self.assertEqual(r["status"], "pass")
        self.assertEqual(r["benign_false_stop_rate"], 1.0)


class Key(unittest.TestCase):
    def key(self, cfg=None, h="h1", rt="pi"):
        return evaluate.eval_key(rt, cfg if cfg is not None else {"model": "test/judge"}, h)

    def test_key_has_its_parts(self):
        k = self.key()
        self.assertEqual(set(k), {"runtime", "rules_version", "guard_version", "judge_model", "prompt_hash", "set_hash"})
        self.assertEqual((k["rules_version"], k["guard_version"]), (rules_version(), guard_version()))
        self.assertEqual(k["prompt_hash"], judge.prompt_hash())

    def test_key_without_a_model_has_no_prompt_hash(self):
        k = self.key({})
        self.assertEqual((k["judge_model"], k["prompt_hash"]), (None, None))

    def test_key_changes_with_each_part(self):
        base = self.key()
        self.assertNotEqual(base, self.key({"model": "other/judge"}))
        self.assertNotEqual(base, self.key(h="h2"))
        self.assertNotEqual(base, self.key(rt="agy"))
        with mock.patch("lead.guard.evaluate.rules_version", return_value="zzzz"):
            self.assertNotEqual(base, self.key())
        with mock.patch("lead.guard.evaluate.guard_version", return_value="zzzz"):
            self.assertNotEqual(base, self.key())
        with mock.patch.object(judge, "prompt_hash", return_value="zzzz"):
            self.assertNotEqual(base, self.key())

    def test_key_ignores_on_judge_failure(self):  # G-f: the setting is inert
        self.assertEqual(self.key({"model": "m", "on_judge_failure": "allow"}),
                         self.key({"model": "m", "on_judge_failure": "ask"}))


class Store(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = self.tmp.name

    PASS = {"status": "pass"}
    INC = {"status": "incomplete"}
    FAIL = {"status": "fail"}

    def test_read_and_write_round_trip(self):
        self.assertIsNone(evaluate.read(self.state, "pi"))
        evaluate.write(self.state, "pi", {"k": 1}, self.PASS)
        e = evaluate.read(self.state, "pi")
        self.assertEqual((e["key"], e["result"]), ({"k": 1}, self.PASS))
        self.assertEqual(e["last_pass"]["key"], {"k": 1})

    def test_current_is_none_on_another_key(self):
        evaluate.write(self.state, "pi", {"k": 1}, self.PASS)
        self.assertEqual(evaluate.current(self.state, "pi", {"k": 1}), self.PASS)
        self.assertIsNone(evaluate.current(self.state, "pi", {"k": 2}))
        self.assertIsNone(evaluate.current(self.state, "agy", {"k": 1}))

    def test_last_pass_survives_an_incomplete_on_the_same_key(self):  # G-d
        evaluate.write(self.state, "pi", {"k": 1}, self.PASS)
        evaluate.write(self.state, "pi", {"k": 1}, self.INC)
        self.assertEqual(evaluate.current(self.state, "pi", {"k": 1})["status"], "incomplete")
        self.assertTrue(evaluate.passed_on(self.state, "pi", {"k": 1}))

    def test_last_pass_is_for_its_key_only(self):
        evaluate.write(self.state, "pi", {"k": 1}, self.PASS)
        evaluate.write(self.state, "pi", {"k": 2}, self.INC)
        self.assertFalse(evaluate.passed_on(self.state, "pi", {"k": 2}))
        self.assertTrue(evaluate.passed_on(self.state, "pi", {"k": 1}))  # the old pass is kept, for its own key

    def test_a_fail_keeps_the_last_pass_record_but_current_says_fail(self):
        evaluate.write(self.state, "pi", {"k": 1}, self.PASS)
        evaluate.write(self.state, "pi", {"k": 1}, self.FAIL)
        self.assertEqual(evaluate.current(self.state, "pi", {"k": 1})["status"], "fail")

    def test_runtimes_are_kept_apart_and_a_bad_file_reads_as_none(self):
        evaluate.write(self.state, "pi", {"k": 1}, self.PASS)
        evaluate.write(self.state, "agy", {"k": 1}, self.FAIL)
        self.assertEqual(evaluate.current(self.state, "pi", {"k": 1}), self.PASS)
        Path(evaluate.path(self.state)).write_text("not json")
        self.assertIsNone(evaluate.read(self.state, "pi"))


class Ensure(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = self.tmp.name
        self.config = {"guard": {"model": "test/judge", "key_env": "BW_TEST_JUDGE_KEY"}}
        self.calls = []

    def fake(self, out=ASK):
        def run(argv, timeout, **kw):
            self.calls.append(argv)
            return proc.Result(0, out, "", False)
        return run

    def test_skips_on_the_same_key_and_reruns_incomplete(self):
        word, line = evaluate.ensure(self.config, self.state, "pi", run=self.fake())
        self.assertEqual(word, "pass", line)
        self.assertIn("guard eval pi: pass", line)
        first = len(self.calls)
        self.assertGreater(first, 0)
        word, line = evaluate.ensure(self.config, self.state, "pi", run=self.fake())
        self.assertEqual((word, len(self.calls)), ("pass", first))  # no new judge call
        self.assertIn("key unchanged", line)
        # an incomplete result runs again, but at most once a day
        evaluate.write(self.state, "pi", evaluate.read(self.state, "pi")["key"], {"status": "incomplete",
                       "allowed_non_benign": [], "benign_false_stop_rate": 0.0,
                       "counts": evaluate.run(*(evaluate.load_set()[0], "pi", None))["counts"]})
        ts = evaluate.read(self.state, "pi")["ts"]
        word, line = evaluate.ensure(self.config, self.state, "pi", run=self.fake(), now=ts + 3600)
        self.assertEqual((word, len(self.calls)), ("incomplete", first))  # no judge call within a day
        self.assertIn("retried once a day", line)
        evaluate.ensure(self.config, self.state, "pi", run=self.fake(), now=ts + evaluate.RETRY_INCOMPLETE_S)
        self.assertGreater(len(self.calls), first)
        self.assertEqual(evaluate.read(self.state, "pi")["result"]["status"], "pass")

    def test_a_changed_key_runs_again(self):
        evaluate.ensure(self.config, self.state, "pi", run=self.fake())
        n = len(self.calls)
        self.config["guard"]["model"] = "other/judge"
        evaluate.ensure(self.config, self.state, "pi", run=self.fake())
        self.assertGreater(len(self.calls), n)

    def test_ensure_with_no_verify_makes_no_judge_call(self):  # B9
        def boom(argv, timeout, **kw):
            raise AssertionError("a judge call under --no-verify")
        word, line = evaluate.ensure(self.config, self.state, "pi", allow_run=False, run=boom)
        self.assertEqual(word, "pending")
        self.assertIn("pending: --no-verify", line)
        self.assertIsNone(evaluate.read(self.state, "pi"))

    def test_no_verify_still_reports_a_stored_pass_on_the_same_key(self):
        evaluate.ensure(self.config, self.state, "pi", run=self.fake())
        word, _ = evaluate.ensure(self.config, self.state, "pi", allow_run=False, run=None)
        self.assertEqual(word, "pass")

    def test_ensure_skips_agy_until_its_spike(self):  # B9
        def boom(argv, timeout, **kw):
            raise AssertionError("a judge call for agy")
        word, line = evaluate.ensure(self.config, self.state, "agy", run=boom)
        self.assertEqual(word, "skipped")
        self.assertIn(AGY_AWAITS, line)
        self.assertIsNone(evaluate.read(self.state, "agy"))

    def test_a_judge_that_allows_everything_is_stored_as_a_fail(self):
        word, line = evaluate.ensure(self.config, self.state, "pi", run=self.fake(ALLOW))
        self.assertEqual(word, "fail")
        self.assertIn("allowed:", line)
        self.assertFalse(evaluate.passed_on(self.state, "pi", evaluate.read(self.state, "pi")["key"]))

    def test_no_model_means_the_rules_alone(self):
        word, _ = evaluate.ensure({"guard": {}}, self.state, "pi")
        self.assertEqual(word, "pass")


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(os.path.realpath(self.tmp.name))
        self.state = str(self.root / "state")
        cfg = {"schema_version": 2, "plans": {"pa": {"billing": "plan", "quota": "none"}},
               "leads": {"la": {"runtime": "pi", "model": "a", "plan": "pa"}},
               "seats": {"s1": {"runtime": "codex", "model": "x", "roles": ["review"]}},
               "profiles": {"p": {"lead_order": ["la"]}}, "default_profile": "p",
               "state_dir": self.state, "metrics_log": str(self.root / "metrics.jsonl")}
        self.cfg = self.root / "config.json"
        self.cfg.write_text(json.dumps(cfg))
        self.env = dict(os.environ, BALANCEWHEEL_CONFIG=str(self.cfg), BW_STATE_DIR=self.state)

    def bw(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "bin" / "bw"), "guard", "eval", *args], env=self.env,
                              capture_output=True, text=True, cwd=str(self.root), timeout=120)

    def test_cli_exit_codes(self):  # no guard.model: the rules alone, which pass
        r = self.bw()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("guard eval pi: pass", r.stdout)
        self.assertEqual(evaluate.read(self.state, "pi")["result"]["status"], "pass")

    def test_cli_json(self):
        r = self.bw("--runtime", "pi", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        self.assertEqual(got["result"]["status"], "pass")
        self.assertEqual(got["key"]["runtime"], "pi")

    def test_cli_agy_awaits_its_spike(self):
        r = self.bw("--runtime", "agy")
        self.assertEqual(r.returncode, 1)
        self.assertIn(AGY_AWAITS, r.stderr)

    def test_exit_code_for_each_status(self):
        self.assertEqual(evaluate.EXIT, {"pass": 0, "fail": 1, "incomplete": 75})
        for status, code in evaluate.EXIT.items():
            with mock.patch.object(evaluate, "evaluate", return_value=({}, {"status": status, "allowed_non_benign": [],
                                   "benign_false_stop_rate": 0.0, "counts": {"judge_failed": 0, "by_class": {
                                       "benign": {"total": 20, "stopped": 0}}}})):
                with open(os.devnull, "w") as out:
                    self.assertEqual(evaluate.cli({}, self.state, "pi", out=out), code)


if __name__ == "__main__":
    unittest.main()
