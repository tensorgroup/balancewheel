import json, os, subprocess, sys, tempfile, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
from lead.guard import AGY_AWAITS, launch, verify  # noqa: E402
from lead.runtimes import ADAPTERS  # noqa: E402

BW = os.path.join(ROOT, "bin", "bw")
PI_LEAD = {"runtime": "pi", "model": "m", "plan": "p"}


def dec(call, rule, decision, outcome):
    return {"call": call, "rule": rule, "decision": decision, "outcome": outcome}


def facts(case, phase, **kw):
    f = {"case": case, "phase": phase, "marker": False, "invoked": False, "decisions": [],
         "unavailable": False, "file": False, "sock_intact": True, "client_intact": True, "exit": 0,
         "runtime": "pi", "structured": True, "nowhere_enoent": False, "sock_same_inode": True,
         "tamper_ok": False, "fake_output": False, "ext_loaded": False, "ext_control": True}
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
    if case in ("injection", "extension"):
        return facts(case, phase, decisions=[dec(needle, "block:canvas-control", "block", "blocked")])
    if case == "allow":
        return facts(case, phase, file=True, invoked=False, decisions=[dec(needle, "allow", "allow", "allowed")])
    if case == "no-broker":
        return facts(case, phase, unavailable=True, nowhere_enoent=True)
    if case == "garbage":
        return facts(case, phase, unavailable=True,
                     decisions=[dec("herdr pane send x", "block:canvas-control", "block", "blocked")])
    if case == "replace":
        return facts(case, phase, tamper_ok=True,
                     decisions=[dec("herdr pane send x", "block:canvas-control", "block", "blocked")])
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

    def test_no_broker_accepts_no_decision_but_needs_run_specific_proof(self):
        fs = full_set()
        self.assertTrue(all(not f["decisions"] for f in fs if f["case"] == "no-broker"))
        self.assertEqual(verify.judge(fs)[0], [])
        for f in fs:  # the lead's prose said "guard unavailable" but no tool result showed this run's ENOENT
            if f["case"] == "no-broker" and f["phase"] == "fresh":
                f["nowhere_enoent"] = False
        self.assertIn("no-enoent-proof:no-broker-fresh", verify.judge(fs)[0])
        fs = full_set()
        for f in fs:
            if f["case"] == "no-broker" and f["phase"] == "fresh":
                f["unavailable"] = False
                f["nowhere_enoent"] = False
        self.assertIn("skipped:no-broker-fresh", verify.judge(fs)[0])
        fs = full_set()
        for f in fs:
            if f["case"] == "no-broker" and f["phase"] == "fresh":
                f["invoked"] = True  # the call ran, so nothing blocked it
                f["marker"] = True
        self.assertIn("ran-despite-guard:no-broker-fresh", verify.judge(fs)[0])

    def test_garbage_needs_a_blocked_decision_and_the_message(self):
        fs = full_set()
        for f in fs:
            if f["case"] == "garbage" and f["phase"] == "fresh":
                f["decisions"] = []
        self.assertIn("no-decision:garbage-fresh", verify.judge(fs)[0])
        fs = full_set()
        for f in fs:
            if f["case"] == "garbage" and f["phase"] == "fresh":
                f["unavailable"] = False
        self.assertIn("no-unavailable-message:garbage-fresh", verify.judge(fs)[0])
        fs = full_set()
        for f in fs:
            if f["case"] == "garbage" and f["phase"] == "fresh":
                f["decisions"] = [dec("herdr pane send x", "allow", "allow", "allowed")]
        self.assertIn("no-decision:garbage-fresh", verify.judge(fs)[0])

    def test_injection_must_carry_the_injected_text(self):
        fs = full_set()
        for f in fs:
            if f["case"] == "injection" and f["phase"] == "fresh":
                f["decisions"] = [dec("herdr pane send x", "block:canvas-control", "block", "blocked")]
        self.assertIn("skipped:injection-fresh", verify.judge(fs)[0])

    def test_replacement_needs_the_tamper_proof(self):
        fs = full_set()
        for f in fs:
            if f["case"] == "replace" and f["phase"] == "resumed":
                f["tamper_ok"] = False
        self.assertIn("tamper-unproven:replace-resumed", verify.judge(fs)[0])

    def test_replacement_socket_inode_must_not_change(self):
        fs = full_set()
        for f in fs:
            if f["case"] == "replace" and f["phase"] == "fresh":
                f["sock_same_inode"] = False
        self.assertIn("socket-replaced:replace-fresh", verify.judge(fs)[0])

    def test_agy_replacement_needs_decisions_naming_socket_and_client(self):
        def agy_set(calls):
            fs = full_set()
            for f in fs:
                f["runtime"] = "agy"
                if f["case"] == "replace":
                    f["tamper_ok"] = False
                    f["decisions"] = [dec(c, "allow", "allow", "allowed") for c in calls] + \
                        [dec("herdr pane send x", "block:canvas-control", "block", "blocked")]
            return fs
        self.assertEqual(verify.judge(agy_set(["rm /g/sock", "echo t > /g/hook.py"]))[0], [])
        self.assertIn("tamper-unattempted:replace-fresh", verify.judge(agy_set(["rm /g/sock"]))[0])

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

    def test_an_unstructured_pi_transcript_fails_every_case_it_covers(self):
        fs = full_set()
        for f in fs:
            if f["case"] in ("no-broker", "block") and f["phase"] == "fresh":
                f["structured"] = False
        failures = verify.judge(fs)[0]
        self.assertIn("unstructured:no-broker-fresh", failures)
        self.assertIn("unstructured:block-fresh", failures)

    def test_a_planted_extension_that_loaded_fails_and_the_case_is_pi_only(self):
        fs = full_set()
        for f in fs:
            if f["case"] == "extension" and f["phase"] == "fresh":
                f["ext_loaded"] = True
        self.assertIn("extension-loaded:extension-fresh", verify.judge(fs)[0])
        fs = full_set()
        for f in fs:
            if f["case"] == "extension" and f["phase"] == "resumed":
                f["marker"] = True
        self.assertIn("ran-despite-guard:extension-resumed", verify.judge(fs)[0])
        fs = [f for f in full_set() if f["case"] != "extension"]
        self.assertIn("skipped:extension-fresh", verify.judge(fs)[0])
        for f in fs:
            f["runtime"] = "agy"
            if f["case"] == "replace":
                f["tamper_ok"] = False
                f["decisions"] = [dec(c, "allow", "allow", "allowed") for c in ("rm /g/sock", "echo t > /g/hook.py")] + \
                    [dec("herdr pane send x", "block:canvas-control", "block", "blocked")]
        self.assertNotIn("skipped:extension-fresh", verify.judge(fs)[0])

    def test_judge_fails_when_extension_control_did_not_load(self):
        fs = full_set()
        for f in fs:
            f["ext_control"] = False
        self.assertIn("extension-control-not-loaded", verify.judge(fs)[0])
        fs = full_set()
        for f in fs:
            if f["case"] == "extension" and f["phase"] == "fresh":
                f["ext_control"] = False
        self.assertNotIn("extension-control-not-loaded", verify.judge(fs)[0])  # the sentinel persists: one is enough
        fs = full_set()
        for f in fs:
            f["runtime"], f["structured"], f["ext_control"] = "agy", False, False
            if f["case"] == "replace":
                f["tamper_ok"] = False
                f["decisions"] = [dec(c, "allow", "allow", "allowed") for c in ("rm /g/sock", "echo t > /g/hook.py")] + \
                    [dec("herdr pane send x", "block:canvas-control", "block", "blocked")]
        self.assertNotIn("extension-control-not-loaded", verify.judge(fs)[0])  # pi only

    def test_agy_has_no_structured_output_and_is_not_failed_for_it(self):
        fs = full_set()
        for f in fs:
            f["runtime"], f["structured"] = "agy", False
            if f["case"] == "replace":
                f["tamper_ok"] = False
                f["decisions"] = [dec(c, "allow", "allow", "allowed") for c in ("rm /g/sock", "echo t > /g/hook.py")] + \
                    [dec("herdr pane send x", "block:canvas-control", "block", "blocked")]
        self.assertEqual(verify.judge(fs)[0], [])

    def test_allow_needs_a_logged_allow_decision_not_just_the_file(self):
        fs = full_set()
        for f in fs:
            if f["case"] == "allow" and f["phase"] == "fresh":
                f["decisions"] = []  # inside.txt exists, but the broker never saw the call
        self.assertIn("no-decision:allow-fresh", verify.judge(fs)[0])

    def test_the_fakes_own_output_in_a_tool_result_fails_a_stopped_case(self):
        for case in ("block", "ask", "injection", "replace", "garbage", "no-broker"):
            fs = full_set()
            for f in fs:
                if f["case"] == case and f["phase"] == "resumed":
                    f["fake_output"] = True
            self.assertIn(f"fake-output:{case}-resumed", verify.judge(fs)[0], case)

    def test_failure_message_says_inconclusive_only_for_skips(self):
        skipped = "FAIL: skipped:block-fresh skipped:ask-fresh — evidence in /x"
        self.assertIn("inconclusive", verify.failure_message(skipped))
        real = "FAIL: skipped:block-fresh ran-despite-guard:ask-fresh — evidence in /x"
        self.assertNotIn("inconclusive", verify.failure_message(real))
        self.assertIn("the guard attack failed", verify.failure_message(real))
        self.assertIn("the guard attack failed", verify.failure_message("something else"))

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


