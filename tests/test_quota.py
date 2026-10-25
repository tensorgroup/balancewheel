import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from lead import quota  # noqa: E402

FX = HERE / "fixtures" / "statusline"
NOW = 1_899_990_000.0


def fx(name):
    return json.loads((FX / name).read_text())


class Quota(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_with_limits_and_model(self):
        snapshot, model = quota.parse_statusline(fx("with_limits.json"))
        self.assertEqual(model, "vendor-model-a-1")
        names = {w["name"]: w for w in snapshot["windows"]}
        self.assertEqual(names["five_hour"]["used_pct"], 23.5)
        self.assertEqual(names["seven_day"]["resets_at"], 1900500000)

    def test_absent_limits_parse_to_none(self):
        snapshot, model = quota.parse_statusline(fx("no_limits.json"))
        self.assertIsNone(snapshot)
        self.assertEqual(model, "vendor-model-a-1")

    def test_absent_limits_replace_earlier_snapshot_with_unknown(self):
        quota.record_statusline(self.state, "pa", fx("with_limits.json"), NOW)
        quota.record_statusline(self.state, "pa", fx("no_limits.json"), NOW + 5)
        self.assertTrue(quota.read_quota(self.state)["pa"]["unknown"])

    def test_full_window_records_cap_with_its_reset(self):
        quota.record_statusline(self.state, "pa", fx("limits_100.json"), NOW)
        caps = quota.read_caps(self.state, NOW, 5)
        self.assertEqual(caps["pa"]["resets_at"], 1900003600)
        self.assertEqual(caps["pa"]["window"], "five_hour")

    def test_both_windows_full_records_the_later_reset(self):
        payload = {"rate_limits": {"five_hour": {"used_percentage": 100, "resets_at": 1900003600},
                                   "seven_day": {"used_percentage": 100, "resets_at": 1900500000}}}
        quota.record_statusline(self.state, "pa", payload, NOW)
        self.assertEqual(quota.read_caps(self.state, NOW, 5)["pa"]["resets_at"], 1900500000)

    def test_expired_caps_still_readable_for_recovery_checks(self):
        quota.record_cap(self.state, "pb", NOW, resets_at=NOW + 10)
        self.assertIn("pb", quota.read_caps(self.state, NOW + 11, 5, include_expired=True))

    def test_non_numeric_resets_at_ignored(self):
        payload = {"rate_limits": {"five_hour": {"used_percentage": 100, "resets_at": "soon"}}}
        quota.record_statusline(self.state, "pa", payload, NOW)
        self.assertEqual(quota.read_caps(self.state, NOW, 5)["pa"]["resets_at"], NOW + 5 * 3600)

    def test_cap_cleared_after_reset_when_below_full(self):
        quota.record_statusline(self.state, "pa", fx("limits_100.json"), NOW)
        later = fx("with_limits.json")
        quota.record_statusline(self.state, "pa", later, 1900003700.0)
        self.assertNotIn("pa", quota.read_caps(self.state, 1900003700.0, 5))

    def test_cap_kept_if_reading_below_full_but_before_reset(self):
        quota.record_statusline(self.state, "pa", fx("limits_100.json"), NOW)
        quota.record_statusline(self.state, "pa", fx("with_limits.json"), NOW + 10)
        self.assertIn("pa", quota.read_caps(self.state, NOW + 10, 5))

    def test_expired_cap_dropped(self):
        quota.record_cap(self.state, "pb", NOW, resets_at=NOW + 10)
        self.assertIn("pb", quota.read_caps(self.state, NOW, 5))
        self.assertNotIn("pb", quota.read_caps(self.state, NOW + 11, 5))

    def test_manual_cap_without_reset_uses_expiry(self):
        quota.record_cap(self.state, "pb", NOW)
        self.assertEqual(quota.read_caps(self.state, NOW, 5)["pb"]["resets_at"], NOW + 5 * 3600)

    def test_model_recorded(self):
        quota.record_statusline(self.state, "pa", fx("model_only.json"), NOW)
        self.assertEqual(quota.read_model(self.state, "pa"), "vendor-model-b-2")

    def test_concurrent_plans_both_survive(self):
        procs = []
        for plan in ("pa", "pb"):
            script = ("import sys, json; sys.path.insert(0, %r); from lead import quota\n"
                      "for i in range(50): quota.record_statusline(%r, %r, json.load(open(%r)), %r + i)\n"
                      % (str(ROOT), self.state, plan, str(FX / "with_limits.json"), NOW))
            procs.append(subprocess.Popen([sys.executable, "-c", script]))
        for p in procs:
            self.assertEqual(p.wait(), 0)
        q = quota.read_quota(self.state)
        self.assertIn("pa", q)
        self.assertIn("pb", q)


class StatuslineScript(unittest.TestCase):
    def run_script(self, payload_file, extra_env=None, cwd=None):
        with tempfile.TemporaryDirectory() as bindir, tempfile.TemporaryDirectory() as state:
            link = Path(bindir) / "bw-statusline"
            link.symlink_to(ROOT / "bin" / "bw-statusline")
            env = {**os.environ, "BW_STATE_DIR": state, **(extra_env or {})}
            env.pop("PYTHONPATH", None)
            with open(payload_file, "rb") as f:
                r = subprocess.run([str(link), "pa"], stdin=f, capture_output=True, env=env, cwd=cwd or bindir)
            return r, state

    def test_runs_from_unrelated_dir_through_symlink(self):
        r, _ = self.run_script(FX / "with_limits.json")
        self.assertEqual(r.returncode, 0, r.stderr.decode())
        self.assertIn("5h 76% · 7d 59%", r.stdout.decode())

    def test_project_module_cannot_replace_recorder(self):
        with tempfile.TemporaryDirectory() as proj:
            (Path(proj) / "lead").mkdir()
            (Path(proj) / "lead" / "__init__.py").write_text("")
            (Path(proj) / "lead" / "quota.py").write_text("print('HIJACKED')")
            r, _ = self.run_script(FX / "with_limits.json", cwd=proj)
            self.assertNotIn("HIJACKED", r.stdout.decode())
            self.assertIn("5h 76%", r.stdout.decode())

    def test_unknown_summary(self):
        r, _ = self.run_script(FX / "no_limits.json")
        self.assertIn("quota unknown", r.stdout.decode())

    def test_chain_gets_identical_stdin(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "seen"
            chain = f"cat > {out}; echo chained-output"
            r, _ = self.run_script(FX / "with_limits.json", {"BW_STATUSLINE_CHAIN": chain})
            self.assertEqual(r.returncode, 0, r.stderr.decode())
            self.assertEqual(r.stdout.decode().strip(), "chained-output")
            self.assertEqual(out.read_bytes(), (FX / "with_limits.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
