import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
from lead import quota  # noqa: E402
from lead.rotation import decide  # noqa: E402

FX = HERE / "fixtures" / "statusline"
NOW = 1_899_990_000.0
LEADS2 = {"a": {"plan": "pa"}, "b": {"plan": "pb"}}
PROFILE2 = {"lead_order": ["a", "b"]}


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


M = 60.0
PROBE_MIN = 15


def plan_cfg(q, orca_provider=None):
    return {"quota": q, "orca_provider": orca_provider, "orca_windows": {}}


def known(used, observed, resets_in=3 * 3600, name="session"):
    return {"windows": [{"name": name, "used_pct": used, "resets_at": NOW + resets_in}], "observed_at": observed}


def pool(used, applies_to, resets_in=3600, name="fableWeekly"):
    return {"name": name, "used_pct": used, "resets_at": NOW + resets_in, "applies_to": applies_to}


def arb(sources, cfg):
    return quota.arbitrate(sources, cfg, NOW, PROBE_MIN)


class Arbiter(unittest.TestCase):
    def test_statusline_beats_orca_and_orca_rides_as_cross_check(self):
        sl, orca = known(10, NOW - 60, name="five_hour"), known(90, NOW - 60)
        a = arb({"statusline": sl, "orca": orca}, plan_cfg("statusline", "claude"))
        self.assertEqual((a["source"], a["windows"]), ("statusline", sl["windows"]))
        self.assertEqual(a["cross_check"], orca)
        self.assertEqual(a["age_s"], 60)

    def test_statusline_plan_never_takes_orca_windows_even_when_statusline_unknown(self):
        a = arb({"orca": known(90, NOW - 60)}, plan_cfg("statusline", "claude"))
        self.assertTrue(a["unknown"])
        self.assertIsNone(a["source"])
        self.assertIsNotNone(a["cross_check"])

    def test_probe_beats_orca_on_a_probe_plan(self):
        a = arb({"probe": known(10, NOW - 60), "orca": known(90, NOW - 30)}, plan_cfg("probe", "codex"))
        self.assertEqual(a["source"], "probe")
        self.assertIsNone(a["cross_check"])

    def test_stale_probe_falls_to_orca(self):
        a = arb({"probe": known(10, NOW - 31 * M), "orca": known(90, NOW - 60)}, plan_cfg("probe", "codex"))
        self.assertEqual((a["source"], a["windows"][0]["used_pct"]), ("orca", 90))

    def test_probe_plan_without_orca_provider_ignores_orca(self):
        a = arb({"probe": known(10, NOW - 31 * M), "orca": known(90, NOW - 60)}, plan_cfg("probe"))
        self.assertTrue(a["unknown"])
        self.assertEqual(a["pools"], [])

    def test_stale_orca_never_replaces_fresh_probe(self):
        a = arb({"probe": known(10, NOW - 60), "orca": known(90, NOW - 31 * M)}, plan_cfg("probe", "codex"))
        self.assertEqual(a["source"], "probe")

    def test_both_unknown_names_both_reasons(self):
        a = arb({"probe": {"unknown": True, "reason": "HTTP 401"}, "orca": known(90, NOW - 31 * M)},
                plan_cfg("probe", "codex"))
        self.assertTrue(a["unknown"])
        self.assertIn("probe: HTTP 401", a["reason"])
        self.assertIn("orca: stale", a["reason"])

    def test_pools_from_orca_while_statusline_wins_windows(self):
        orca = dict(known(50, NOW - 60), pools=[pool(100, ["alpha"])])
        a = arb({"statusline": known(10, NOW - 60, name="five_hour"), "orca": orca}, plan_cfg("statusline", "claude"))
        self.assertEqual(a["source"], "statusline")
        self.assertEqual(a["pools"], [pool(100, ["alpha"])])

    def test_pools_dropped_when_orca_stale(self):
        orca = dict(known(50, NOW - 31 * M), pools=[pool(100, ["alpha"])])
        a = arb({"statusline": known(10, NOW - 60, name="five_hour"), "orca": orca}, plan_cfg("statusline", "claude"))
        self.assertEqual(a["pools"], [])

    def test_observed_in_the_future_is_unknown(self):
        a = arb({"orca": known(10, NOW + 10 * M)}, plan_cfg("orca", "codex"))
        self.assertTrue(a["unknown"])
        self.assertIn("orca: observed in the future", a["reason"])

    def test_missing_or_malformed_windows_are_unknown_without_raising(self):
        for snap in ({"observed_at": NOW - 60}, {"windows": [], "observed_at": NOW - 60},
                     {"windows": [{"name": "session"}], "observed_at": NOW - 60},
                     {"windows": ["x"], "observed_at": NOW - 60},
                     {"windows": [{"name": "session", "used_pct": True}], "observed_at": NOW - 60},
                     {"windows": [{"name": "session", "used_pct": 5}]},
                     {"windows": [{"name": "session", "used_pct": 5}], "observed_at": True}, [], "x"):
            a = arb({"orca": snap}, plan_cfg("orca", "codex"))
            self.assertTrue(a["unknown"], snap)

    def test_orca_pools_malformed_are_dropped(self):
        a = arb({"orca": dict(known(10, NOW - 60), pools="nope")}, plan_cfg("orca", "codex"))
        self.assertEqual(a["pools"], [])

    def test_none_plan_is_unknown_with_its_reason(self):
        a = arb({}, plan_cfg("none"))
        self.assertEqual((a["unknown"], a["reason"], a["source"]), (True, "quota: none", None))

    def test_last_known_at_from_unknown_and_known_sources(self):
        a = arb({"probe": {"unknown": True, "reason": "timeout", "last_known_at": NOW - 99 * M},
                 "orca": known(10, NOW - 40 * M)}, plan_cfg("probe", "codex"))
        self.assertEqual(a["last_known_at"], NOW - 40 * M)


