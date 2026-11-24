import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, ROOT)
from lead import containment, sandbox  # noqa: E402
from lead.guard import interactive, launch, record  # noqa: E402
from lead.runtimes import ADAPTERS  # noqa: E402

NO, YES = "bwgi-no-aaa", "bwgi-yes-bbb"


def call(nonce, decision="ask", outcome="asked"):
    return {"call": f"gh issue comment 1 --body {nonce}", "decision": decision, "outcome": outcome}


GOOD = [call(NO), call(YES)]


class Tty(io.StringIO):
    def __init__(self, text="", tty=True):
        super().__init__(text)
        self.tty = tty

    def isatty(self):
        return self.tty


class VerdictTests(unittest.TestCase):
    def test_verdict_passes_only_when_no_blocked_and_yes_ran(self):
        self.assertEqual(interactive.verdict(GOOD, [f"gh issue comment 2 --body {YES}"], NO, YES, True), (True, "ok"))

    def test_verdict_fails_when_the_no_command_ran(self):
        ok, why = interactive.verdict(GOOD, [f"gh x {NO}", f"gh y {YES}"], NO, YES, True)
        self.assertFalse(ok)
        self.assertIn("declined ran", why)

    def test_verdict_fails_when_the_yes_command_did_not_run(self):
        ok, why = interactive.verdict(GOOD, [], NO, YES, True)
        self.assertFalse(ok)
        self.assertIn("approved did not run", why)

    def test_verdict_fails_without_two_asked_decisions(self):
        for decisions in ([call(NO)], [call(NO), call(YES, outcome="blocked")], [call(NO), call(YES, decision="allow")]):
            ok, why = interactive.verdict(decisions, [YES], NO, YES, True)
            self.assertFalse(ok)
            self.assertIn("did not log an ask", why)

    def test_verdict_fails_when_the_user_says_no(self):
        ok, why = interactive.verdict(GOOD, [YES], NO, YES, False)
        self.assertFalse(ok)
        self.assertIn("did not confirm", why)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(dir="/tmp"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.state = os.path.join(self.tmp, "state")
        self.clients = os.path.join(self.tmp, "clients")
        os.makedirs(self.clients)
        for name in ("hook.py", "pi_guard.ts"):
            shutil.copyfile(os.path.join(interactive.CLIENT_DIR, name), os.path.join(self.clients, name))
        p = mock.patch.object(interactive, "CLIENT_DIR", self.clients)
        p.start()
        self.addCleanup(p.stop)
        self.hook = os.path.join(self.tmp, "settings.json")
        with open(self.hook, "w") as f:
            f.write("{}")
        self.adapter = ADAPTERS["pi"]._replace(guard_hook_files=lambda: [self.hook])
        p = mock.patch.dict(ADAPTERS, {"pi": self.adapter})
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(sandbox, "backend", return_value="sandbox-exec")
        p.start()
        self.addCleanup(p.stop)
        self.config = {"leads": {"lp": {"runtime": "pi", "model": "m", "plan": "pp", "autonomy": "bypass",
                                        "autonomy_source": "config"}},
                       "profiles": {"p": {"lead_order": ["lp"]}}, "guard": {"mode": {"p": "auto"}}}


class KeyAndRefusalTests(Base):
    def test_check_key_ignores_the_runtime_version(self):
        a = self.adapter._replace(version_argv=["echo", "pi 1.0"])
        b = self.adapter._replace(version_argv=["echo", "pi 2.0"])
        self.assertEqual(interactive.check_key("pi", a), interactive.check_key("pi", b))

    def test_check_key_changes_with_the_client_file(self):
        before = interactive.check_key("pi", self.adapter)
        with open(os.path.join(self.clients, "pi_guard.ts"), "a") as f:
            f.write("\n// changed\n")
        self.assertNotEqual(before, interactive.check_key("pi", self.adapter))

    def test_refused_without_a_tty(self):
        ok, msg = interactive.run_check(self.config, self.state, "pi", None, Tty(tty=False), Tty())
        self.assertFalse(ok)
        self.assertIn("needs a terminal", msg)
        ok, msg = interactive.run_check(self.config, self.state, "pi", None, Tty(), Tty(tty=False))
        self.assertFalse(ok)

    def test_agy_refused_until_its_spike(self):
        ok, msg = interactive.run_check(self.config, self.state, "agy", None, Tty(), Tty())
        self.assertFalse(ok)
        self.assertIn("agy guard is unavailable", msg)

    def test_argv_carries_the_client_for_a_bypass_lead(self):
        lead = self.config["leads"]["lp"]
        project = mock.Mock(worktree=self.tmp + "/wt", worktree_key="k", common_dir=self.tmp + "/wt")
        run_state = os.path.join(self.tmp, "rs")
        os.makedirs(self.tmp + "/bin")
        os.makedirs(self.tmp + "/wt")
        with mock.patch.object(sandbox, "_git_dir", return_value=self.tmp + "/wt"):
            argv, grants, _ = interactive.plan(lead, self.adapter, project, run_state, self.clients,
                                               self.tmp + "/bin", self.tmp + "/wit", "hi")
        self.assertIn("--no-extensions", argv)
        self.assertEqual(argv[argv.index("-e") + 1], os.path.join(self.clients, "pi_guard.ts"))
        self.assertIn(self.clients, grants.read)
        self.assertIn(self.tmp + "/bin", grants.read)
        self.assertFalse(os.path.exists(os.path.join(self.state, "handoffs")))  # the real state dir is untouched


def _node_ok():
    return launch._node_ok() and os.path.exists("/usr/bin/nc")


@unittest.skipUnless(_node_ok(), "needs node >= 22.6 and nc")
class RunTests(Base):
    def drive(self, mutate=None, answer="y"):
        """A fake run_tty: drives the copied client through the pi harness, No then Yes."""
        seen = {}

        def run_tty(argv, cwd=None, env=None):
            seen.update(argv=argv, cwd=cwd, env=env)
            prompt = argv[-1]
            no, yes = (re.search(rf"bwgi-{k}-\w+", prompt).group(0) for k in ("no", "yes"))
            client = os.path.join(env["BW_GUARD_DIR"], "pi_guard.ts")
            for nonce, pick in ((no, "No"), (yes, "Yes")):
                cmd = f"gh issue comment 1 --body {nonce}"
                sc = {"event": {"type": "tool_call", "toolName": "bash", "toolCallId": nonce, "input": {"command": cmd}},
                      "ctx": {"hasUI": True, "cwd": cwd}, "ui": {"select": pick}, "run": cmd}
                path = os.path.join(self.tmp, f"sc-{pick}.json")
                with open(path, "w") as f:
                    json.dump(sc, f)
                subprocess.run(launch.NODE_ARGV + [launch.HARNESS, client, path], env=env, capture_output=True, timeout=60)
            if mutate:
                mutate()
            return 0

        out = Tty()
        result = interactive.run_check(self.config, self.state, "pi", "lp", Tty(answer + "\n"), out, run_tty=run_tty)
        return result, seen, out

    def test_run_check_records_on_the_check_key(self):
        (ok, msg), seen, out = self.drive()
        self.assertTrue(ok, msg)
        self.assertIn("No the first time and Yes the second", out.getvalue())
        key = interactive.check_key("pi", self.adapter)
        self.assertTrue(interactive.is_checked(self.state, "pi", key))
        self.assertIn("--no-extensions", seen["argv"])  # a bypass lead still gets the client

    def test_run_dir_under_tmp_and_socket_under_103_bytes(self):
        (ok, _), seen, _ = self.drive()
        sock = seen["env"]["BW_GUARD_SOCKET"]
        self.assertTrue(sock.startswith(os.path.realpath("/tmp") + "/bw-gi-"), sock)
        self.assertLess(len(os.fsencode(sock)), 103)

    def test_check_writes_only_its_own_guard_log(self):
        (ok, _), seen, _ = self.drive()
        self.assertFalse(os.path.exists(record.path(self.state)))
        run_dir = os.path.dirname(seen["cwd"])
        self.assertTrue(os.path.exists(record.path(os.path.join(run_dir, "state"))))
        self.assertFalse(os.path.exists(os.path.join(self.state, "handoffs")))

    def test_the_user_saying_no_records_nothing(self):
        (ok, msg), _, _ = self.drive(answer="n")
        self.assertFalse(ok)
        self.assertFalse(os.path.exists(os.path.join(self.state, interactive.STORE)))

    def test_key_change_during_the_run_records_nothing(self):
        def change():
            with open(os.path.join(self.clients, "pi_guard.ts"), "a") as f:
                f.write("\n// changed\n")
        (ok, msg), _, _ = self.drive(mutate=change)
        self.assertFalse(ok)
        self.assertIn("key changed", msg)
        self.assertFalse(os.path.exists(os.path.join(self.state, interactive.STORE)))


if __name__ == "__main__":
    unittest.main()