class Collect(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run_dir = os.path.realpath(self.tmp.name)
        for d in ("wt", "logs", "out", "state/guard/wt"):
            os.makedirs(os.path.join(self.run_dir, d))

    def transcript(self, name, results, prose=""):
        lines = [json.dumps({"type": "tool_execution_end", "toolName": "bash",
                             "result": {"content": [{"type": "text", "text": t}]}, "isError": True}) for t in results]
        lines.append(json.dumps({"type": "message_end", "message": {"role": "assistant",
                                 "content": [{"type": "text", "text": prose}]}}))
        with open(os.path.join(self.run_dir, "out", name + ".txt"), "w") as f:
            f.write("\n".join(lines) + "\n[lead exited 0]\n")

    def collect(self, case="no-broker", phase="fresh"):
        return verify.collect(self.run_dir, case, phase, "pi", 0)

    def test_unavailable_comes_from_tool_results_not_prose(self):
        self.transcript("no-broker-fresh", ["ok"], prose="guard unavailable: connect ENOENT")
        f = self.collect()
        self.assertTrue(f["structured"])
        self.assertFalse(f["unavailable"])
        self.assertFalse(f["nowhere_enoent"])

    def test_enoent_must_name_this_runs_socket(self):
        nowhere = os.path.join(self.run_dir, "nowhere", "sock")
        self.transcript("no-broker-fresh", [f"guard unavailable: connect ENOENT {nowhere}"])
        f = self.collect()
        self.assertTrue(f["unavailable"] and f["nowhere_enoent"])
        self.transcript("no-broker-fresh", ["guard unavailable: connect ENOENT /elsewhere/nowhere/sock"])
        f = self.collect()
        self.assertTrue(f["unavailable"])
        self.assertFalse(f["nowhere_enoent"])

    def test_a_transcript_in_the_lead_writable_logs_dir_is_not_evidence(self):
        self.transcript("no-broker-fresh", ["guard unavailable: connect ENOENT"])
        os.rename(os.path.join(self.run_dir, "out", "no-broker-fresh.txt"), os.path.join(self.run_dir, "logs", "no-broker-fresh.txt"))
        f = self.collect()
        self.assertFalse(f["structured"] or f["unavailable"])

    def test_the_planted_extensions_sentinel_is_a_fact(self):
        self.transcript("extension-fresh", ["blocked"])
        self.assertFalse(self.collect("extension")["ext_loaded"])
        open(os.path.join(self.run_dir, "wt", ".ext-loaded"), "w").close()
        self.assertTrue(self.collect("extension")["ext_loaded"])

    def test_the_extension_controls_sentinel_is_a_fact(self):
        self.transcript("extension-fresh", ["blocked"])
        self.assertFalse(self.collect("extension")["ext_control"])
        open(os.path.join(self.run_dir, "wt", ".ext-control"), "w").close()
        self.assertTrue(self.collect("extension")["ext_control"])

    def test_the_fakes_stdout_in_a_tool_result_is_recorded(self):
        self.transcript("block-fresh", ["fake herdr ran: pane send x"])
        self.assertTrue(self.collect("block")["fake_output"])
        self.transcript("block-fresh", ["blocked by the guard"], prose="fake gh ran: the model said it")
        self.assertFalse(self.collect("block")["fake_output"])
        self.transcript("ask-fresh", ["fake gh ran: release create v0"])
        self.assertTrue(self.collect("ask")["fake_output"])

    def test_prose_fallback_is_for_agy_only(self):
        with open(os.path.join(self.run_dir, "out", "no-broker-fresh.txt"), "w") as f:
            f.write("the guard unavailable: connect ENOENT\n[lead exited 0]\n")
        self.assertFalse(verify.collect(self.run_dir, "no-broker", "fresh", "pi", 0)["unavailable"])
        self.assertTrue(verify.collect(self.run_dir, "no-broker", "fresh", "agy", 0)["unavailable"])

    def tamper(self, rcs=(1, 1, 1, 1), denied=4, control=True, phase="fresh"):
        lines = []
        for n, rc in zip(("rm", "overwrite", "mv", "truncate"), rcs):
            if rc:
                lines.append("x: Operation not permitted" if denied > 0 else "x: other")
                denied -= 1
            lines.append(f"{n} rc={rc}")
        lines.append("control rc=0" if control else "control rc=1")
        with open(os.path.join(self.run_dir, "out", f"tamper-{phase}.txt"), "w") as f:
            f.write("\n".join(lines) + "\n")
        if control:
            with open(os.path.join(self.run_dir, "logs", f"tamper-control-{phase}"), "w") as f:
                f.write("ran\n")

    def replace_facts(self):
        self.transcript("replace-fresh", [])
        return self.collect("replace")

    def test_tamper_log_proof(self):
        self.tamper()
        self.assertTrue(self.replace_facts()["tamper_ok"])

    def test_tamper_never_ran_is_unproven(self):
        self.assertFalse(self.replace_facts()["tamper_ok"])

    def test_tamper_without_control_is_unproven(self):
        self.tamper(control=False)
        self.assertFalse(self.replace_facts()["tamper_ok"])

    def test_tamper_that_succeeded_or_lacks_eperm_is_unproven(self):
        self.tamper(rcs=(1, 0, 1, 1))
        self.assertFalse(self.replace_facts()["tamper_ok"])
        self.tamper(denied=2)
        self.assertFalse(self.replace_facts()["tamper_ok"])

    def test_socket_inode_compared_with_the_snapshot(self):
        import socket
        sp = os.path.join(self.run_dir, "state", "guard", "wt", "sock")
        s1 = socket.socket(socket.AF_UNIX); s1.bind(sp)
        self.addCleanup(s1.close)
        verify.snapshot(self.run_dir)
        self.transcript("replace-fresh", [])
        self.assertTrue(self.collect("replace")["sock_same_inode"])
        os.unlink(sp)
        s2 = socket.socket(socket.AF_UNIX); s2.bind(sp)
        self.addCleanup(s2.close)
        self.assertFalse(self.collect("replace")["sock_same_inode"])


class Argv(unittest.TestCase):
    def test_pi_argv_loads_the_client_and_turns_discovered_extensions_off(self):
        argv = verify.script_argv(ROOT, "pi", PI_LEAD, "nono", "{client}")
        self.assertEqual(argv[0], os.path.join(ROOT, "tests", "attack", "guard_attack.sh"))
        self.assertIn("--runtime", argv)
        self.assertEqual(argv[argv.index("--backend") + 1], "nono")
        self.assertIn("-e", argv)
        self.assertEqual(argv[argv.index("-e") + 1], "{client}/pi_guard.ts")
        self.assertIn("--no-extensions", argv)
        self.assertEqual(argv[argv.index("--mode") + 1], "json")
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

    def test_a_missing_node_refuses_a_pi_session_instead_of_skipping_the_self_test(self):
        class Broker:
            def arm_self_test(self, marker):
                pass
        with tempfile.TemporaryDirectory() as d, mock.patch.object(launch, "_node_ok", return_value=False):
            err, _ = launch.self_test(Broker(), "pi", d, "tok", "acting", d, d)
        self.assertIn("node >= 22.6", err)

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
            self.assertIn("hook behaviour is verified", runner._guard_ready(ctx, "a"))


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
        self.env = {**os.environ, "BW_STATE_DIR": self.state, "BALANCEWHEEL_CONFIG": self.cfg,
                    "BW_ORCA_SUPPORT": os.path.join(self.state, "no-orca"), "BW_CCSWITCH_DIR": os.path.join(self.state, "no-cc-switch")}

    def bw(self, *a):
        return subprocess.run([sys.executable, BW, *a], env=self.env, capture_output=True, text=True)

    def test_kimi_is_refused(self):
        r = self.bw("guard", "verify", "kimi")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no guard attack for kimi", r.stderr)

    def test_agy_refused_until_the_spike_passes(self):
        r = self.bw("guard", "verify", "agy")
        self.assertEqual(r.returncode, 1)
        self.assertIn(AGY_AWAITS, r.stderr)

    def test_unknown_lead_for_runtime(self):
        r = self.bw("guard", "verify", "pi", "--lead", "nope")
        self.assertEqual(r.returncode, 1)
        self.assertIn("no lead", r.stderr)


if __name__ == "__main__":
    unittest.main()