class Snapshots(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_file_names(self):
        quota.write_snapshot(self.state, "pa", "statusline", known(1, NOW), NOW)
        quota.write_snapshot(self.state, "pa", "orca", known(2, NOW), NOW)
        quota.write_snapshot(self.state, "pa", "probe", known(3, NOW), NOW)
        self.assertEqual(sorted(os.listdir(os.path.join(self.state, "quota"))),
                         ["pa.json", "pa.orca.json", "pa.probe.json"])
        s = quota.read_sources(self.state)["pa"]
        self.assertEqual({k: v["windows"][0]["used_pct"] for k, v in s.items()},
                         {"statusline": 1, "orca": 2, "probe": 3})

    def test_unknown_source_rejected(self):
        with self.assertRaises(ValueError):
            quota.write_snapshot(self.state, "pa", "elsewhere", known(1, NOW), NOW)

    def test_last_known_at_carried_through_two_unknown_writes(self):
        quota.write_snapshot(self.state, "pa", "orca", known(2, NOW - 100), NOW - 100)
        quota.write_snapshot(self.state, "pa", "orca", {"unknown": True, "reason": "x"}, NOW - 50)
        quota.write_snapshot(self.state, "pa", "orca", {"unknown": True, "reason": "y"}, NOW)
        s = quota.read_sources(self.state)["pa"]["orca"]
        self.assertEqual((s["reason"], s["last_known_at"]), ("y", NOW - 100))

    def test_future_reading_is_never_last_known(self):
        quota.write_snapshot(self.state, "pa", "orca", known(2, NOW + 3600), NOW)
        quota.write_snapshot(self.state, "pa", "orca", {"unknown": True, "reason": "x"}, NOW)
        self.assertIsNone(quota.read_sources(self.state)["pa"]["orca"]["last_known_at"])
        a = arb({"orca": known(2, NOW + 3600)}, plan_cfg("orca", "codex"))
        self.assertIsNone(a["last_known_at"])

    def test_first_unknown_has_no_last_known(self):
        quota.write_snapshot(self.state, "pa", "probe", {"unknown": True, "reason": "x"}, NOW)
        self.assertIsNone(quota.read_sources(self.state)["pa"]["probe"]["last_known_at"])

    def test_statusline_unknown_carries_last_known(self):
        quota.record_statusline(self.state, "pa", fx("with_limits.json"), NOW)
        quota.record_statusline(self.state, "pa", fx("no_limits.json"), NOW + 5)
        quota.record_statusline(self.state, "pa", fx("no_limits.json"), NOW + 9)
        self.assertEqual(quota.read_sources(self.state)["pa"]["statusline"]["last_known_at"], NOW)

    def test_legacy_read_quota_ignores_dotted_names(self):
        quota.write_snapshot(self.state, "chatgpt", "orca", known(2, NOW), NOW)
        quota.write_snapshot(self.state, "pa", "statusline", known(1, NOW), NOW)
        self.assertEqual(sorted(quota.read_quota(self.state)), ["pa"])

    def test_read_quota_with_config_arbitrates_every_plan(self):
        quota.write_snapshot(self.state, "chatgpt", "orca", known(20, NOW - 60), NOW)
        config = {"plans": {"chatgpt": plan_cfg("orca", "codex"), "other": plan_cfg("probe")},
                  "rotation": {"probe_every_min": PROBE_MIN}}
        q = quota.read_quota(self.state, config, NOW)
        self.assertEqual(q["chatgpt"]["source"], "orca")
        self.assertTrue(q["other"]["unknown"])


class Notice(unittest.TestCase):
    def test_after_one_interval_not_before(self):
        cfg = plan_cfg("probe")
        before = {"unknown": True, "last_known_at": NOW - PROBE_MIN * M + 1}
        after = {"unknown": True, "last_known_at": NOW - PROBE_MIN * M - 1}
        self.assertIsNone(quota.notice(before, cfg, NOW, PROBE_MIN))
        n = quota.notice(after, cfg, NOW, PROBE_MIN)
        self.assertTrue(n.startswith("no quota reading since "), n)
        self.assertTrue(n.endswith("; rotation is using observed caps only"), n)

    def test_yet_when_never_known(self):
        self.assertEqual(quota.notice({"unknown": True, "last_known_at": None}, plan_cfg("orca", "x"), NOW, PROBE_MIN),
                         "no quota reading yet; rotation is using observed caps only")

    def test_none_plan_and_known_reading_have_no_notice(self):
        self.assertIsNone(quota.notice({"unknown": True, "last_known_at": None}, plan_cfg("none"), NOW, PROBE_MIN))
        self.assertIsNone(quota.notice(dict(known(1, NOW - 99 * M), source="orca"), plan_cfg("orca", "x"), NOW, PROBE_MIN))


class Summary(unittest.TestCase):
    def test_unknown_with_reason(self):
        self.assertEqual(quota.summary({"unknown": True, "reason": "x"}), "quota unknown — x")
        self.assertEqual(quota.summary(None), "quota unknown")

    def test_names_as_is_with_source_and_age(self):
        s = dict(known(30, NOW - 300), source="orca", age_s=300)
        s["windows"].append({"name": "weekly", "used_pct": 60, "resets_at": None})
        self.assertEqual(quota.summary(s), "session 70% · weekly 40% · orca 5m")


class ExpiredCaps(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = self.tmp.name
        self.config = {"plans": {"pa": plan_cfg("orca", "codex"), "pb": plan_cfg("probe")},
                       "rotation": {"probe_every_min": PROBE_MIN, "cap_expiry_hours": 5, "return_above_pct": 50}}

    def tearDown(self):
        self.tmp.cleanup()

    def caps(self):
        return quota.read_caps(self.state, NOW, 5, include_expired=True)

    def test_expired_cap_cleared_on_fresh_known_reading(self):
        quota.record_cap(self.state, "pa", NOW - 3600, resets_at=NOW - 600)
        quota.write_snapshot(self.state, "pa", "orca", known(80, NOW - 60), NOW - 60)
        cleared = quota.clear_expired_caps(self.state, quota.read_quota(self.state, self.config, NOW), NOW, 5)
        self.assertEqual(cleared, ["pa"])
        self.assertNotIn("pa", self.caps())

    def test_reading_from_before_the_cap_reset_does_not_clear_it(self):
        quota.record_cap(self.state, "pa", NOW - 3600, resets_at=NOW - 120)
        quota.write_snapshot(self.state, "pa", "orca", known(80, NOW - 600), NOW - 600)
        self.assertEqual(quota.clear_expired_caps(self.state, quota.read_quota(self.state, self.config, NOW), NOW, 5), [])
        self.assertIn("pa", self.caps())

    def test_expired_cap_kept_without_reading_until_expiry_window(self):
        quota.record_cap(self.state, "pb", NOW - 3600, resets_at=NOW - 600)
        arb = quota.read_quota(self.state, self.config, NOW)
        self.assertEqual(quota.clear_expired_caps(self.state, arb, NOW, 5), [])
        self.assertIn("pb", self.caps())
        later = NOW - 600 + 5 * 3600 + 1
        self.assertEqual(quota.clear_expired_caps(self.state, arb, later, 5), ["pb"])
        self.assertNotIn("pb", self.caps())

    def test_unexpired_cap_never_cleared_by_reading(self):
        quota.record_cap(self.state, "pa", NOW - 3600, resets_at=NOW + 600)
        quota.write_snapshot(self.state, "pa", "orca", known(5, NOW - 60), NOW - 60)
        self.assertEqual(quota.clear_expired_caps(self.state, quota.read_quota(self.state, self.config, NOW), NOW, 5), [])
        self.assertIn("pa", self.caps())

    def test_cap_renewed_concurrently_is_kept(self):
        quota.record_cap(self.state, "pa", NOW - 3600, resets_at=NOW - 600)
        quota.write_snapshot(self.state, "pa", "orca", known(80, NOW - 60), NOW - 60)
        arb = quota.read_quota(self.state, self.config, NOW)
        quota.record_cap(self.state, "pa", NOW, resets_at=NOW + 3600)  # renewed after the read
        self.assertEqual(quota.clear_expired_caps(self.state, arb, NOW, 5), [])
        self.assertIn("pa", self.caps())

    def test_full_pool_never_creates_cap(self):
        quota.write_snapshot(self.state, "pa", "orca", dict(known(100, NOW - 60), pools=[pool(100, ["a"])]), NOW - 60)
        arb = quota.read_quota(self.state, self.config, NOW)
        quota.clear_expired_caps(self.state, arb, NOW, 5)
        decide(PROFILE2, LEADS2, self.config["rotation"], "a", None, arb, self.caps(), NOW, "running")
        decide(PROFILE2, LEADS2, self.config["rotation"], None, None, arb, self.caps(), NOW, "start")
        caps_dir = os.path.join(self.state, "caps")
        self.assertFalse(os.path.exists(caps_dir))

    def test_end_to_end_capped_then_recovered_orca_plan(self):
        rot = self.config["rotation"]
        quota.record_cap(self.state, "pa", NOW - 3600, resets_at=NOW + 600, source="lead-hook")
        quota.write_snapshot(self.state, "pa", "orca", known(100, NOW - 60), NOW - 60)
        arb = quota.read_quota(self.state, self.config, NOW)
        quota.clear_expired_caps(self.state, arb, NOW, 5)
        d = decide(PROFILE2, LEADS2, rot, None, None, arb, self.caps(), NOW, "start")
        self.assertEqual((d.lead, d.reason), ("b", "capped"))
        later = NOW + 1200  # past the cap's reset; Orca has read the plan again since, 30% used
        quota.write_snapshot(self.state, "pa", "orca",
                             {"windows": [{"name": "session", "used_pct": 30, "resets_at": later + 5 * 3600}],
                              "observed_at": later - 60}, later - 60)
        arb = quota.read_quota(self.state, self.config, later)
        self.assertEqual(quota.clear_expired_caps(self.state, arb, later, 5), ["pa"])
        caps = quota.read_caps(self.state, later, 5, include_expired=True)
        d = decide(PROFILE2, LEADS2, rot, None, None, arb, caps, later, "start")
        self.assertEqual((d.lead, d.reason), ("a", "order"))


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
