"""`bw run` (lead/run_cli.py): the wrap form, start/end, heartbeats, and the sink order (PD-13, PD-14)."""
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from lead import run_cli, runs  # noqa: E402
from metrics import store  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
EXIT = str(FIX / "exit_with.py")
SLEEPER = str(FIX / "sleeper.py")
BW = str(ROOT / "bin" / "bw")


def wait_for(pred, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


class RunCli(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = os.path.join(self.tmp, "config.json")
        self.log = os.path.join(self.tmp, "metrics.jsonl")
        self.flag = os.path.join(self.tmp, "flag")
        self.write_config()
        self.env = {k: v for k, v in os.environ.items() if k != "BW_CHECKPOINT"}
        self.env.update({"BALANCEWHEEL_CONFIG": self.cfg, "BALANCEWHEEL_METRICS_LOG": self.log,
                         "BW_STATE_DIR": os.path.join(self.tmp, "state")})
        patch = mock.patch.dict(os.environ, self.env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)

    def write_config(self, local=None):
        obj = json.loads((FIX / "config_v3.json").read_text())
        obj["state_dir"] = os.path.join(self.tmp, "state")
        if local is not None:
            obj["local"] = local
        Path(self.cfg).write_text(json.dumps(obj))

    def main(self, argv):
        return self.main_env({}, argv)[0]

    def main_env(self, env, argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env):
            rc = run_cli.main(argv, out, err)
        self.last_out = out.getvalue()
        return rc, err.getvalue()

    def capture(self, argv):
        self.assertEqual(self.main(argv), 0)
        return self.last_out

    def runs(self):
        if not os.path.exists(self.log):
            return []
        return [e for e in store.read_events(self.log)[0] if e.get("type") == "run"]

    def test_wrap_ok_passes_status_and_writes_two_rows(self):
        self.assertEqual(self.main(["--seat", "my-seat", "--label", "lint", "--", sys.executable, EXIT, "0"]), 0)
        s, e = self.runs()
        self.assertEqual((s["source"], s["seat"], s["label"], s["pid"], e["ok"], e["reason"]),
                         ("local", "my-seat", "lint", os.getpid(), True, None))
        self.assertEqual((s["run_id"], e["seat"], e["dir"]), (e["run_id"], "my-seat", s["dir"]))
        self.assertIn("host", s)
        self.assertEqual(s["dir"], os.path.realpath(str(ROOT)))  # the cwd resolves to the worktree root

    def test_wrap_failure_and_signal(self):  # Review Focus 1
        self.assertEqual(self.main(["--seat", "s", "--", sys.executable, EXIT, "3"]), 3)
        self.assertEqual(self.runs()[-1]["reason"], "exit 3")
        self.assertIs(self.runs()[-1]["ok"], False)
        self.assertEqual(self.main(["--seat", "s", "--", sys.executable, EXIT, "0", "15"]), 143)
        self.assertEqual(self.runs()[-1]["reason"], "signal 15")
        rc, err = self.main_env({}, ["--seat", "s", "--", "no-such-command-xyz"])
        self.assertEqual(rc, 127)
        self.assertIn("bw run: no-such-command-xyz: not found", err)
        self.assertEqual(self.runs()[-1]["reason"], "exit 127")
        script = Path(self.tmp, "noexec.sh"); script.write_text("#!/bin/sh\n"); script.chmod(0o644)
        rc, err = self.main_env({}, ["--seat", "s", "--", str(script)])
        self.assertEqual(rc, 126)
        self.assertIn("not executable", err)
        self.assertEqual((self.runs()[-1]["reason"], len([r for r in self.runs() if r["phase"] == "end"])),
                         ("exit 126", 4))

    def test_signal_handlers_are_restored(self):
        before = (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM))
        self.main(["--seat", "s", "--", sys.executable, EXIT, "0"])
        self.assertEqual((signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)), before)

    def test_sigterm_to_bw_run_reaches_the_child(self):
        p = subprocess.Popen([sys.executable, BW, "run", "--seat", "s", "--", sys.executable, SLEEPER, self.flag],
                             env=self.env)
        try:
            self.assertTrue(wait_for(lambda: os.path.exists(self.flag)))
            p.send_signal(signal.SIGTERM); p.wait(10)
        finally:
            if p.poll() is None:
                p.kill(); p.wait()
        self.assertTrue(os.path.exists(self.flag + ".term"))
        self.assertEqual(p.returncode, 0)  # the child caught it and exited 0
        self.assertEqual([r["phase"] for r in self.runs()], ["start", "end"])

    def sigint_to_bw_run(self, foreground):
        """SIGINT to this process (bw run) only, while a child that counts SIGINTs runs; returns the count."""
        count = os.path.join(self.tmp, "ints")
        child = ("import signal, sys, time\n"
                 "signal.signal(signal.SIGINT, lambda n, f: open(sys.argv[1], 'a').write('i\\n'))\n"
                 "open(sys.argv[2], 'w').close()\n"
                 "end = time.time() + 2\n"
                 "while time.time() < end: time.sleep(0.05)\n")
        if os.path.exists(self.flag):
            os.unlink(self.flag)

        def send():
            if wait_for(lambda: os.path.exists(self.flag)):
                os.kill(os.getpid(), signal.SIGINT)
        th = threading.Thread(target=send); th.start()
        with mock.patch.object(run_cli, "in_foreground", return_value=foreground):
            rc = self.main(["--seat", "s", "--", sys.executable, "-c", child, count, self.flag])
        th.join()
        self.assertEqual(rc, 0)
        return len(Path(count).read_text().splitlines()) if os.path.exists(count) else 0

    def test_sigint_from_the_terminal_is_not_sent_twice(self):  # the terminal already signalled the child
        self.assertEqual(self.sigint_to_bw_run(foreground=True), 0)

    def test_sigint_from_kill_is_forwarded_once(self):
        self.assertEqual(self.sigint_to_bw_run(foreground=False), 1)

    def test_in_foreground_is_false_without_a_terminal(self):
        with mock.patch.object(run_cli.os, "isatty", return_value=False):
            self.assertFalse(run_cli.in_foreground())
        with mock.patch.object(run_cli.os, "isatty", return_value=True), \
                mock.patch.object(run_cli.os, "tcgetpgrp", side_effect=OSError(25, "not a tty")):
            self.assertFalse(run_cli.in_foreground())
        with mock.patch.object(run_cli.os, "isatty", return_value=True), \
                mock.patch.object(run_cli.os, "tcgetpgrp", return_value=os.getpgrp()):
            self.assertTrue(run_cli.in_foreground())

    def test_heartbeat_while_the_child_runs(self):  # PD-13
        self.write_config(local={"run_max_s": 2})          # a beat every 1 s
        self.main(["--seat", "s", "--", sys.executable, "-c", "import time; time.sleep(2.5)"])
        rows = self.runs()
        phases = [r["phase"] for r in rows]
        self.assertEqual((phases[0], phases[-1]), ("start", "end")); self.assertGreaterEqual(phases.count("beat"), 2)
        beat = next(r for r in rows if r["phase"] == "beat")
        self.assertEqual((beat["run_id"], beat["seat"], beat["dir"]), (rows[0]["run_id"], "s", rows[0]["dir"]))

    def test_model_defaults_to_the_local_seat(self):
        self.write_config(local={"seats": [{"name": "my-seat", "match": "my-wrapper", "model": "model-a"}]})
        self.main(["--seat", "my-seat", "--", sys.executable, EXIT, "0"])
        self.assertEqual(self.runs()[0]["model"], "model-a")
        self.main(["--seat", "my-seat", "--model", "model-b", "--", sys.executable, EXIT, "0"])
        self.assertEqual(self.runs()[-2]["model"], "model-b")

    def test_dir_outside_git_is_its_real_path(self):
        plain = os.path.join(self.tmp, "plain"); os.makedirs(plain)
        link = os.path.join(self.tmp, "link"); os.symlink(plain, link)
        self.main(["--seat", "s", "--dir", link, "--", sys.executable, EXIT, "0"])
        self.assertEqual(self.runs()[0]["dir"], plain)

    def test_untracked_still_runs(self):  # Review Focus 2
        for env in ({"BALANCEWHEEL_CONFIG": "/nonexistent.json"}, {"BALANCEWHEEL_METRICS_LOG": "/dev/null/x.jsonl"}):
            rc, err = self.main_env(env, ["--seat", "s", "--", sys.executable, EXIT, "4"])
            self.assertEqual(rc, 4); self.assertEqual(err.count("bw run:"), 1); self.assertIn("not tracked", err)

    def test_uncontained_lead_appends_directly(self):
        cp = os.path.join(self.tmp, "handoffs", "k"); os.makedirs(cp)
        self.main_env({"BW_CHECKPOINT": os.path.join(cp, "checkpoint.md")}, ["--seat", "s", "--", sys.executable, EXIT, "0"])
        self.assertEqual(len(self.runs()), 2); self.assertFalse(os.path.exists(os.path.join(cp, "runs")))

    def test_contained_lead_falls_back_to_request_files(self):
        cp = os.path.join(self.tmp, "handoffs", "k"); os.makedirs(cp)
        rc, _ = self.main_env({"BW_CHECKPOINT": os.path.join(cp, "checkpoint.md"),
                               "BALANCEWHEEL_METRICS_LOG": "/dev/null/x.jsonl"},
                              ["--seat", "s", "--", sys.executable, EXIT, "0"])
        names = sorted(os.listdir(os.path.join(cp, "runs")))
        self.assertEqual((rc, len(names)), (0, 2)); self.assertTrue(all(n.endswith((".start.json", ".end.json")) for n in names))
        start = json.loads(Path(cp, "runs", next(n for n in names if n.endswith(".start.json"))).read_text())
        self.assertEqual((start["type"], start["phase"], start["source"]), ("run", "start", "local"))
        self.assertTrue(names[0].startswith(start["run_id"] + "."))

    def test_start_prints_only_the_id_and_end_closes_it(self):
        rid = self.capture(["start", "--seat", "s"]).strip()
        self.assertTrue(runs.RUN_ID.fullmatch(rid)); self.assertNotIn("pid", self.runs()[0])
        self.assertEqual(self.main(["end", rid, "--fail", "--reason", "lint failed"]), 0)
        self.assertEqual((self.runs()[-1]["seat"], self.runs()[-1]["reason"]), ("s", "lint failed"))
        self.assertEqual((self.runs()[-1]["dir"], self.runs()[-1]["ok"]), (self.runs()[0]["dir"], False))

    def test_start_prints_the_id_also_when_untracked(self):
        rc, err = self.main_env({"BALANCEWHEEL_CONFIG": "/nonexistent.json"}, ["start", "--seat", "s"])
        self.assertEqual(rc, 0)
        self.assertTrue(runs.RUN_ID.fullmatch(self.last_out.strip()))
        self.assertIn("not tracked", err)
        rc, _ = self.main_env({"BALANCEWHEEL_CONFIG": "/nonexistent.json"}, ["end", self.last_out.strip(), "--ok"])
        self.assertEqual(rc, 0)

    def test_end_request_with_no_start_omits_seat_and_dir(self):
        cp = os.path.join(self.tmp, "handoffs", "k"); os.makedirs(cp)
        rc, _ = self.main_env({"BW_CHECKPOINT": os.path.join(cp, "checkpoint.md"),
                               "BALANCEWHEEL_CONFIG": "/nonexistent.json"}, ["end", "r0123456789ab", "--ok"])
        body = json.loads(Path(cp, "runs", "r0123456789ab.end.json").read_text())
        self.assertEqual(rc, 0)
        self.assertEqual((body["phase"], body["ok"]), ("end", True))
        self.assertNotIn("seat", body); self.assertNotIn("dir", body)

    def test_end_finds_a_pending_start(self):  # contained: start and end both as request files
        cp = os.path.join(self.tmp, "handoffs", "k"); os.makedirs(cp)
        env = {"BW_CHECKPOINT": os.path.join(cp, "checkpoint.md"), "BALANCEWHEEL_METRICS_LOG": "/dev/null/x.jsonl"}
        rc, _ = self.main_env(env, ["start", "--seat", "s", "--dir", self.tmp])
        rid = self.last_out.strip()
        self.assertEqual(rc, 0)
        rc, _ = self.main_env(env, ["end", rid, "--ok"])
        self.assertEqual(rc, 0)
        end = json.loads(Path(cp, "runs", f"{rid}.end.json").read_text())
        self.assertEqual((end["seat"], end["dir"], end["ok"]), ("s", self.tmp, True))
        self.assertIsInstance(end["duration_s"], float)

    def test_end_refuses_a_bw_seat_run(self):
        store.append(self.log, runs.start_row("astra", "model-a", "/w", "x", "bw"))
        rc, err = self.main_env({}, ["end", self.runs()[0]["run_id"], "--ok"])
        self.assertEqual(rc, 2)
        self.assertIn("bw seat", err)
        self.assertEqual(len(self.runs()), 1)

    def test_usage_errors(self):
        for argv in (["--seat", "s"], ["--seat", "S!", "--", "true"], ["end", "bad", "--ok"], ["end", "r0123456789ab"],
                     ["--seat", "s", "--"], ["--", "true"], ["end", "r0123456789ab", "--ok", "--fail"],
                     ["start"]):
            rc, err = self.main_env({}, argv)
            self.assertEqual(rc, 2, argv)
            self.assertIn("usage", err.lower(), argv)
        self.assertEqual(self.runs(), [])


if __name__ == "__main__":
    unittest.main()
