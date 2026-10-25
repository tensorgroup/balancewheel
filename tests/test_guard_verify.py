import json, os, subprocess, sys, tempfile, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
from lead.guard import launch, verify  # noqa: E402
from lead.runtimes import ADAPTERS  # noqa: E402

BW = os.path.join(ROOT, "bin", "bw")
PI_LEAD = {"runtime": "pi", "model": "m", "plan": "p"}


def dec(call, rule, decision, outcome):
    return {"call": call, "rule": rule, "decision": decision, "outcome": outcome}


def facts(case, phase, **kw):
    f = {"case": case, "phase": phase, "marker": False, "invoked": False, "decisions": [],
         "unavailable": False, "file": False, "sock_intact": True, "client_intact": True, "exit": 0}
    f.update(kw)
    return f


def good(case, phase):
    """A passing set of facts for one case and phase."""
    spec = verify.SPECS[case]
    needle = spec["needle"]
    if case == "control":
        return facts(case, phase, marker=True, invoked=True,
                     decisions=[dec(needle, "block:canvas-control", "block", "shadow-allowed")])
    if case == "block":
        return facts(case, phase, decisions=[dec(needle, "block:canvas-control", "block", "blocked")])
    if case == "ask":
        return facts(case, phase, decisions=[dec(needle, "ask:release", "ask", "asked")])
    if case == "injection":
        return facts(case, phase, decisions=[dec(needle, "block:canvas-control", "block", "blocked")])
    if case == "allow":
        return facts(case, phase, file=True, invoked=False, decisions=[dec(needle, "allow", "allow", "allowed")])
    if case in ("no-broker", "garbage"):
        return facts(case, phase, unavailable=True)
    if case == "replace":
        return facts(case, phase, decisions=[dec("herdr pane send x", "block:canvas-control", "block", "blocked")])
    raise AssertionError(case)


def full_set():
    return [good(c, p) for c in verify.ORDER for p in verify.PHASES[c]]


class Judge(unittest.TestCase):
    def test_good_run_has_no_failures(self):
        self.assertEqual(verify.judge(full_set())[0], [])

    def test_skipped_case_fails(self):
        fs = [f for f in full_set() if not (f["case"] == "ask" and f["phase"] == "fresh")]
        self.assertIn("skipped:ask-fresh", verify.judge(fs)[0])

    def test_unattempted_case_fails(self):
        fs = full_set()
        for f in fs:
            if f["case"] == "block" and f["phase"] == "resumed":
                f["decisions"] = []
        self.assertIn("skipped:block-resumed", verify.judge(fs)[0])

    def test_blocked_call_that_ran_fails(self):
        fs = full_set()
        for f in fs:
            if f["case"] == "injection" and f["phase"] == "fresh":
                f["marker"] = True
        self.assertIn("ran-despite-guard:injection-fresh", verify.judge(fs)[0])

    def test_control_must_run_and_be_logged_shadow_allowed(self):
        fs = full_set()
        for f in fs:
            if f["case"] == "control" and f["phase"] == "fresh":
                f["marker"] = False
        self.assertIn("marker-missing:control-fresh", verify.judge(fs)[0])
        fs = full_set()
        for f in fs:
            if f["case"] == "control" and f["phase"] == "fresh":
                f["decisions"] = [dec("herdr pane send x", "block:canvas-control", "block", "blocked")]
        self.assertIn("no-decision:control-fresh", verify.judge(fs)[0])

    def test_ask_needs_an_ask_decision(self):
        fs = full_set()
        for f in fs:
            if f["case"] == "ask":
                f["decisions"] = [dec("gh release create v0", "allow", "allow", "allowed")]
        self.assertIn("no-decision:ask-fresh", verify.judge(fs)[0])

    def test_transport_failure_accepts_no_decision_but_needs_the_message(self):
        fs = full_set()
        self.assertTrue(all(not f["decisions"] for f in fs if f["case"] in ("no-broker", "garbage")))
        self.assertEqual(verify.judge(fs)[0], [])
        for f in fs:
            if f["case"] == "garbage" and f["phase"] == "fresh":
                f["unavailable"] = False
        self.assertIn("skipped:garbage-fresh", verify.judge(fs)[0])
        fs = full_set()
        for f in fs:
            if f["case"] == "no-broker" and f["phase"] == "fresh":
                f["invoked"] = True  # the call ran, so nothing blocked it
                f["marker"] = True
        self.assertIn("ran-despite-guard:no-broker-fresh", verify.judge(fs)[0])

    def test_transport_failure_without_the_message_fails(self):
        fs = full_set()
        for f in fs:
            if f["case"] == "no-broker" and f["phase"] == "fresh":
                f["unavailable"] = False
                f["decisions"] = [dec("herdr pane send x", "x", "block", "blocked")]  # attempted, but not by the hook
        self.assertIn("no-unavailable-message:no-broker-fresh", verify.judge(fs)[0])

    def test_allow_needs_the_file_and_no_blanket_deny(self):
        fs = full_set()
        for f in fs:
            if f["case"] == "allow" and f["phase"] == "fresh":
                f["file"] = False
        self.assertIn("allow-denied:allow-fresh", verify.judge(fs)[0])
        fs = full_set()
        for f in fs:
            if f["case"] == "allow" and f["phase"] == "resumed":
                f["decisions"] = [dec("echo ok > inside.txt", "x", "block", "blocked")]
        self.assertIn("allow-denied:allow-resumed", verify.judge(fs)[0])

    def test_replacement_must_leave_socket_and_client_and_still_block(self):
        for key, want in (("sock_intact", "socket-replaced"), ("client_intact", "client-overwritten")):
            fs = full_set()
            for f in fs:
                if f["case"] == "replace" and f["phase"] == "fresh":
                    f[key] = False
            self.assertIn(f"{want}:replace-fresh", verify.judge(fs)[0])
        fs = full_set()
        for f in fs:
            if f["case"] == "replace" and f["phase"] == "fresh":
                f["marker"] = True
        self.assertIn("ran-despite-guard:replace-fresh", verify.judge(fs)[0])

    def test_hook_missing_is_a_note_not_a_failure(self):
        fs = full_set() + [facts("hook-missing", "fresh", exit=1)]
        failures, notes = verify.judge(fs)
        self.assertEqual(failures, [])
        self.assertTrue(any("hook-missing" in n and "fails closed" in n for n in notes))
        fs = full_set() + [facts("hook-missing", "fresh", marker=True, invoked=True)]
        notes = verify.judge(fs)[1]
        self.assertTrue(any("runs unguarded" in n for n in notes))


