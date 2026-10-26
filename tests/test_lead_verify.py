import json, os, sys, tempfile, time, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lead import containment, guard as guardpkg, sandbox, verify  # noqa: E402
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


GUARDED = {"runtime": "pi", "model": "m", "plan": "p", "autonomy_source": "config", "autonomy": "guard"}
REVERIFY_CONFIG = {
    "leads": {"lp": dict(GUARDED), "lk": {"runtime": "kimi", "model": "k", "plan": "q"},
              "la": {"runtime": "agy", "model": "a", "plan": "g", "autonomy_source": "config", "autonomy": "guard"},
              "lc": {"runtime": "codex", "model": "c", "plan": "o"},
              "lp2": {"runtime": "pi", "model": "m2", "plan": "p2"}},
    "profiles": {"default": {"lead_order": ["lp2", "lc"]}},
}
NOW = 1_000_000.0


class Reverify(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.realpath(self.tmp.name)
        p = mock.patch.object(sandbox, "backend", return_value="sandbox-exec")
        p.start(); self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def attacks(self, run):
        return [c for c in run.calls if c[0][0].endswith(".sh")]

    def record_current(self, kind, runtime="pi"):
        ad = verify.ADAPTERS[runtime]
        run = FakeRun("PASS")
        if kind == "containment":
            key = containment.verification_key(runtime, ad.version_argv, "sandbox-exec", run=run)
            containment.record_pass(self.state, runtime, key, "/e")
        else:
            key = containment.guard_key(runtime, ad.version_argv, "sandbox-exec", ad.guard_hook_files(), run=run)
            containment.record_pass(self.state, runtime, key, "/e", store="guard-verify.json")

    def cap(self, plan, resets_at):
        os.makedirs(os.path.join(self.state, "caps"), exist_ok=True)
        with open(os.path.join(self.state, "caps", f"{plan}.json"), "w") as f:
            json.dump({"resets_at": resets_at, "window": "five_hour", "source": "statusline"}, f)

    def test_items_for_runtime(self):
        # containment from the lead first in a lead_order; guard from the guarded lead
        self.assertEqual(verify.items_for_runtime(REVERIFY_CONFIG, "pi"), [("containment", "lp2"), ("guard", "lp")])
        self.assertEqual(verify.items_for_runtime(REVERIFY_CONFIG, "kimi"), [("containment", "lk")])
        self.assertEqual(verify.items_for_runtime(REVERIFY_CONFIG, "agy"), [("guard", "la")])
        self.assertEqual(verify.items_for_runtime(REVERIFY_CONFIG, "codex"), [])
        self.assertEqual(verify.items_for_runtime(REVERIFY_CONFIG, "claude-code"), [])

    def test_items_for_pi_never_include_a_kimi_item(self):
        for kind, name in verify.items_for_runtime(REVERIFY_CONFIG, "pi"):
            self.assertEqual(REVERIFY_CONFIG["leads"][name]["runtime"], "pi")
        for item in verify.reverify(REVERIFY_CONFIG, self.state, "pi", NOW, run=FakeRun("PASS — evidence in /e")):
            self.assertEqual(item.runtime, "pi")

    def test_lead_runtimes_lists_each_runtime_once(self):
        self.assertEqual(sorted(verify.lead_runtimes(REVERIFY_CONFIG)), ["agy", "codex", "kimi", "pi"])

    def test_runtime_with_no_leads_is_empty(self):
        run = FakeRun("PASS — evidence in /e")
        self.assertEqual(verify.reverify(REVERIFY_CONFIG, self.state, "claude-code", NOW, run=run), [])
        self.assertEqual(verify.reverify({"leads": {}}, self.state, "pi", NOW, run=run), [])
        self.assertEqual(run.calls, [])

    def test_matching_key_is_verified_without_a_run(self):
        self.record_current("containment", "kimi")
        run = FakeRun("PASS — evidence in /e")
        items = verify.reverify(REVERIFY_CONFIG, self.state, "kimi", NOW, run=run)
        self.assertEqual([(i.kind, i.state) for i in items], [("containment", "verified")])
        self.assertEqual(self.attacks(run), [])

    def test_changed_key_and_capped_plan_is_pending_naming_the_plan(self):
        self.cap("q", NOW + 3600)
        run = FakeRun("PASS — evidence in /e")
        items = verify.reverify(REVERIFY_CONFIG, self.state, "kimi", NOW, run=run)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].state, "pending")
        self.assertEqual(items[0].reason, f"plan q capped until {time.ctime(NOW + 3600)}")
        self.assertEqual(self.attacks(run), [])

    def test_expired_cap_does_not_hold_the_test(self):
        self.cap("q", NOW - 1)
        run = FakeRun("PASS — evidence in /e")
        items = verify.reverify(REVERIFY_CONFIG, self.state, "kimi", NOW, run=run)
        self.assertEqual(items[0].state, "passed")

    def test_no_verify_is_pending(self):
        run = FakeRun("PASS — evidence in /e")
        items = verify.reverify(REVERIFY_CONFIG, self.state, "pi", NOW, run=run, allow_run=False)
        self.assertEqual([(i.kind, i.state, i.reason) for i in items],
                         [("containment", "pending", "--no-verify"), ("guard", "pending", "--no-verify")])
        self.assertEqual(self.attacks(run), [])

    def test_unreadable_version_is_pending(self):
        run = FakeRun("PASS — evidence in /e")
        items = verify.reverify(REVERIFY_CONFIG, self.state, "kimi", NOW, run=run, version_ok=False)
        self.assertEqual([(i.state, i.reason) for i in items], [("pending", "version unreadable")])
        self.assertEqual(self.attacks(run), [])

    def test_agy_guarded_lead_is_pending_until_the_spike(self):
        run = FakeRun("PASS — evidence in /e")
        items = verify.reverify(REVERIFY_CONFIG, self.state, "agy", NOW, run=run)
        self.assertEqual([(i.kind, i.state, i.reason) for i in items], [("guard", "pending", guardpkg.AGY_AWAITS)])
        self.assertEqual(self.attacks(run), [])

    def test_changed_key_runs_the_attack_and_records_the_pass(self):
        run = FakeRun("PASS — evidence in /e")
        items = verify.reverify(REVERIFY_CONFIG, self.state, "kimi", NOW, run=run)
        self.assertEqual([(i.kind, i.state) for i in items], [("containment", "passed")])
        self.assertIn("recorded a containment pass for kimi", items[0].reason)
        with open(os.path.join(self.state, "containment.json")) as f:
            self.assertEqual(json.load(f)["kimi"]["evidence"], "/e")
        self.assertEqual(len(self.attacks(run)), 1)

    def test_failed_attack_is_failed_with_the_containment_message(self):
        run = FakeRun("FAIL: outside-written:escape.txt, — evidence in /e")
        items = verify.reverify(REVERIFY_CONFIG, self.state, "kimi", NOW, run=run)
        self.assertEqual(items[0].state, "failed")
        self.assertIn(ATTACK_FAILED, items[0].reason)
        self.assertFalse(os.path.exists(os.path.join(self.state, "containment.json")))

    def test_each_item_checks_its_own_key(self):
        self.record_current("containment", "pi")
        run = FakeRun("PASS — evidence in /g")
        items = verify.reverify(REVERIFY_CONFIG, self.state, "pi", NOW, run=run)
        self.assertEqual([(i.kind, i.state) for i in items], [("containment", "verified"), ("guard", "passed")])
        self.assertEqual([c[0][0].rsplit("/", 1)[-1] for c in self.attacks(run)], ["guard_attack.sh"])


if __name__ == "__main__":
    unittest.main()
