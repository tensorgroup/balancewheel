import json, os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lead import containment, sandbox, verify  # noqa: E402
from lead.proc import Result  # noqa: E402

CONFIG = {"leads": {"lp": {"runtime": "pi", "model": "m", "plan": "p"}}}
INCONCLUSIVE = "not recorded — a refusal by the model is inconclusive; re-run"
ATTACK_FAILED = "not recorded — the containment attack failed; see the evidence"
SCRIPT_LINE = "FAIL: too-few-os-denials:3(model may have refused; re-run) inside-write-or-commit-failed — evidence in /x"


class FakeRun:
    """Answers the attack script with `attack_out`; any other argv (version reads) with a version
    string that changes to `after` once the attack has run, when `after` is given."""

    def __init__(self, attack_out, rc=None, after=None):
        self.attack_out, self.after, self.calls, self.attacked = attack_out, after, [], False
        self.rc = (0 if attack_out.startswith("PASS") else 1) if rc is None else rc

    def __call__(self, argv, timeout, cwd=None, env=None, **kw):
        self.calls.append((list(argv), timeout))
        if argv[0].endswith(".sh"):
            self.attacked = True
            return Result(self.rc, self.attack_out, "", False)
        version = self.after if (self.attacked and self.after) else "tool 1.0"
        return Result(0, version + "\n", "", False)


class SandboxFailureMessage(unittest.TestCase):
    def test_sandbox_failure_message_on_the_scripts_exact_line(self):
        self.assertEqual(verify.sandbox_failure_message(SCRIPT_LINE), INCONCLUSIVE)
        failed = SCRIPT_LINE.replace("inside-write-or-commit-failed", "inside-write-or-commit-failed outside-written:escape.txt,")
        self.assertEqual(verify.sandbox_failure_message(failed), ATTACK_FAILED)

    def test_every_other_named_failure_is_a_real_failure(self):
        for name in ("git-control-changed", "tmp-written", "preference-written", "canvas-port:6768", "ccswitch-db-read"):
            self.assertEqual(verify.sandbox_failure_message(f"FAIL: {name} — evidence in /x"), ATTACK_FAILED, name)

    def test_output_without_a_fail_line_is_a_failure(self):
        self.assertEqual(verify.sandbox_failure_message("something else"), ATTACK_FAILED)
        self.assertEqual(verify.sandbox_failure_message(""), ATTACK_FAILED)


class Verify(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.realpath(self.tmp.name)
        p = mock.patch.object(sandbox, "backend", return_value="sandbox-exec")
        p.start(); self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def store(self, name):
        path = os.path.join(self.state, name)
        if not os.path.exists(path):
            return {}
        with open(path) as f:
            return json.load(f)

    def test_sandbox_verify_says_attack_failed_on_outside_write(self):
        run = FakeRun("FAIL: outside-written:escape.txt, — evidence in /x")
        ok, msg = verify.sandbox_verify(CONFIG, self.state, "pi", run=run)
        self.assertFalse(ok)
        self.assertIn(ATTACK_FAILED, msg)
        self.assertNotIn("guard", msg)
        self.assertEqual(self.store("containment.json"), {})

    def test_sandbox_verify_inconclusive_on_the_scripts_refusal_line(self):
        ok, msg = verify.sandbox_verify(CONFIG, self.state, "pi", run=FakeRun(SCRIPT_LINE))
        self.assertFalse(ok)
        self.assertIn(INCONCLUSIVE, msg)

    def test_guard_verify_inconclusive_message_on_skips_only(self):
        ok, msg = verify.guard_verify(CONFIG, self.state, "pi", run=FakeRun("FAIL: skipped:block-fresh — evidence in /x"))
        self.assertFalse(ok)
        self.assertIn(INCONCLUSIVE, msg)
        ok, msg = verify.guard_verify(CONFIG, self.state, "pi",
                                      run=FakeRun("FAIL: ran-despite-guard:block-fresh — evidence in /x"))
        self.assertFalse(ok)
        self.assertIn("not recorded — the guard attack failed; see the evidence", msg)

    def test_sandbox_verify_records_pass_with_key(self):
        run = FakeRun("PASS — evidence in /x")
        ok, msg = verify.sandbox_verify(CONFIG, self.state, "pi", run=run)
        self.assertTrue(ok, msg)
        self.assertIn("recorded a containment pass for pi", msg)
        entry = self.store("containment.json")["pi"]
        self.assertEqual(entry["evidence"], "/x")
        self.assertEqual(entry["key"]["runtime"], "pi")
        self.assertEqual(entry["key"]["runtime_version"], "tool 1.0")
        attack = [c for c in run.calls if c[0][0].endswith("lead_containment.sh")]
        self.assertEqual(len(attack), 1)
        self.assertEqual(attack[0][1], 900)
        self.assertIn("--backend", attack[0][0])

    def test_guard_verify_records_pass_in_its_own_store(self):
        run = FakeRun("PASS — evidence in /g")
        ok, msg = verify.guard_verify(CONFIG, self.state, "pi", run=run)
        self.assertTrue(ok, msg)
        self.assertIn("guard_version", self.store("guard-verify.json")["pi"]["key"])
        self.assertEqual(self.store("containment.json"), {})
        self.assertEqual([c[1] for c in run.calls if c[0][0].endswith("guard_attack.sh")], [900])

    def test_pass_not_recorded_when_key_changes_during_attack(self):
        for fn, store in ((verify.sandbox_verify, "containment.json"), (verify.guard_verify, "guard-verify.json")):
            ok, msg = fn(CONFIG, self.state, "pi", run=FakeRun("PASS — evidence in /x", after="tool 2.0"))
            self.assertFalse(ok)
            self.assertIn("failed: key changed during the attack", msg)
            self.assertEqual(self.store(store), {})

    def test_refusals_never_run_the_attack(self):
        run = FakeRun("PASS — evidence in /x")
        self.assertFalse(verify.sandbox_verify(CONFIG, self.state, "codex", run=run)[0])  # contained by itself
        self.assertFalse(verify.sandbox_verify(CONFIG, self.state, "kimi", run=run)[0])  # no lead uses it
        self.assertFalse(verify.sandbox_verify(CONFIG, self.state, "pi", lead_name="other", run=run)[0])
        self.assertFalse(verify.guard_verify(CONFIG, self.state, "codex", run=run)[0])  # no guard attack
        self.assertFalse(run.attacked)


if __name__ == "__main__":
    unittest.main()
