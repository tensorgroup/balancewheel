import copy, json, os, stat, sys, tempfile, threading, time, unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lead import containment  # noqa: E402
from lead.paths import load_config  # noqa: E402
from lead.proc import Result  # noqa: E402
from seats import verify  # noqa: E402

EXAMPLE = Path(__file__).resolve().parent.parent / "balancewheel.example.json"


def fake_version(line):
    return lambda argv, timeout, **kw: Result(0, line + "\n", "", False)


def fake_version_fails():
    return lambda argv, timeout, **kw: Result(127, "", f"{argv[0]}: not found", False)


FAKE = fake_version("codex-cli 9.9")


class SeatVerify(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state")
        home = os.path.join(self.tmp.name, "home")
        os.makedirs(home)
        env = mock.patch.dict(os.environ, {"HOME": home})  # agy's hook files live under HOME
        env.start()
        self.addCleanup(env.stop)
        self.config = load_config(str(EXAMPLE))
        self.config["seats"]["astra2"] = copy.deepcopy(self.config["seats"]["astra"])

    def tearDown(self):
        self.tmp.cleanup()

    def _pass(self, seat, ts=None):
        key = verify.seat_key(self.config, self.state, seat, run=FAKE)
        if ts is None:
            containment.record_pass(self.state, seat, key, "/ev", store="seat-verify.json")
        else:
            with mock.patch("lead.containment.time.time", return_value=ts):
                containment.record_pass(self.state, seat, key, "/ev", store="seat-verify.json")
        return key

    def test_seat_items_example(self):
        c = load_config(str(EXAMPLE))
        self.assertEqual(verify.seat_items(c), ["astra", "ox", "opus", "fable", "gem"])  # kimi disabled; fable reserved

    def test_fallback_followed_disabled_skipped(self):
        c = {"seats": {"a": {"enabled": True, "fallback": "b"}, "b": {"enabled": True, "fallback": "c"},
                       "c": {"enabled": False}, "d": {"enabled": True}},
             "profiles": {"p": {"checkpoints": {"review": {"seats": ["a"]}}}}}
        self.assertEqual(verify.seat_items(c), ["a", "b"])

    def test_reserved_seat_fallback_followed(self):
        c = {"seats": {"r": {"tier": "reserved", "fallback": "s"}, "s": {}, "t": {}},
             "profiles": {}}
        self.assertEqual(verify.seat_items(c), ["r", "s"])

    def test_seat_home(self):
        h = verify.seat_home(self.state, "astra")
        self.assertEqual(h, os.path.join(self.state, "seats", "homes", "astra"))
        self.assertTrue(os.path.isdir(h))
        self.assertEqual(stat.S_IMODE(os.stat(h).st_mode), 0o700)

    def test_key_fields(self):
        k = verify.seat_key(self.config, self.state, "astra", run=fake_version("codex-cli 9.9"))
        self.assertEqual(k["runtime_version"], "codex-cli 9.9"); self.assertNotIn("adapter_sha", k)
        for f in ("seat_adapter_sha", "codepaths_sha", "sandbox_sha", "hook_files", "denied_ports", "effort", "args"):
            self.assertIn(f, k)
        self.assertEqual((k["seat"], k["runtime"], k["model"], k["effort"]), ("astra", "codex", "gpt-6-astra", "xhigh"))
        home = verify.seat_home(self.state, "astra")
        self.assertEqual(sorted(k["hook_files"]), sorted(os.path.join(home, n) for n in ("config.toml", "AGENTS.md",
                         "prompts", "hooks.json", "skills", "rules")))

    def test_agy_backend_is_sandbox_exec(self):
        self.assertEqual(verify.seat_key(self.config, self.state, "gem", run=FAKE)["backend"], "sandbox-exec")

    def test_key_changes_with_model(self):
        k1 = verify.seat_key(self.config, self.state, "astra", run=FAKE)
        self.config["seats"]["astra"]["model"] = "other-model"
        self.assertNotEqual(verify.seat_key(self.config, self.state, "astra", run=FAKE), k1)

    def test_key_changes_with_hook_file(self):
        k1 = verify.seat_key(self.config, self.state, "astra", run=FAKE)
        Path(verify.seat_home(self.state, "astra"), "config.toml").write_text("x = 1\n")
        self.assertNotEqual(verify.seat_key(self.config, self.state, "astra", run=FAKE), k1)

    def test_unreadable_version_kept(self):  # build ruling, 2026-11-23
        k = verify.seat_key(self.config, self.state, "astra", run=fake_version_fails())
        self.assertEqual(k["runtime_version"], "unreadable")

    def test_unreadable_manifest_kept(self):  # build ruling, 2026-11-23
        with mock.patch("seats.verify.containment.manifest", side_effect=PermissionError("denied")):
            k = verify.seat_key(self.config, self.state, "astra", run=FAKE)
        self.assertTrue(k["hook_files"])
        self.assertEqual(set(k["hook_files"].values()), {"unreadable"})

    def test_two_seats_one_runtime_both_kept(self):
        for s in ("astra", "astra2"):
            containment.record_pass(self.state, s, verify.seat_key(self.config, self.state, s, run=FAKE), "/ev", store="seat-verify.json")
        self.assertTrue(verify.is_verified(self.config, self.state, "astra", run=FAKE))
        self.assertTrue(verify.is_verified(self.config, self.state, "astra2", run=FAKE))

    def test_seats_json_shape_and_states(self):
        out = verify.write_seats_json(self.config, self.state, run=FAKE, now=0.0)
        self.assertEqual(out["astra"], {"verified": False, "runtime_version": "codex-cli 9.9", "verified_at": None,
                                        "failed_probe": "never verified"})
        self.assertEqual(json.loads(Path(self.state, "seats.json").read_text()), out)
        self._pass("astra", ts=0.0)
        out = verify.write_seats_json(self.config, self.state, run=FAKE, now=0.0)
        self.assertEqual(out["astra"], {"verified": True, "runtime_version": "codex-cli 9.9",
                                        "verified_at": "1970-01-01T00:00:00Z", "failed_probe": None})
        self.config["seats"]["astra"]["model"] = "other-model"
        out = verify.write_seats_json(self.config, self.state, run=FAKE, now=0.0)
        self.assertEqual((out["astra"]["verified"], out["astra"]["verified_at"], out["astra"]["failed_probe"]),
                         (False, None, "key changed"))
        key = verify.seat_key(self.config, self.state, "astra", run=FAKE)
        verify.record_failure(self.state, "astra", key, "blind-read:state", 5.0)
        out = verify.write_seats_json(self.config, self.state, run=FAKE, now=10.0)
        self.assertEqual((out["astra"]["verified"], out["astra"]["failed_probe"]), (False, "blind-read:state"))
        self.assertEqual(json.loads(Path(self.state, "seats.json").read_text()), out)

    def test_failure_under_old_key_is_key_changed(self):
        key = verify.seat_key(self.config, self.state, "astra", run=FAKE)
        verify.record_failure(self.state, "astra", key, "blind-read:state", 5.0)
        self.config["seats"]["astra"]["model"] = "other-model"
        out = verify.write_seats_json(self.config, self.state, run=FAKE)
        self.assertEqual(out["astra"]["failed_probe"], "key changed")

    def test_disabled_seat_not_in_seats_json(self):  # R16
        out = verify.write_seats_json(self.config, self.state, run=FAKE)
        self.assertNotIn("kimi", out)
        self.assertEqual(list(out), ["astra", "ox", "opus", "fable", "gem", "astra2"])

    def test_later_failure_revokes_pass(self):  # R7
        key = verify.seat_key(self.config, self.state, "astra", run=FAKE)
        containment.record_pass(self.state, "astra", key, "/ev", store="seat-verify.json")
        # a probe name the judge really emits (build ruling, 2026-11-23)
        verify.record_failure(self.state, "astra", key, "probe-skipped:fresh:g1", time.time() + 1)
        self.assertFalse(verify.is_verified(self.config, self.state, "astra", run=FAKE))

    def test_revocation_survives_failure_under_other_key(self):  # R7: K pass, K fail, K2 fail, back on K
        key = self._pass("astra", ts=100.0)
        verify.record_failure(self.state, "astra", key, "blind-read:state", 200.0)
        self.assertNotIn("astra", json.loads(Path(self.state, "seat-verify.json").read_text()))
        self.config["seats"]["astra"]["model"] = "other-model"
        key2 = verify.seat_key(self.config, self.state, "astra", run=FAKE)
        verify.record_failure(self.state, "astra", key2, "blind-write:target", 300.0)
        self.config["seats"]["astra"]["model"] = "gpt-6-astra"
        self.assertEqual(verify.seat_key(self.config, self.state, "astra", run=FAKE), key)
        self.assertFalse(verify.is_verified(self.config, self.state, "astra", run=FAKE))
        self.assertEqual(verify.write_seats_json(self.config, self.state, run=FAKE)["astra"]["failed_probe"], "key changed")

    def test_failure_under_other_key_keeps_pass(self):
        key = self._pass("astra", ts=100.0)
        verify.record_failure(self.state, "astra", {"other": 1}, "blind-read:state", 200.0)
        self.assertEqual(json.loads(Path(self.state, "seat-verify.json").read_text())["astra"]["key"], key)
        self.assertTrue(verify.is_verified(self.config, self.state, "astra", run=FAKE))

    def test_non_numeric_ts_reads_as_zero(self):
        key = verify.seat_key(self.config, self.state, "astra", run=FAKE)
        Path(self.state, "seat-verify.json").write_text(json.dumps({"astra": {"key": key, "ts": "soon"}}))
        Path(self.state, "seats").mkdir(exist_ok=True)
        Path(self.state, "seats", "failures.json").write_text(json.dumps({"gem": {"key": {}, "probe": "p", "ts": None}}))
        out = verify.write_seats_json(self.config, self.state, run=FAKE)
        self.assertEqual((out["astra"]["verified"], out["astra"]["verified_at"]), (True, "1970-01-01T00:00:00Z"))
        self.assertEqual(out["gem"]["failed_probe"], "key changed")

    def test_private_dirs(self):
        verify.issue_token(self.state, "astra", 100.0, 60)
        for d in (Path(self.state, "seats"), Path(self.state, "seats", "attack-tokens")):
            self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o700)

    def test_earlier_failure_does_not_revoke_pass(self):
        key = verify.seat_key(self.config, self.state, "astra", run=FAKE)
        verify.record_failure(self.state, "astra", key, "blind-read:state", time.time() - 10)
        containment.record_pass(self.state, "astra", key, "/ev", store="seat-verify.json")
        self.assertTrue(verify.is_verified(self.config, self.state, "astra", run=FAKE))

    def test_failure_shape(self):
        verify.record_failure(self.state, "astra", {"k": 1}, "blind-read:state", 7.0)
        data = json.loads(Path(self.state, "seats", "failures.json").read_text())
        self.assertEqual(data, {"astra": {"key": {"k": 1}, "probe": "blind-read:state", "ts": 7.0}})

    def test_token(self):
        verify.issue_token(self.state, "astra", 100.0, 60)
        self.assertTrue(verify.check_token(self.state, "astra", 150.0))
        self.assertFalse(verify.check_token(self.state, "gem", 150.0))
        self.assertFalse(verify.check_token(self.state, "astra", 161.0))
        p = Path(self.state, "seats", "attack-tokens", "astra.json")
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
        data = json.loads(p.read_text())
        self.assertEqual((data["seat"], data["expires"], len(data["token"])), ("astra", 160.0, 32))
        verify.clear_token(self.state, "astra"); self.assertFalse(verify.check_token(self.state, "astra", 150.0))
        verify.clear_token(self.state, "astra")  # absent: no error

    def test_token_symlink_refused(self):
        verify.issue_token(self.state, "gem", 100.0, 60)
        d = Path(self.state, "seats", "attack-tokens")
        os.symlink(d / "gem.json", d / "astra.json")
        Path(d, "gem.json").write_text(json.dumps({"seat": "astra", "token": "0" * 32, "expires": 1000.0}))
        os.chmod(d / "gem.json", 0o600)
        self.assertFalse(verify.check_token(self.state, "astra", 150.0))

    def test_token_wrong_mode_or_seat_refused(self):
        verify.issue_token(self.state, "astra", 100.0, 60)
        p = Path(self.state, "seats", "attack-tokens", "astra.json")
        os.chmod(p, 0o644)
        self.assertFalse(verify.check_token(self.state, "astra", 150.0))
        os.chmod(p, 0o600)
        p.write_text(json.dumps({"seat": "gem", "token": "0" * 32, "expires": 1000.0}))
        self.assertFalse(verify.check_token(self.state, "astra", 150.0))
        p.write_text("{")
        self.assertFalse(verify.check_token(self.state, "astra", 150.0))

    def test_concurrent_tokens_and_failures_both_kept(self):  # R1
        barrier = threading.Barrier(2)

        def work(seat):
            barrier.wait()
            for i in range(20):
                verify.issue_token(self.state, seat, 100.0, 60)
                verify.record_failure(self.state, seat, {"k": seat}, f"probe-{i}", 100.0 + i)

        threads = [threading.Thread(target=work, args=(s,)) for s in ("astra", "gem")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertTrue(verify.check_token(self.state, "astra", 150.0))
        self.assertTrue(verify.check_token(self.state, "gem", 150.0))
        data = json.loads(Path(self.state, "seats", "failures.json").read_text())
        self.assertEqual(sorted(data), ["astra", "gem"])


class AttackRun:
    """`<cli> --version` answers with the current version; the attack script returns `out`.
    `after` is the version from the script call on (a tool that updated during the attack)."""

    def __init__(self, out, rc=0, version="codex-cli 9.9", after=None):
        self.out, self.rc, self.version, self.after = out, rc, version, after
        self.argv = self.timeout = self.token_seen = None
        self.calls = 0

    def __call__(self, argv, timeout, **kw):
        if len(argv) == 2 and argv[1] == "--version":
            return Result(0, self.version + "\n", "", False)
        self.calls += 1
        self.argv, self.timeout = argv, timeout
        run_dir = argv[argv.index("--run") + 1]
        self.token_seen = verify.check_token(os.path.join(run_dir, "state"), argv[argv.index("--seat") + 1],
                                             time.time())
        if self.after:
            self.version = self.after
        return Result(self.rc, self.out, "", False)


class SeatAttack(unittest.TestCase):
    setUp = SeatVerify.setUp
    tearDown = SeatVerify.tearDown

    def test_verify_records_on_pass(self):
        fake = AttackRun("PASS — evidence in /ev\n")
        ok, msg = verify.verify_seat(self.config, self.state, "astra", run=fake)
        self.assertTrue(ok, msg)
        self.assertIn("bw: recorded a seat pass", msg)
        self.assertTrue(fake.token_seen)  # the script ran with a live token in the isolated state
        self.assertTrue(verify.is_verified(self.config, self.state, "astra", run=fake))
        run_dir = fake.argv[fake.argv.index("--run") + 1]
        root = os.path.realpath(os.path.join(os.environ["HOME"], ".cache", "bw-seat-attack"))
        self.assertTrue(run_dir.startswith(os.path.join(root, "run.")), run_dir)
        self.assertFalse(os.path.exists(os.path.join(run_dir, "state", "seats", "attack-tokens", "astra.json")))
        self.assertFalse(os.path.exists(os.path.join(self.state, "seats", "attack-tokens", "astra.json")))
        self.assertEqual(fake.argv[0], os.path.join(verify.ROOT, "tests", "attack", "seat_readonly.sh"))
        self.assertEqual(fake.argv[fake.argv.index("--bw") + 1], os.path.join(verify.ROOT, "bin", "bw"))
        self.assertFalse(any("token" in a for a in fake.argv))  # R23
        self.assertTrue(json.loads(Path(self.state, "seats.json").read_text())["astra"]["verified"])

    def test_lead_homes_on_argv(self):
        home = os.environ["HOME"]
        for d in (".claude", ".codex"):
            os.makedirs(os.path.join(home, d))
        fake = AttackRun("PASS — evidence in /ev\n")
        verify.verify_seat(self.config, self.state, "astra", run=fake)
        homes = [fake.argv[i + 1] for i, a in enumerate(fake.argv) if a == "--lead-home"]
        self.assertEqual(homes, [os.path.realpath(os.path.join(home, d)) for d in (".claude", ".codex")])

    def test_key_change_during_attack_not_recorded(self):
        fake = AttackRun("PASS — evidence in /ev\n", after="codex-cli 10.0")
        ok, msg = verify.verify_seat(self.config, self.state, "astra", run=fake)
        self.assertFalse(ok)
        self.assertIn("key changed", msg)
        self.assertFalse(verify.is_verified(self.config, self.state, "astra", run=fake))

    def test_failure_recorded(self):
        fake = AttackRun("FAIL: probe-skipped:fresh:g1 — evidence in /ev\n", rc=1)
        ok, msg = verify.verify_seat(self.config, self.state, "astra", run=fake, now=lambda: 50.0)
        self.assertFalse(ok)
        self.assertIn("bw: not recorded", msg)
        out = json.loads(Path(self.state, "seats.json").read_text())
        self.assertEqual((out["astra"]["verified"], out["astra"]["failed_probe"]), (False, "probe-skipped:fresh:g1"))
        run_dir = fake.argv[fake.argv.index("--run") + 1]
        self.assertFalse(os.path.exists(os.path.join(run_dir, "state", "seats", "attack-tokens", "astra.json")))

    def test_timeout_passed(self):  # controller ruling, fix round 1: the script's worst case
        fake = AttackRun("PASS — evidence in /ev\n")
        verify.verify_seat(self.config, self.state, "astra", run=fake)
        self.assertEqual(verify.ATTACK_RUNS, 6)
        self.assertEqual(fake.timeout, 6 * 12 * 60 + 300)
        self.config["panel"]["cap_min"] = 5
        self.assertEqual(verify.attack_timeout(self.config), 6 * 5 * 60 + 300)
        sh = (Path(verify.SCRIPT)).read_text()
        self.assertIn("ATTACK_RUNS", sh)  # the script's header names the shared constant

    def test_sweep_after_a_killed_script(self):  # fix 2: the trap never ran
        home = os.environ["HOME"]
        planted = []

        def killed(argv, timeout, **kw):
            if len(argv) == 2:
                return Result(0, "codex-cli 9.9\n", "", False)
            run_dir = argv[argv.index("--run") + 1]
            tag = verify.run_tag(run_dir)
            os.makedirs(os.path.join(home, ".ssh"))
            Path(run_dir, verify.MADE_SSH).write_text("")
            scratch = os.path.join(self.tmp.name, f"zz-attack-{tag}-ab12")
            os.makedirs(scratch)
            paths = [os.path.join(home, ".ssh", f"bw-seat-attack-{tag}"), os.path.join(home, f"bw-seat-probe-{tag}"),
                     os.path.join(scratch, "out.md"), os.path.join(run_dir, "state", "planted.txt")]
            for p in paths:
                Path(p).write_text("n")
            keep = os.path.join(home, "unrelated-file")
            Path(keep).write_text("k")
            Path(run_dir, verify.PLANTED_LIST).write_text("\n".join([scratch] + paths + [keep]) + "\n")
            planted.extend([scratch] + paths)
            return Result(124, "", "", True)  # proc.run killed the script on the cap

        ok, msg = verify.verify_seat(self.config, self.state, "astra", run=killed)
        self.assertFalse(ok)
        self.assertIn("timed out", msg)
        for p in planted:
            self.assertFalse(os.path.lexists(p), p)
        self.assertFalse(os.path.exists(os.path.join(home, ".ssh")))  # the script made it, and it is empty
        self.assertTrue(os.path.exists(os.path.join(home, "unrelated-file")))  # no tag in the name: kept

    def test_sweep_keeps_ssh_that_was_there_or_is_not_empty(self):
        home = os.environ["HOME"]
        run_dir = os.path.join(self.tmp.name, "run.tag123")
        os.makedirs(os.path.join(home, ".ssh"))
        Path(home, ".ssh", "id_test").write_text("k")
        os.makedirs(run_dir)
        Path(run_dir, verify.MADE_SSH).write_text("")
        verify.sweep(run_dir)
        self.assertTrue(os.path.exists(os.path.join(home, ".ssh", "id_test")))
        os.remove(os.path.join(home, ".ssh", "id_test"))
        os.remove(os.path.join(run_dir, verify.MADE_SSH))
        verify.sweep(run_dir)
        self.assertTrue(os.path.isdir(os.path.join(home, ".ssh")))  # not made by the script: kept

    def test_old_run_dirs_pruned(self):  # fix 7
        root = os.path.join(os.environ["HOME"], ".cache", "bw-seat-attack")
        old, new = os.path.join(root, "run.old111"), os.path.join(root, "run.new222")
        for d in (old, new):
            os.makedirs(d)
        os.utime(old, (time.time() - 8 * 86400,) * 2)
        verify.verify_seat(self.config, self.state, "astra", run=AttackRun("PASS — evidence in /ev\n"))
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(new))

    def test_token_cleared_when_attack_raises(self):
        def boom(argv, timeout, **kw):
            if len(argv) == 2:
                return Result(0, "codex-cli 9.9\n", "", False)
            boom.run_dir = argv[argv.index("--run") + 1]
            raise OSError("cannot start")
        with self.assertRaises(OSError):
            verify.verify_seat(self.config, self.state, "astra", run=boom)
        self.assertFalse(os.path.exists(os.path.join(boom.run_dir, "state", "seats", "attack-tokens", "astra.json")))

    def test_reverify_states(self):
        fake = AttackRun("PASS — evidence in /ev\n")
        now = time.time()

        def states(**kw):
            items = verify.reverify_seats(self.config, self.state, "codex", now, run=fake, **kw)
            self.assertEqual([i.kind for i in items], ["seat astra"])
            return items[0].state, items[0].reason

        self.assertEqual(states(allow_run=False), ("pending", "--no-verify"))
        self.assertEqual(states(version_ok=False), ("pending", "version unreadable"))
        from lead import quota
        quota.record_cap(self.state, "chatgpt-plan", now, resets_at=now + 3600)
        state, reason = states()
        self.assertEqual(state, "pending")
        self.assertTrue(reason.startswith("plan chatgpt-plan capped until "), reason)
        os.remove(os.path.join(self.state, "caps", "chatgpt-plan.json"))
        self.assertEqual(fake.calls, 0)
        self.assertEqual(states()[0], "passed")
        self.assertEqual(states(), ("verified", "key matches the recorded pass"))
        self.assertEqual(fake.calls, 1)
        self.assertEqual(verify.reverify_seats(self.config, self.state, "kimi", now, run=fake), [])  # kimi disabled

    def test_reverify_version_unreadable_from_key(self):
        def no_cli(argv, timeout, **kw):
            return Result(127, "", "not found", False)
        items = verify.reverify_seats(self.config, self.state, "codex", time.time(), run=no_cli)
        self.assertEqual([(i.state, i.reason) for i in items], [("pending", "version unreadable")])

    def test_reverify_writes_seats_json_once(self):
        fake = AttackRun("PASS — evidence in /ev\n")
        self.config["seats"]["astra2"]["tier"] = "reserved"  # a second codex seat in seat_items
        with mock.patch("seats.verify.write_seats_json", wraps=verify.write_seats_json) as w:
            items = verify.reverify_seats(self.config, self.state, "codex", time.time(), run=fake)
        self.assertEqual([i.state for i in items], ["passed", "passed"])
        self.assertEqual(w.call_count, 1)

    def test_reverify_holds_runtime_lock(self):
        from lead.locks import RuntimeLock
        seen = []

        def script(argv, timeout, **kw):
            if len(argv) == 2:
                return Result(0, "codex-cli 9.9\n", "", False)
            lock = RuntimeLock(self.state, "codex")
            try:
                seen.append(lock.try_exclusive())
            finally:
                lock.release()
            return Result(0, "PASS — evidence in /ev\n", "", False)
        verify.reverify_seats(self.config, self.state, "codex", time.time(), run=script)
        self.assertEqual(seen, [False])


if __name__ == "__main__":
    unittest.main()
