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


if __name__ == "__main__":
    unittest.main()
