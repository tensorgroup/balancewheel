import json, os, pty, signal, subprocess, sys, tempfile, threading, time, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "metrics"))
import store  # noqa: E402
from lead import quota  # noqa: E402
from lead.guard import AGY_AWAITS  # noqa: E402

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


class RunnerCase(unittest.TestCase):
    def make_tmp(self):
        return tempfile.TemporaryDirectory()

    def config(self, root):
        return {"schema_version": 2,
                "plans": {"pa": {"billing": "plan", "quota": "statusline"}, "pb": {"billing": "plan", "quota": "probe"}},
                "leads": {"la": {"runtime": "codex", "model": "a", "plan": "pa"},
                          "lb": {"runtime": "pi", "model": "b", "plan": "pb"}},
                "seats": {"s1": {"runtime": "codex", "model": "x", "roles": ["review"]}},
                "profiles": {"p": {"lead_order": ["la", "lb"]}}, "default_profile": "p",
                "state_dir": str(root / "state"), "metrics_log": str(root / "metrics.jsonl")}

    def setUp(self):
        self.tmp = self.make_tmp()
        root = Path(os.path.realpath(self.tmp.name))
        self.repo = root / "app"; self.repo.mkdir()
        for a in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "init"]):
            subprocess.run(["git", "-C", str(self.repo), *a], check=True, env=GITENV)
        self.state = root / "state"; self.out = root / "out"; self.out.mkdir()
        self.log = root / "metrics.jsonl"
        cfg = self.config(root)
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


