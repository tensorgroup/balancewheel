import json, os, pty, signal, subprocess, sys, tempfile, time, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "metrics"))
import store  # noqa: E402
from lead import quota  # noqa: E402

DRIVER = str(HERE / "fixtures" / "run_runner.py")
BW = str(ROOT / "bin" / "bw")
GITENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def wait_for(pred, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


class Runner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(os.path.realpath(self.tmp.name))
        self.repo = root / "app"; self.repo.mkdir()
        for a in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "init"]):
            subprocess.run(["git", "-C", str(self.repo), *a], check=True, env=GITENV)
        self.state = root / "state"; self.out = root / "out"; self.out.mkdir()
        self.log = root / "metrics.jsonl"
        cfg = {"schema_version": 2,
               "plans": {"pa": {"billing": "plan", "quota": "statusline"}, "pb": {"billing": "plan", "quota": "probe"}},
               "leads": {"la": {"runtime": "codex", "model": "a", "plan": "pa"},
                         "lb": {"runtime": "pi", "model": "b", "plan": "pb"}},
               "seats": {"s1": {"runtime": "codex", "model": "x", "roles": ["review"]}},
               "profiles": {"p": {"lead_order": ["la", "lb"]}}, "default_profile": "p",
               "state_dir": str(self.state), "metrics_log": str(self.log)}
        self.cfg = root / "config.json"; self.cfg.write_text(json.dumps(cfg))
        self.env = {**os.environ, "FAKE_OUT": str(self.out), "BW_PROBE_SECONDS": "0.3", "BW_STOP_GRACE": "0.5",
                    "BALANCEWHEEL_CONFIG": str(self.cfg)}
        self.procs = []

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            p.wait()
            if p.stdout:
                p.stdout.close()
        for f in self.out.glob("*.json"):
            try:
                os.killpg(json.loads(f.read_text())["pgid"], signal.SIGKILL)
            except (ProcessLookupError, PermissionError, ValueError, KeyError):
                pass
        self.tmp.cleanup()

    def start(self, *extra, env=None):
        p = subprocess.Popen([sys.executable, DRIVER, str(self.cfg), str(self.repo), *extra], env={**self.env, **(env or {})},
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, stdin=subprocess.DEVNULL)
        self.procs.append(p)
        return p

    def fake(self, name):
        f = self.out / f"{name}.json"
        return json.loads(f.read_text()) if f.exists() else None

    def events(self, kind):
        return store.load(str(self.log))[kind] if self.log.exists() else []

    def test_second_runner_refused_while_first_holds_lock(self):
        self.start()
        self.assertTrue(wait_for(lambda: self.fake("a")))
        second = self.start()
        self.assertEqual(second.wait(10), 3)
        self.assertIn("already has a lead: la", second.stdout.read())

    def test_lock_held_after_runner_sigkill_while_child_lives(self):
        first = self.start()
        self.assertTrue(wait_for(lambda: self.fake("a")))
        first.kill(); first.wait()
        child = self.fake("a")["pid"]
        second = self.start()
        self.assertEqual(second.wait(10), 3, "lock released while the lead still runs")
        os.killpg(self.fake("a")["pgid"], signal.SIGKILL)
        self.assertTrue(wait_for(lambda: not _alive(child)))
        (self.out / "a.json").unlink()
        third = self.start(env={"FAKE_MODE_a": "exit"})
        self.assertEqual(third.wait(15), 0, third.stdout.read())
        lost = [s for s in self.events("sessions") if s["end_reason"] == "lost"]
        self.assertEqual(len(lost), 1)

    def test_cap_switches_after_group_confirmed_gone(self):
        p = self.start(env={"FAKE_MODE_a": "ignore_term", "FAKE_MODE_b": "exit"})
        self.assertTrue(wait_for(lambda: self.fake("a")))
        quota.record_cap(str(self.state), "pa", time.time(), resets_at=time.time() + 3600)
        self.assertEqual(p.wait(20), 0, p.stdout.read())
        b = self.fake("b")
        self.assertIsNotNone(b, "the next lead never started")
        self.assertEqual(b["earlier_alive"], [], "next lead started while the capped one was alive")
        self.assertIn("@next handoff", " ".join(b["argv"]))
        self.assertEqual([e["reason"] for e in self.events("leads")], ["capped"])
        reasons = sorted(s["end_reason"] for s in self.events("sessions"))
        self.assertEqual(reasons, ["exit", "switch"])

    def test_all_capped_keeps_session(self):
        p = self.start(env={"FAKE_MODE_a": "sleep"})
        self.assertTrue(wait_for(lambda: self.fake("a")))
        for plan in ("pa", "pb"):
            quota.record_cap(str(self.state), plan, time.time(), resets_at=time.time() + 3600)
        time.sleep(1.2)
        self.assertIsNone(p.poll())
        self.assertTrue(_alive(self.fake("a")["pid"]))
        os.killpg(self.fake("a")["pgid"], signal.SIGTERM)
        self.assertEqual(p.wait(10), 0)
        self.assertEqual(self.events("leads"), [])

    def test_all_capped_at_start_refuses_to_launch(self):
        for plan in ("pa", "pb"):
            quota.record_cap(str(self.state), plan, time.time(), resets_at=time.time() + 3600)
        p = self.start()
        self.assertEqual(p.wait(10), 6)
        self.assertIn("all leads capped", p.stdout.read())
        self.assertIsNone(self.fake("a"))

    def test_leftover_descendant_stopped_at_exit(self):
        p = self.start(env={"FAKE_MODE_a": "exit", "FAKE_SPAWN_a": "1"})
        self.assertEqual(p.wait(15), 0, p.stdout.read())
        child = json.loads((self.out / "a-child.json").read_text())["pid"]
        self.assertTrue(wait_for(lambda: not _alive(child), 5), "a descendant in the lead's group outlived the session")

    def test_lost_session_logged_once_even_on_early_exit(self):
        from lead.runner import Lock
        import hashlib
        key = hashlib.sha1(os.path.realpath(str(self.repo)).encode()).hexdigest()[:12]
        lk = Lock(str(self.state), key); lk.write({"session_id": "dead1", "lead": "la"}); os.close(lk.fd)
        for plan in ("pa", "pb"):
            quota.record_cap(str(self.state), plan, time.time(), resets_at=time.time() + 3600)
        self.assertEqual(self.start().wait(10), 6)
        self.assertEqual(self.start().wait(10), 6)
        lost = [s for s in self.events("sessions") if s["end_reason"] == "lost"]
        self.assertEqual(len(lost), 1)

    def test_return_logged_at_boundary(self):
        quota.record_cap(str(self.state), "pa", time.time(), resets_at=time.time() + 1.0)
        p = self.start(env={"FAKE_MODE_b": "sleep"})
        self.assertTrue(wait_for(lambda: self.fake("b")))
        time.sleep(1.3)  # pa's cap passes while b leads
        os.killpg(self.fake("b")["pgid"], signal.SIGTERM)
        self.assertEqual(p.wait(10), 0)
        out = p.stdout.read()
        self.assertIn("la is available again", out)
        self.assertEqual([e["reason"] for e in self.events("leads")], ["returned"])

    def test_pin_and_auto(self):
        p = self.start("--use", "lb", env={"FAKE_MODE_b": "exit"})
        self.assertEqual(p.wait(10), 0)
        self.assertIsNotNone(self.fake("b"))
        pins = json.loads((self.state / "leads.json").read_text())
        self.assertIn("lb", pins.values())
        (self.out / "b.json").unlink()
        p = self.start("--auto", env={"FAKE_MODE_a": "exit"})
        self.assertEqual(p.wait(10), 0)
        self.assertIsNotNone(self.fake("a"))
        self.assertEqual(json.loads((self.state / "leads.json").read_text()), {})

    def test_unverified_runtime_refused_with_verify_hint(self):
        p = self.start("--contained-by-bw", "codex")
        self.assertEqual(p.wait(10), 4)
        self.assertIn("bw sandbox verify codex", p.stdout.read())
        self.assertIsNone(self.fake("a"))

    def test_child_gets_foreground_and_runner_survives_ctrl_c(self):
        pid, fd = pty.fork()
        if pid == 0:
            os.environ.update(self.env)
            os.execv(sys.executable, [sys.executable, DRIVER, str(self.cfg), str(self.repo)])
        buf = b""
        end = time.time() + 15
        while b"FAKE a started" not in buf and time.time() < end:
            try:
                buf += os.read(fd, 1024)
            except OSError:
                break
        self.assertTrue(wait_for(lambda: self.fake("a")))
        self.assertTrue(self.fake("a")["foreground"], "lead did not get the terminal")
        os.write(fd, b"\x03")  # Ctrl-C goes to the foreground group: the lead, not bw
        while time.time() < end:
            try:
                chunk = os.read(fd, 1024)
                if not chunk:
                    break
                buf += chunk
            except OSError:
                break
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else status >> 8, 0, buf.decode(errors="replace"))
        self.assertEqual([s["end_reason"] for s in self.events("sessions")], ["exit"])

    def test_status_shows_unknown_and_v0_scope_note(self):
        r = subprocess.run([sys.executable, BW, "status"], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("pa (plan): quota unknown", r.stdout)
        self.assertIn("bw cap <plan>", r.stdout)

    def test_bw_cap_records_cap(self):
        r = subprocess.run([sys.executable, BW, "cap", "pb"], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("pb", quota.read_caps(str(self.state), time.time(), 5))


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


if __name__ == "__main__":
    unittest.main()