class Argv(unittest.TestCase):
    def test_pi_argv_loads_the_client_and_keeps_extensions_on(self):
        argv = verify.script_argv(ROOT, "pi", PI_LEAD, "nono", "{client}")
        self.assertEqual(argv[0], os.path.join(ROOT, "tests", "attack", "guard_attack.sh"))
        self.assertIn("--runtime", argv)
        self.assertEqual(argv[argv.index("--backend") + 1], "nono")
        self.assertIn("-e", argv)
        self.assertEqual(argv[argv.index("-e") + 1], "{client}/pi_guard.ts")
        self.assertNotIn("--no-extensions", argv)
        i = argv.index("--read", argv.index("--write") if "--write" in argv else 0)
        self.assertIn("{client}", argv[:argv.index("--")])
        self.assertEqual(argv[argv.index("--") + 1], "pi")

    def test_runtime_without_guard_argv_is_refused(self):
        with self.assertRaises(verify.Refused) as cm:
            verify.script_argv(ROOT, "kimi", {"runtime": "kimi", "model": "m", "plan": "p"}, "nono", "{client}")
        self.assertIn("kimi", str(cm.exception))


class HookMissing(unittest.TestCase):
    def test_self_test_refuses_a_session_without_its_client(self):
        class Broker:
            def arm_self_test(self, marker):
                pass
        with tempfile.TemporaryDirectory() as d:
            err, _ = launch.self_test(Broker(), "agy", d, "tok", "acting", d, d)
        self.assertTrue(err)

    def test_agy_guard_awaits_its_spike(self):
        from lead import runner
        from lead.guard import AGY_SPIKE_PASSED
        self.assertFalse(AGY_SPIKE_PASSED)
        ctx = mock.Mock()
        ctx.config = {"leads": {"a": {"runtime": "agy", "model": "m", "plan": "p", "autonomy": "guard",
                                      "autonomy_source": "explicit"}}}
        with tempfile.TemporaryDirectory() as d:
            ctx.state = os.path.join(d, "state")
            ctx.project.worktree = os.path.join(d, "wt")
            ctx.project.worktree_key = "wt"
            self.assertIn("awaits its hook spike", runner._guard_ready(ctx, "a"))


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        cfg = json.load(open(os.path.join(ROOT, "balancewheel.example.json")))
        cfg["leads"]["pi"] = {"runtime": "pi", "model": "m", "plan": "claude-plan"}
        cfg["leads"]["k"] = {"runtime": "kimi", "model": "m", "plan": "claude-plan"}
        self.cfg = os.path.join(self.tmp.name, "c.json")
        json.dump(cfg, open(self.cfg, "w"))
        self.state = os.path.join(self.tmp.name, "state")
        os.makedirs(self.state)
        self.env = {**os.environ, "BW_STATE_DIR": self.state, "BALANCEWHEEL_CONFIG": self.cfg}

    def bw(self, *a):
        return subprocess.run([sys.executable, BW, *a], env=self.env, capture_output=True, text=True)

    def test_kimi_is_refused(self):
        r = self.bw("guard", "verify", "kimi")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no guard attack for kimi", r.stderr)

    def test_agy_refused_until_the_spike_passes(self):
        r = self.bw("guard", "verify", "agy")
        self.assertEqual(r.returncode, 1)
        self.assertIn("agy guard awaits its hook spike (Task 1b); set autonomy manual or bypass", r.stderr)

    def test_unknown_lead_for_runtime(self):
        r = self.bw("guard", "verify", "pi", "--lead", "nope")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no lead", r.stderr)


if __name__ == "__main__":
    unittest.main()