class Runner(RunnerCase):
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

    def test_cap_signal_from_inside_the_sandbox_switches(self):
        p = self.start(env={"FAKE_MODE_a": "sleep", "FAKE_MODE_b": "exit"})
        self.assertTrue(wait_for(lambda: self.fake("a")))
        import hashlib
        key = hashlib.sha1(os.path.realpath(str(self.repo)).encode()).hexdigest()[:12]
        (self.state / "handoffs" / key / "cap-signal").write_text("")
        self.assertEqual(p.wait(20), 0, p.stdout.read())
        self.assertEqual([e["reason"] for e in self.events("leads")], ["capped"])

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

    def test_scratch_removed_after_bw_error_post_exit(self):
        tmpdir = self.out.parent / "tmpdir"; tmpdir.mkdir()
        p = self.start("--contained-by-bw", "codex", "--verified", "--fail-in-caps",
                       env={"TMPDIR": str(tmpdir), "BW_PROBE_SECONDS": "0", "FAKE_MODE_a": "exit"})
        out = p.communicate(timeout=30)[0]
        self.assertNotEqual(p.returncode, 0, out)
        self.assertIn("injected failure after the lead exited", out)
        self.assertIsNotNone(self.fake("a"), out)
        scratch = [l.split(" ", 1)[1] for l in out.splitlines() if l.startswith("SCRATCH ")]
        self.assertEqual(len(scratch), 1, out)
        self.assertEqual(os.path.dirname(os.path.realpath(scratch[0])), os.path.realpath(tmpdir))
        self.assertEqual([n for n in os.listdir(tmpdir) if n.startswith("bw-lead-")], [])

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

    def test_config_warnings_printed_once_on_stderr(self):
        obj = json.loads(self.cfg.read_text())
        obj["tools"] = {"x": {"update": ["x", "up"], "runtime": "pi", "foo": 1}}
        self.cfg.write_text(json.dumps(obj))
        r = subprocess.run([sys.executable, BW, "status"], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr.count("bw: config warning: tools.x.foo: unknown key (ignored)"), 1, r.stderr)
        self.assertNotIn("config warning", r.stdout)

    def test_bw_cap_records_cap(self):
        r = subprocess.run([sys.executable, BW, "cap", "pb"], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("pb", quota.read_caps(str(self.state), time.time(), 5))

class RuntimeLocks(RunnerCase):
    def exclusive(self, runtime):
        from lead.locks import RuntimeLock
        lk = RuntimeLock(str(self.state), runtime)
        try:
            return lk.try_exclusive()
        finally:
            lk.release()

    def verify_probes(self):
        f = self.out / "verify-lock.jsonl"
        return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []

    def test_runtime_lock_held_before_verification_check(self):
        p = self.start("--probe-lock-in-verify", env={"FAKE_MODE_a": "exit"})
        self.assertEqual(p.wait(15), 0, p.stdout.read())
        self.assertEqual(self.verify_probes(), [{"runtime": "codex", "exclusive": "False"}])
        self.assertTrue(self.exclusive("codex"), "the runtime lock outlived the session")

    def test_lead_child_keeps_runtime_lock_after_runner_sigkill(self):
        first = self.start()
        self.assertTrue(wait_for(lambda: self.fake("a")))
        self.assertFalse(self.exclusive("codex"))
        first.kill(); first.wait()
        child = self.fake("a")["pid"]
        self.assertFalse(self.exclusive("codex"), "runtime lock released while the lead still runs")
        os.killpg(self.fake("a")["pgid"], signal.SIGKILL)
        self.assertTrue(wait_for(lambda: not _alive(child)))
        self.assertTrue(wait_for(lambda: self.exclusive("codex"), 5))

    def test_switch_takes_new_runtime_lock(self):
        p = self.start("--probe-lock-in-verify", env={"FAKE_MODE_a": "sleep", "FAKE_MODE_b": "sleep"})
        self.assertTrue(wait_for(lambda: self.fake("a")))
        quota.record_cap(str(self.state), "pa", time.time(), resets_at=time.time() + 3600)
        self.assertTrue(wait_for(lambda: self.fake("b")), "the next lead never started")
        self.assertEqual(self.verify_probes(), [{"runtime": "codex", "exclusive": "False"},
                                                {"runtime": "pi", "exclusive": "False"}])
        self.assertFalse(self.exclusive("pi"), "the new lead runs without its runtime lock")
        self.assertTrue(self.exclusive("codex"), "the old runtime lock outlived its lead")
        os.killpg(self.fake("b")["pgid"], signal.SIGTERM)
        self.assertEqual(p.wait(10), 0, p.stdout.read())
        self.assertTrue(self.exclusive("pi"))

    def hold_exclusive(self, runtime):
        from lead.locks import RuntimeLock
        lk = RuntimeLock(str(self.state), runtime)
        self.assertTrue(lk.try_exclusive())
        self.addCleanup(lk.release)
        return lk

    def lines(self, p):
        got = []

        fd = os.dup(p.stdout.fileno())  # our own copy: tearDown's close of p.stdout cannot wait on this reader

        def pump():
            buf = b""
            try:
                while True:
                    chunk = os.read(fd, 4096)
                    if not chunk:
                        break
                    buf += chunk
                    *done, buf = buf.split(b"\n")
                    got.extend(l.decode(errors="replace") + "\n" for l in done)
            finally:
                os.close(fd)
        threading.Thread(target=pump, daemon=True).start()
        return got

    def test_start_waits_for_an_update_and_says_so_once(self):
        held = self.hold_exclusive("codex")
        p = self.start(env={"FAKE_MODE_a": "exit"})
        out = self.lines(p)
        self.assertTrue(wait_for(lambda: any("waiting for the codex update to finish" in l for l in out), 5), out)
        time.sleep(0.5)
        self.assertIsNone(self.fake("a"), "the lead started while its runtime was being updated")
        held.release()
        self.assertEqual(p.wait(15), 0, "".join(out))
        self.assertIsNotNone(self.fake("a"))
        self.assertEqual(sum("waiting for the codex update" in l for l in out), 1, out)

    def test_switch_check_does_not_block_on_an_update(self):
        p = self.start(env={"FAKE_MODE_a": "sleep", "FAKE_MODE_b": "sleep"})
        out = self.lines(p)
        self.assertTrue(wait_for(lambda: self.fake("a")))
        held = self.hold_exclusive("pi")
        quota.record_cap(str(self.state), "pa", time.time(), resets_at=time.time() + 3600)
        self.assertTrue(wait_for(lambda: any("staying on la: pi is being updated" in l for l in out), 5), out)
        time.sleep(1.0)  # several polls (0.3 s) pass with the lock still held: no switch, no hang
        self.assertIsNone(self.fake("b"), "switched to a runtime that is being updated")
        self.assertIsNone(p.poll())
        held.release()
        self.assertTrue(wait_for(lambda: self.fake("b"), 5), "".join(out))  # retried at a later poll
        os.killpg(self.fake("b")["pgid"], signal.SIGTERM)
        self.assertEqual(p.wait(10), 0, "".join(out))
        self.assertEqual(sum("is being updated" in l for l in out), 1, out)

    def test_inherited_runtime_lock_fd_is_read_only(self):
        p = self.start(env={"FAKE_MODE_a": "exit"})
        self.assertEqual(p.wait(15), 0, p.stdout.read())
        ino = os.stat(self.state / "locks" / "runtime-codex.lock").st_ino
        modes = [m for i, m in self.fake("a")["fds"] if i == ino]
        self.assertEqual(modes, [os.O_RDONLY])

    def test_lead_unlocking_its_inherited_fd_does_not_free_the_runtime(self):
        p = self.start(env={"FAKE_MODE_a": "sleep", "FAKE_UNLOCK_a": "1"})
        self.assertTrue(wait_for(lambda: self.fake("a")))
        self.assertFalse(self.exclusive("codex"), "a lead's LOCK_UN let an update in under the live session")
        os.killpg(self.fake("a")["pgid"], signal.SIGTERM)
        self.assertEqual(p.wait(10), 0, p.stdout.read())
        self.assertTrue(self.exclusive("codex"), "the runtime lock outlived the session")

    def test_refused_start_releases_runtime_lock(self):
        p = self.start("--contained-by-bw", "codex")
        self.assertEqual(p.wait(10), 4)
        self.assertTrue(self.exclusive("codex"))


class UnguardedRunner(RunnerCase):
    def test_unguarded_session_has_autonomy_but_no_guard_env_or_event(self):
        p = self.start("--use", "la", env={"FAKE_MODE_a": "exit"})
        self.assertEqual(p.wait(15), 0, p.stdout.read())
        self.assertEqual(self.fake("a")["guard_env"], [])
        self.assertEqual(self.events("guards"), [])
        start = self.events("sessions")[0]
        self.assertEqual(start["autonomy"], {"requested": None, "effective": "manual"})
        self.assertIsNone(start["guard_version"])
        self.assertFalse((self.state / "guard").exists())

    def test_stale_guard_variables_in_bws_own_environment_do_not_reach_an_unguarded_lead(self):
        poisoned = {f"BW_GUARD_{k}": "stale" for k in ("SOCKET", "TOKEN", "MODE", "DIR", "MARKERS", "EXTRA")}
        p = self.start("--use", "la", env={"FAKE_MODE_a": "exit", **poisoned})
        self.assertEqual(p.wait(15), 0, p.stdout.read())
        self.assertEqual(self.fake("a")["guard_env"], [])


class GuardRunner(RunnerCase):
    def make_tmp(self):
        return tempfile.TemporaryDirectory(dir="/tmp")  # unix socket paths are short-limited

    def config(self, root):
        cfg = super().config(root)
        cfg["plans"]["pc"] = {"billing": "plan", "quota": "probe"}
        cfg["leads"]["lc"] = {"runtime": "agy", "model": "c", "plan": "pc"}
        cfg["profiles"]["p"]["lead_order"] = ["la", "lb", "lc"]
        return cfg

    def key(self):
        import hashlib
        return hashlib.sha1(os.path.realpath(str(self.repo)).encode()).hexdigest()[:12]

    def guard_log(self):
        from lead.guard import record
        return record.load(str(self.state))["decisions"]

    def test_guarded_call_is_logged_counted_and_the_socket_removed(self):
        p = self.start("--use", "lb", "--guard", "pi", env={"FAKE_MODE_b": "guard_probe"})
        self.assertEqual(p.wait(30), 0, p.stdout.read())
        b = self.fake("b")
        self.assertEqual(b["guard_env"], ["BW_GUARD_DIR", "BW_GUARD_MARKERS", "BW_GUARD_MODE", "BW_GUARD_SOCKET", "BW_GUARD_TOKEN"])
        self.assertEqual(b["probe"]["decision"], "allow")
        sid = self.events("sessions")[0]["session_id"]
        rows = self.guard_log()
        real = [d for d in rows if not d["self_test"]]
        self.assertEqual([(d["session_id"], d["outcome"], d["call"]) for d in real], [(sid, "shadow-allowed", "ls")])
        self.assertEqual(len([d for d in rows if d["self_test"]]), 1)
        g = self.events("guards")
        self.assertEqual(len(g), 1)
        self.assertEqual((g[0]["session_id"], g[0]["calls"], g[0]["decisions"], g[0]["mode"], g[0]["unreachable"]),
                         (sid, 1, {"allow": 1, "ask": 0, "block": 0}, "shadow", 0))
        for k in ("project", "profile", "lead", "runtime", "guard_version", "rules_version", "gray", "latency_ms"):
            self.assertIn(k, g[0])
        start = self.events("sessions")[0]
        self.assertEqual(start["autonomy"], {"requested": "guard", "effective": "guard"})
        self.assertEqual(start["guard_version"], g[0]["guard_version"])
        self.assertFalse((self.state / "guard" / self.key() / "sock").exists())
        for name in ("hook.py", "pi_guard.ts"):
            self.assertTrue((self.state / "guard" / self.key() / name).exists())
        log = [json.loads(l) for l in self.log.read_text().splitlines()]
        types = [(e["type"], e.get("phase")) for e in log]
        self.assertLess(types.index(("guard", None)), types.index(("session", "end")))

    def test_stale_guard_variables_are_replaced_by_the_sessions_own_for_a_guarded_lead(self):
        p = self.start("--use", "lb", "--guard", "pi", env={"FAKE_MODE_b": "guard_probe", "BW_GUARD_TOKEN": "stale",
                                                          "BW_GUARD_EXTRA": "stale"})
        self.assertEqual(p.wait(30), 0, p.stdout.read())
        b = self.fake("b")
        self.assertEqual(b["probe"]["decision"], "allow")  # the session's token, not the stale one
        self.assertNotIn("BW_GUARD_EXTRA", b["guard_env"])

    def test_a_missing_client_source_exits_7_not_a_traceback(self):
        p = self.start("--use", "lb", "--guard", "pi", "--no-client")
        out = p.communicate(timeout=30)[0]
        self.assertEqual(p.returncode, 7, out)
        self.assertNotIn("Traceback", out)
        self.assertIn("guard cannot start", out)
        self.assertIsNone(self.fake("b"))

    def test_an_exception_in_the_poll_loop_stops_the_lead_and_still_logs_the_guard_event(self):
        p = self.start("--use", "lb", "--guard", "pi", "--fail-in-poll", env={"FAKE_MODE_b": "sleep"})
        out = p.communicate(timeout=30)[0]
        self.assertNotEqual(p.returncode, 0, out)
        self.assertIn("injected failure in the poll loop", out)
        self.assertEqual(len(self.events("guards")), 1)
        self.assertFalse((self.state / "guard" / self.key() / "sock").exists())
        self.assertFalse(_alive_group(self.fake("b")["pgid"]), "the lead kept running unguarded")

    def test_failure_after_launch_stops_the_lead_and_the_broker(self):
        p = self.start("--use", "lb", "--guard", "pi", "--fail-after-launch", env={"FAKE_MODE_b": "sleep"})
        out = p.communicate(timeout=30)[0]
        self.assertNotEqual(p.returncode, 0, out)
        self.assertIn("injected failure after launch", out)
        pgid = int((self.out / "launched.pgid").read_text())
        self.assertTrue(wait_for(lambda: not _alive_group(pgid), 5), "the lead kept running unguarded")
        self.assertFalse((self.state / "guard" / self.key() / "sock").exists())

    def test_replaying_the_logged_self_test_marker_is_a_real_counted_call(self):
        p = self.start("--use", "lb", "--guard", "pi",
                       env={"FAKE_MODE_b": "guard_probe", "FAKE_STATE": str(self.state)})
        self.assertEqual(p.wait(30), 0, p.stdout.read())
        self.assertEqual(self.fake("b")["probe"]["decision"], "allow")  # shadow: no real decision leaked
        rows = self.guard_log()
        self.assertEqual([d["self_test"] for d in rows], [True, False])
        g = self.events("guards")[0]
        self.assertEqual((g["calls"], g["decisions"]["block"]), (1, 1))

    def test_guarded_agy_without_hook_entry_exits_7(self):
        p = self.start("--use", "lc", "--guard", "agy", "--no-hook")
        self.assertEqual(p.wait(15), 7, "")
        self.assertIn("bw guard install agy", p.stdout.read())
        self.assertIsNone(self.fake("c"))

    def test_guarded_agy_refused_until_the_hook_spike_passed(self):
        p = self.start("--use", "lc", "--guard", "agy", "--no-spike")
        self.assertEqual(p.wait(15), 7)
        self.assertIn(AGY_AWAITS, p.stdout.read())
        self.assertIsNone(self.fake("c"))

    def test_cap_switch_onto_unready_guarded_lead_stays_put(self):
        self.cfg_lead_order(["la", "lc"])
        p = self.start("--guard", "agy", "--no-hook", env={"FAKE_MODE_a": "sleep"})
        self.assertTrue(wait_for(lambda: self.fake("a")))
        quota.record_cap(str(self.state), "pa", time.time(), resets_at=time.time() + 3600)
        time.sleep(1.5)
        self.assertIsNone(p.poll(), "the current lead was stopped")
        self.assertTrue(_alive(self.fake("a")["pid"]))
        self.assertIsNone(self.fake("c"))
        os.killpg(self.fake("a")["pgid"], signal.SIGTERM)
        self.assertEqual(p.wait(10), 0)
        out = p.stdout.read()
        self.assertIn("bw guard install agy", out)
        self.assertEqual(out.count("bw guard install agy"), 1, "the reason should be printed once")
        self.assertEqual(self.events("leads"), [])

    def cfg_lead_order(self, order):
        c = json.loads(self.cfg.read_text())
        c["profiles"]["p"]["lead_order"] = order
        self.cfg.write_text(json.dumps(c))

    def test_too_long_state_path_exits_7_naming_the_path(self):
        c = json.loads(self.cfg.read_text())
        c["state_dir"] = str(self.state.parent / ("deep" * 25) / "state")
        self.cfg.write_text(json.dumps(c))
        p = self.start("--use", "lb", "--guard", "pi")
        self.assertEqual(p.wait(15), 7)
        out = p.stdout.read()
        self.assertIn("deepdeep", out)
        self.assertIn("sock", out)
        self.assertIsNone(self.fake("b"))

    def test_state_dir_inside_the_worktree_exits_7(self):
        c = json.loads(self.cfg.read_text())
        c["state_dir"] = str(self.repo / ".bwstate")
        self.cfg.write_text(json.dumps(c))
        p = self.start("--use", "lb", "--guard", "pi")
        self.assertEqual(p.wait(15), 7)
        self.assertIn("overlaps", p.stdout.read())
        self.assertIsNone(self.fake("b"))

    def test_stale_socket_from_a_killed_run_does_not_stop_the_next_start(self):
        gdir = self.state / "guard" / self.key()
        gdir.mkdir(parents=True)
        (gdir / "sock").write_text("left behind")
        p = self.start("--use", "lb", "--guard", "pi", env={"FAKE_MODE_b": "guard_probe"})
        self.assertEqual(p.wait(30), 0, p.stdout.read())
        self.assertEqual(self.fake("b")["probe"]["decision"], "allow")

    def test_stale_markers_reported_once_and_removed(self):
        cp = self.state / "handoffs" / self.key()
        cp.mkdir(parents=True)
        line = json.dumps({"ts": "t", "runtime": "pi", "mode": "shadow", "why": "x"}) + "\n"
        (cp / "guard-unreachable").write_text(line * 2)
        p = self.start("--use", "lb", "--guard", "pi", env={"FAKE_MODE_b": "exit"})
        out = p.communicate(timeout=30)[0]
        self.assertEqual(p.returncode, 0, out)
        self.assertEqual(out.count("guard was unreachable"), 1, out)
        self.assertFalse((cp / "guard-unreachable").exists())
        again = self.start("--use", "lb", "--guard", "pi", env={"FAKE_MODE_b": "exit"})
        self.assertNotIn("unreachable", again.communicate(timeout=30)[0])

    def test_fifo_in_place_of_the_markers_file_does_not_hang(self):
        cp = self.state / "handoffs" / self.key()
        cp.mkdir(parents=True)
        os.mkfifo(str(cp / "guard-unreachable"))
        p = self.start("--use", "lb", "--guard", "pi", env={"FAKE_MODE_b": "exit"})
        self.assertEqual(p.wait(30), 0, p.stdout.read())
        self.assertFalse((cp / "guard-unreachable").exists())

    def test_markers_written_during_the_session_are_noticed_once_and_counted(self):
        cp = self.state / "handoffs" / self.key()
        env = {"FAKE_MODE_b": "sleep"}
        p = self.start("--use", "lb", "--guard", "pi", env=env)
        self.assertTrue(wait_for(lambda: self.fake("b")))
        line = json.dumps({"ts": "t", "runtime": "pi", "mode": "shadow", "why": "gone"}) + "\n"
        with open(cp / "guard-unreachable", "a") as f:
            f.write(line * 2)
        time.sleep(1.2)
        os.killpg(self.fake("b")["pgid"], signal.SIGTERM)
        out = p.communicate(timeout=30)[0]
        self.assertEqual(out.count("guard was unreachable"), 1, out)
        self.assertEqual(self.events("guards")[0]["unreachable"], 2)


def _alive_group(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


if __name__ == "__main__":
    unittest.main()
